import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from transfer_search import search_fastest_one_transfer


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "rts_gtfs.sqlite"
DEFAULTS_PATH = BASE_DIR / "db" / "answering_defaults.json"


@dataclass
class StopCandidate:
    stop_id_padded: str
    stop_name: str


def normalize_text(text):
    if text is None:
        return ""
    cleaned = []
    for ch in text.lower().strip():
        if ch.isalnum() or ch.isspace():
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def load_defaults():
    if not DEFAULTS_PATH.exists():
        return []
    with DEFAULTS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("default_stops", [])


def parse_route(text):
    m = re.search(r"\broute\s+(\d+)\b", text, re.IGNORECASE)
    return m.group(1) if m else None


def parse_date(text):
    text = text.lower()
    today = date.today()

    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    mdY = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if mdY:
        return date(int(mdY.group(3)), int(mdY.group(1)), int(mdY.group(2)))

    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, idx in weekdays.items():
        if name in text:
            days_ahead = (idx - today.weekday()) % 7
            if days_ahead == 0:
                return today
            return today + timedelta(days=days_ahead)

    if "today" in text:
        return today
    if "tomorrow" in text:
        return today + timedelta(days=1)
    return today


def parse_time(text):
    text = text.lower()
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}:00"


def extract_from_to(text):
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:\s+on|\s+at|\s+around|\?|$)", text, re.IGNORECASE)
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def find_stop_by_alias(text, defaults):
    norm = normalize_text(text)
    for d in defaults:
        if d.get("alias") and d["alias"] in norm:
            return StopCandidate(d["stop_id_padded"], d["stop_name"])
    return None


def find_stops_like(conn, name_like, route_short_name=None):
    params = {"like": f"%{name_like}%"}
    if route_short_name:
        sql = """
        SELECT DISTINCT s.stop_id_padded, s.stop_name
        FROM stops s
        JOIN stop_times st ON st.stop_id = s.stop_id
        JOIN trips t ON t.trip_id = st.trip_id
        JOIN routes r ON r.route_id = t.route_id
        WHERE r.route_short_name = :route
          AND s.stop_name LIKE :like
        ORDER BY s.stop_name;
        """
        params["route"] = route_short_name
    else:
        sql = """
        SELECT stop_id_padded, stop_name
        FROM stops
        WHERE stop_name LIKE :like
        ORDER BY stop_name;
        """
    rows = conn.execute(sql, params).fetchall()
    return [StopCandidate(r["stop_id_padded"], r["stop_name"]) for r in rows]


def find_stops_fuzzy(conn, name_text, route_short_name=None):
    norm = normalize_text(name_text)
    if not norm:
        return []
    pattern = "%" + "%".join(norm.split()) + "%"
    params = {"pattern": pattern}
    if route_short_name:
        sql = """
        SELECT DISTINCT s.stop_id_padded, s.stop_name
        FROM fuzzy_lookup f
        JOIN stops s ON s.stop_id = f.entity_id
        JOIN stop_times st ON st.stop_id = s.stop_id
        JOIN trips t ON t.trip_id = st.trip_id
        JOIN routes r ON r.route_id = t.route_id
        WHERE f.entity_type = 'stop'
          AND f.normalized LIKE :pattern
          AND r.route_short_name = :route
        ORDER BY s.stop_name;
        """
        params["route"] = route_short_name
    else:
        sql = """
        SELECT DISTINCT s.stop_id_padded, s.stop_name
        FROM fuzzy_lookup f
        JOIN stops s ON s.stop_id = f.entity_id
        WHERE f.entity_type = 'stop'
          AND f.normalized LIKE :pattern
        ORDER BY s.stop_name;
        """
    rows = conn.execute(sql, params).fetchall()
    return [StopCandidate(r["stop_id_padded"], r["stop_name"]) for r in rows]


def next_departures_per_headsign(conn, route_short_name, stop_id_padded, date_str, time_str):
    sql = """
    WITH base_services AS (
      SELECT c.service_id
      FROM calendar c
      WHERE :date BETWEEN c.start_date AND c.end_date
        AND (
          (c.monday = 1 AND strftime('%w', :date) = '1') OR
          (c.tuesday = 1 AND strftime('%w', :date) = '2') OR
          (c.wednesday = 1 AND strftime('%w', :date) = '3') OR
          (c.thursday = 1 AND strftime('%w', :date) = '4') OR
          (c.friday = 1 AND strftime('%w', :date) = '5') OR
          (c.saturday = 1 AND strftime('%w', :date) = '6') OR
          (c.sunday = 1 AND strftime('%w', :date) = '0')
        )
    ),
    exception_add AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date AND exception_type = 1
    ),
    exception_remove AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date AND exception_type = 2
    ),
    active_services AS (
      SELECT service_id FROM base_services
      UNION
      SELECT service_id FROM exception_add
      EXCEPT
      SELECT service_id FROM exception_remove
    ),
    ranked AS (
      SELECT st.departure_time, t.trip_headsign,
             ROW_NUMBER() OVER (PARTITION BY t.trip_headsign ORDER BY st.departure_time) AS rn
      FROM stops s
      JOIN stop_times st ON st.stop_id = s.stop_id
      JOIN trips t ON t.trip_id = st.trip_id
      JOIN routes r ON r.route_id = t.route_id
      JOIN active_services a ON a.service_id = t.service_id
      WHERE r.route_short_name = :route
        AND s.stop_id_padded = :stop_id
        AND st.departure_time >= :time
    )
    SELECT departure_time, trip_headsign
    FROM ranked
    WHERE rn = 1
    ORDER BY departure_time;
    """
    return conn.execute(
        sql,
        {"date": date_str, "route": route_short_name, "stop_id": stop_id_padded, "time": time_str},
    ).fetchall()


def first_or_last_departure(conn, route_short_name, stop_id_padded, date_str, first=True):
    sql = """
    WITH base_services AS (
      SELECT c.service_id
      FROM calendar c
      WHERE :date BETWEEN c.start_date AND c.end_date
        AND (
          (c.monday = 1 AND strftime('%w', :date) = '1') OR
          (c.tuesday = 1 AND strftime('%w', :date) = '2') OR
          (c.wednesday = 1 AND strftime('%w', :date) = '3') OR
          (c.thursday = 1 AND strftime('%w', :date) = '4') OR
          (c.friday = 1 AND strftime('%w', :date) = '5') OR
          (c.saturday = 1 AND strftime('%w', :date) = '6') OR
          (c.sunday = 1 AND strftime('%w', :date) = '0')
        )
    ),
    exception_add AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date AND exception_type = 1
    ),
    exception_remove AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date AND exception_type = 2
    ),
    active_services AS (
      SELECT service_id FROM base_services
      UNION
      SELECT service_id FROM exception_add
      EXCEPT
      SELECT service_id FROM exception_remove
    ),
    departures AS (
      SELECT st.departure_time
      FROM stops s
      JOIN stop_times st ON st.stop_id = s.stop_id
      JOIN trips t ON t.trip_id = st.trip_id
      JOIN routes r ON r.route_id = t.route_id
      JOIN active_services a ON a.service_id = t.service_id
      WHERE r.route_short_name = :route
        AND s.stop_id_padded = :stop_id
    )
    SELECT {agg}(departure_time) AS result
    FROM departures;
    """.format(
        agg="MIN" if first else "MAX"
    )
    row = conn.execute(
        sql, {"date": date_str, "route": route_short_name, "stop_id": stop_id_padded}
    ).fetchone()
    return row["result"] if row else None


def answer_question(question):
    defaults = load_defaults()
    route = parse_route(question)
    q_date = parse_date(question)
    q_time = parse_time(question)
    date_str = q_date.strftime("%Y-%m-%d")

    conn = connect_db()
    try:
        # Fastest way (transfer search)
        if "fastest" in question.lower() and "from" in question.lower() and "to" in question.lower():
            from_text, to_text = extract_from_to(question)
            if not from_text or not to_text:
                return "I need both origin and destination (from X to Y)."

            from_alias = find_stop_by_alias(from_text, defaults)
            to_alias = find_stop_by_alias(to_text, defaults)

            if not from_alias:
                candidates = find_stops_like(conn, from_text)
                if len(candidates) == 1:
                    from_alias = candidates[0]
                else:
                    names = ", ".join([c.stop_name for c in candidates[:5]])
                    return f"Multiple origin stops match '{from_text}': {names}."

            if not to_alias:
                candidates = find_stops_like(conn, to_text)
                if len(candidates) == 1:
                    to_alias = candidates[0]
                else:
                    names = ", ".join([c.stop_name for c in candidates[:5]])
                    return f"Multiple destination stops match '{to_text}': {names}."

            time_str = q_time or "00:00:00"
            result = search_fastest_one_transfer(
                date=date_str,
                time=time_str,
                from_stop_id_padded=from_alias.stop_id_padded,
                to_stop_id_padded=to_alias.stop_id_padded,
                limit=3,
            )
            return format_response(question, result)

        if not route:
            return "Please include a route number (e.g., 'route 5')."

        stop = find_stop_by_alias(question, defaults)
        if not stop:
            # try to extract a stop name after 'from' or 'leaving'
            m = re.search(r"(from|leaving)\s+(.+?)(?:\s+on|\s+at|\s+around|\?|$)", question, re.IGNORECASE)
            if m:
                stop_term = m.group(2).strip()
                candidates = find_stops_like(conn, stop_term, route)
            else:
                candidates = find_stops_like(conn, " ".join(question.split()[-2:]), route)

            if len(candidates) == 1:
                stop = candidates[0]
            elif len(candidates) > 1:
                names = ", ".join([c.stop_name for c in candidates[:5]])
                return f"Multiple stops on route {route} match: {names}."
            else:
                fuzzy = find_stops_fuzzy(conn, stop_term if m else question, route)
                if len(fuzzy) == 1:
                    stop = fuzzy[0]
                elif len(fuzzy) > 1:
                    names = ", ".join([c.stop_name for c in fuzzy[:5]])
                    return f"Multiple fuzzy matches on route {route}: {names}."
                else:
                    return "I couldn't find a matching stop on that route."

        # Last / First
        if "last" in question.lower():
            last_time = first_or_last_departure(conn, route, stop.stop_id_padded, date_str, first=False)
            return format_response(
                question,
                {
                "route": route,
                "stop": stop.stop_name,
                "date": date_str,
                "last_departure": last_time,
                },
            )
        if "first" in question.lower():
            first_time = first_or_last_departure(conn, route, stop.stop_id_padded, date_str, first=True)
            return format_response(
                question,
                {
                "route": route,
                "stop": stop.stop_name,
                "date": date_str,
                "first_departure": first_time,
                },
            )

        # Next / closest with time
        if q_time:
            rows = next_departures_per_headsign(conn, route, stop.stop_id_padded, date_str, q_time)
            return format_response(
                question,
                {
                "route": route,
                "stop": stop.stop_name,
                "date": date_str,
                "time": q_time,
                "next_by_direction": [(r["departure_time"], r["trip_headsign"]) for r in rows],
                },
            )

        return format_response(
            question,
            {"error": "Please include a time (e.g., 'around 7:30 am') or ask for first/last."},
        )
    finally:
        conn.close()


def main():
    q = input("Question: ").strip()
    print(answer_question(q))


def format_response(question, payload):
    if isinstance(payload, dict) and "error" in payload:
        return {"raw": payload, "response_text": payload["error"]}

    if "options" in payload and "from_name" in payload:
        lines = [f"Fastest options from {payload['from_name']} to {payload['to_name']}:"]
        if not payload["options"]:
            lines.append("No options found.")
        for i, it in enumerate(payload["options"], 1):
            lines.append(
                f"{i}) {it['first_route']} {it['first_headsign']} "
                f"{it['first_depart']} -> {it['transfer_stop_name']} {it['transfer_arrive']}; "
                f"{it['second_route']} {it['second_headsign']} {it['second_depart']} -> "
                f"{it['final_arrive']}"
            )
        return {"raw": payload, "response_text": "\\n".join(lines)}

    if "last_departure" in payload:
        text = (
            f"Last departure for route {payload['route']} from {payload['stop']} on "
            f"{payload['date']}: {payload['last_departure']}"
        )
        return {"raw": payload, "response_text": text}

    if "first_departure" in payload:
        text = (
            f"First departure for route {payload['route']} from {payload['stop']} on "
            f"{payload['date']}: {payload['first_departure']}"
        )
        return {"raw": payload, "response_text": text}

    if "next_by_direction" in payload:
        lines = [
            f"Next departures for route {payload['route']} from {payload['stop']} on "
            f"{payload['date']} after {payload['time']}:"
        ]
        for t, headsign in payload["next_by_direction"]:
            lines.append(f"- {t} ({headsign})")
        return {"raw": payload, "response_text": "\\n".join(lines)}

    return {"raw": payload, "response_text": str(payload)}


if __name__ == "__main__":
    main()
