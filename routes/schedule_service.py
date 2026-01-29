import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TZ = ZoneInfo("America/New_York")
BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "Backend Basics" / "db" / "rts_gtfs.sqlite"
DEFAULTS_PATH = BASE_DIR / "Backend Basics" / "db" / "answering_defaults.json"


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


def parse_date(text):
    text = (text or "").lower()
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
    text = (text or "").lower()
    if "noon" in text:
        return "12:00:00"
    if "midnight" in text:
        return "00:00:00"
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


def extract_stop_term(text):
    m = re.search(r"(from|leaving|at)\s+(.+?)(?:\s+on|\s+at|\s+around|\?|$)", text, re.IGNORECASE)
    if not m:
        return None
    cand = m.group(2).strip()
    if re.search(r"\d", cand) or re.search(r"\b(am|pm)\b", cand.lower()):
        return None
    return cand


def connect_db():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def find_stop_by_alias(text, defaults):
    norm = normalize_text(text)
    for d in defaults:
        if d.get("alias") and d["alias"] in norm:
            return {"stop_id_padded": d["stop_id_padded"], "stop_name": d["stop_name"]}
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
    return [{"stop_id_padded": r["stop_id_padded"], "stop_name": r["stop_name"]} for r in rows]


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
    return [{"stop_id_padded": r["stop_id_padded"], "stop_name": r["stop_name"]} for r in rows]


def resolve_stop(conn, route, text, stop_id=None, stop_name=None):
    if stop_id:
        row = conn.execute(
            "SELECT stop_id_padded, stop_name FROM stops WHERE stop_id_padded = ?",
            (stop_id,),
        ).fetchone()
        if row:
            return {"stop_id_padded": row["stop_id_padded"], "stop_name": row["stop_name"]}
        return None

    defaults = load_defaults()
    if stop_name:
        stop_term = stop_name
    else:
        stop_term = extract_stop_term(text) or text

    stop = find_stop_by_alias(stop_term, defaults)
    if stop:
        return stop

    candidates = find_stops_like(conn, stop_term, route)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return {"candidates": candidates[:5]}

    fuzzy = find_stops_fuzzy(conn, stop_term, route)
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        return {"candidates": fuzzy[:5]}

    return None


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


def get_schedule(route, text, stop_id=None, stop_name=None, kind="next"):
    conn = connect_db()
    if not conn:
        return {"error": "db_unavailable"}
    try:
        stop = resolve_stop(conn, route, text, stop_id=stop_id, stop_name=stop_name)
        if not stop:
            return {"error": "stop_not_found"}
        if isinstance(stop, dict) and "candidates" in stop:
            return {"error": "multiple_stops", "candidates": stop["candidates"]}

        q_date = parse_date(text)
        q_time = parse_time(text)
        date_str = q_date.strftime("%Y-%m-%d")

        if kind == "first":
            first_time = first_or_last_departure(conn, route, stop["stop_id_padded"], date_str, first=True)
            return {"route": route, "stop": stop["stop_name"], "date": date_str, "first_departure": first_time}
        if kind == "last":
            last_time = first_or_last_departure(conn, route, stop["stop_id_padded"], date_str, first=False)
            return {"route": route, "stop": stop["stop_name"], "date": date_str, "last_departure": last_time}

        if not q_time:
            now = datetime.now(TZ)
            q_time = now.strftime("%H:%M:%S")

        rows = next_departures_per_headsign(conn, route, stop["stop_id_padded"], date_str, q_time)
        return {
            "route": route,
            "stop": stop["stop_name"],
            "date": date_str,
            "time": q_time,
            "next_by_direction": [(r["departure_time"], r["trip_headsign"]) for r in rows],
        }
    finally:
        conn.close()
