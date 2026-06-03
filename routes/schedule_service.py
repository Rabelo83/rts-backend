import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from agency_config import get_timezone
from routes.parsing_helpers import expand_landmark_aliases, format_time_12h

TZ = ZoneInfo(get_timezone())
BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "Backend Basics" / "db" / "rts_gtfs.sqlite"
DEFAULTS_PATH = BASE_DIR / "Backend Basics" / "db" / "answering_defaults.json"
STOP_AREAS_PATH = BASE_DIR / "data" / "stop_areas.json"

# Map user-facing place names → Area codes in stop_areas.json
# Includes Spanish equivalents so LLM-extracted Spanish destinations resolve correctly.
_AREA_ALIASES: dict[str, str] = {
    # English — UF / campus
    "uf": "UF",
    "university of florida": "UF",
    "university florida": "UF",
    "campus": "UF",
    "uf campus": "UF",
    "gator": "UF",
    "reitz": "UF",
    "the hub": "UF",
    # Spanish — UF / campus
    "universidad de florida": "UF",
    "universidad florida": "UF",
    "la universidad": "UF",
    "universidad": "UF",
    "uf gainesville": "UF",
    # English — Downtown / CG
    "downtown": "CG",
    "downtown gainesville": "CG",
    "rosa parks": "CG",
    "transit center": "CG",
    # Spanish — Downtown / CG
    "centro": "CG",
    "centro de gainesville": "CG",
    "centro de la ciudad": "CG",
    "el centro": "CG",
    # English — Alachua / AL
    "alachua": "AL",
    "alachua county": "AL",
    # Other areas
    "lake city": "Lake City",
    "trenton": "Trenton City",
}

def _load_stop_areas() -> dict[str, list[str]]:
    """Load area→[stop_id] mapping from stop_areas.json. Returns {} on error."""
    try:
        with STOP_AREAS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_STOP_AREAS: dict[str, list[str]] = _load_stop_areas()


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
    today = datetime.now(TZ).date()

    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    mdY = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if mdY:
        return date(int(mdY.group(3)), int(mdY.group(1)), int(mdY.group(2)))

    weekdays = {
        "monday": 0,    "lunes": 0,
        "tuesday": 1,   "martes": 1,
        "wednesday": 2, "miércoles": 2, "miercoles": 2,
        "thursday": 3,  "jueves": 3,
        "friday": 4,    "viernes": 4,
        "saturday": 5,  "sábado": 5, "sabado": 5,
        "sunday": 6,    "domingo": 6,
    }
    for name, idx in weekdays.items():
        if name in text:
            days_ahead = (idx - today.weekday()) % 7
            if days_ahead == 0:
                return today
            return today + timedelta(days=days_ahead)

    if "weekday" in text or "weekdays" in text or "dias de semana" in text:
        # If today is weekday (Mon-Fri), use today; else next Monday
        if today.weekday() < 5:
            return today
        days_ahead = (0 - today.weekday()) % 7
        return today + timedelta(days=days_ahead)

    if "weekend" in text or "weekends" in text or "fin de semana" in text:
        # If today is weekend (Sat/Sun), use today; else next Saturday
        if today.weekday() >= 5:
            return today
        days_ahead = (5 - today.weekday()) % 7
        return today + timedelta(days=days_ahead)

    if "today" in text or "hoy" in text:
        return today
    if "day after tomorrow" in text or "pasado mañana" in text or "pasado manana" in text:
        return today + timedelta(days=2)
    if "tomorrow" in text or "mañana" in text or "manana" in text:
        return today + timedelta(days=1)
    return today


def parse_time(text):
    text = (text or "").lower()
    if re.search(r"\bnoon\b", text):
        return "12:00:00"
    if re.search(r"\bmidnight\b", text):
        return "00:00:00"
    # Explicit times first (e.g. "7am", "3:30pm") — takes priority over vague words
    m = re.search(r"\b(\d{1,2})(?::(\d{1,2}))?\s*(am|pm)\b", text)
    if m:
        hour = int(m.group(1))
        minute_raw = m.group(2) or "0"
        if len(minute_raw) == 1:
            minute_raw = minute_raw.zfill(2)
        minute = int(minute_raw)
        ampm = m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}:00"
    # Vague time-of-day words → anchor to start of that window
    if "morning" in text or "mañana" in text or "madrugada" in text:
        return "06:00:00"
    if "afternoon" in text or "tarde" in text:
        return "12:00:00"
    if "evening" in text or "noche" in text or "night" in text:
        return "17:00:00"
    return None


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


def get_active_service_label(date_et: date | None = None) -> str:
    """Return a human-readable label for today's active GTFS service type.

    Returns one of: 'Reduced Service', 'Regular Weekday', 'Saturday', 'Sunday',
    'No Service', or a comma-joined list when multiple are active.
    Used to inject service context into the agent system prompt.
    """
    if date_et is None:
        date_et = datetime.now(TZ).date()
    date_compact = date_et.strftime("%Y%m%d")
    date_iso = date_et.isoformat()
    day_of_week = date_et.weekday()  # 0=Mon, 6=Sun
    dow_map = {0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday",
               4: "friday", 5: "saturday", 6: "sunday"}
    dow_col = dow_map[day_of_week]

    conn = connect_db()
    if conn is None:
        return "Unknown"
    try:
        rows = conn.execute(
            f"""
            SELECT service_id FROM calendar
            WHERE :dc BETWEEN start_date AND end_date
              AND {dow_col} = 1
            """,
            {"dc": date_compact},
        ).fetchall()
        base = {r["service_id"] for r in rows}

        exc = conn.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?",
            (date_compact,),
        ).fetchall()
        for r in exc:
            if r["exception_type"] == "1" or r["exception_type"] == 1:
                base.add(r["service_id"])
            elif r["exception_type"] == "2" or r["exception_type"] == 2:
                base.discard(r["service_id"])

        if not base:
            return "No Service"
        # Match any service_id that signals reduced service (underscore or hyphen variants)
        if any("Reduced" in s for s in base):
            return "Reduced Service"
        if "Weekday" in base or "Mon-Thur" in base:
            return "Regular Weekday"
        if "Saturday" in base:
            return "Saturday Schedule"
        if "Sunday" in base:
            return "Sunday Schedule"
        return ", ".join(sorted(base))
    finally:
        conn.close()


def get_route_first_last_by_service_type(route_id: str) -> dict:
    """Return first and last departure per service type for a route.

    Queries stop_times at the first stop of each trip (MIN stop_sequence),
    grouped by service_id. Returns a dict like:
        {
          "Weekday":         {"first": "6:00 AM", "last": "10:30 PM"},
          "Saturday":        {"first": "7:00 AM", "last": "9:00 PM"},
          "Sunday":          {"first": "10:10 AM", "last": "6:30 PM"},
          "Reduced Service": {"first": "7:30 AM", "last": "8:00 PM"},
        }
    Keys only appear when the route has trips for that service type.
    """
    conn = connect_db()
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            """
            WITH first_stop AS (
                SELECT trip_id, MIN(stop_sequence) AS min_seq
                FROM stop_times
                GROUP BY trip_id
            )
            SELECT
                t.service_id,
                MIN(st.departure_time) AS first_dep,
                MAX(st.departure_time) AS last_dep
            FROM trips t
            JOIN routes r   ON r.route_id  = t.route_id
            JOIN first_stop fs ON fs.trip_id = t.trip_id
            JOIN stop_times st ON st.trip_id = t.trip_id
                               AND st.stop_sequence = fs.min_seq
            WHERE r.route_short_name = ?
            GROUP BY t.service_id
            """,
            (route_id,),
        ).fetchall()

        _label = {
            "Weekday": "Weekday", "Mon-Thur": "Weekday",
            "Reduced_Service": "Reduced Service",
            "Reduced-Mo-Th": "Reduced Service", "Reduced-Fr": "Reduced Service",
            "Saturday": "Saturday", "Sunday": "Sunday",
        }
        result = {}
        for row in rows:
            label = _label.get(row["service_id"], row["service_id"])
            # Keep the earliest first / latest last if multiple service_ids map to same label
            if label not in result:
                result[label] = {
                    "first": format_time_12h(row["first_dep"]),
                    "last":  format_time_12h(row["last_dep"]),
                }
        return result
    finally:
        conn.close()


def get_service_exceptions(conn, date_compact: str) -> dict:
    rows = conn.execute(
        "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?",
        (date_compact,),
    ).fetchall()
    added = [r["service_id"] for r in rows if r["exception_type"] == 1]
    removed = [r["service_id"] for r in rows if r["exception_type"] == 2]
    if not added and not removed:
        return {}
    return {"added": added, "removed": removed}


def find_stop_by_alias(text, defaults):
    norm = normalize_text(text)
    for d in defaults:
        if d.get("alias") and d["alias"] in norm:
            return {"stop_id_padded": d["stop_id_padded"], "stop_name": d["stop_name"]}
    return None


def find_stops_like(conn, name_like, route_short_name=None):
    variants = expand_landmark_aliases(name_like) or [name_like]
    seen: set[str] = set()
    matches: list[dict] = []

    for variant in variants:
        params = {"like": f"%{variant}%"}
        if route_short_name:
            sql = """
            SELECT DISTINCT s.stop_id_padded, s.stop_name
            FROM stops s
            JOIN stop_times st ON st.stop_id = s.stop_id
            JOIN trips t ON t.trip_id = st.trip_id
            JOIN routes r ON r.route_id = t.route_id
            WHERE TRIM(r.route_short_name) = :route
              AND LOWER(TRIM(s.stop_name)) LIKE LOWER(:like)
            ORDER BY s.stop_name;
            """
            params["route"] = route_short_name
        else:
            sql = """
            SELECT stop_id_padded, stop_name
            FROM stops
            WHERE LOWER(TRIM(stop_name)) LIKE LOWER(:like)
            ORDER BY stop_name;
            """
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            stop_id = row["stop_id_padded"]
            if stop_id in seen:
                continue
            seen.add(stop_id)
            matches.append({"stop_id_padded": stop_id, "stop_name": row["stop_name"]})
    return matches


def find_stops_fuzzy(conn, name_text, route_short_name=None):
    variants = expand_landmark_aliases(name_text) or [name_text]
    seen: set[str] = set()
    matches: list[dict] = []

    for variant in variants:
        norm = normalize_text(variant)
        if not norm:
            continue
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
              AND TRIM(r.route_short_name) = :route
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
        for row in rows:
            stop_id = row["stop_id_padded"]
            if stop_id in seen:
                continue
            seen.add(stop_id)
            matches.append({"stop_id_padded": stop_id, "stop_name": row["stop_name"]})
    return matches


def stop_on_route(conn, route_short_name, stop_id_padded):
    if not route_short_name or not stop_id_padded:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM stops s
        JOIN stop_times st ON st.stop_id = s.stop_id
        JOIN trips t ON t.trip_id = st.trip_id
        JOIN routes r ON r.route_id = t.route_id
        WHERE TRIM(r.route_short_name) = ?
          AND s.stop_id_padded = ?
        LIMIT 1
        """,
        (route_short_name, stop_id_padded),
    ).fetchone()
    return bool(row)


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
        # Only use defaults if the stop is actually on the requested route.
        if stop_on_route(conn, route, stop["stop_id_padded"]):
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


def next_departures_per_headsign(conn, route_short_name, stop_id_padded, date_iso, date_compact, time_str):
    sql = """
    WITH base_services AS (
      SELECT c.service_id
      FROM calendar c
      WHERE :date_compact BETWEEN c.start_date AND c.end_date
        AND (
          (c.monday = 1 AND strftime('%w', :date_iso) = '1') OR
          (c.tuesday = 1 AND strftime('%w', :date_iso) = '2') OR
          (c.wednesday = 1 AND strftime('%w', :date_iso) = '3') OR
          (c.thursday = 1 AND strftime('%w', :date_iso) = '4') OR
          (c.friday = 1 AND strftime('%w', :date_iso) = '5') OR
          (c.saturday = 1 AND strftime('%w', :date_iso) = '6') OR
          (c.sunday = 1 AND strftime('%w', :date_iso) = '0')
        )
    ),
    exception_add AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date_compact AND exception_type = 1
    ),
    exception_remove AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date_compact AND exception_type = 2
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
        {
            "date_iso": date_iso,
            "date_compact": date_compact,
            "route": route_short_name,
            "stop_id": stop_id_padded,
            "time": time_str,
        },
    ).fetchall()

def last_departures_before(conn, route_short_name, stop_id_padded, date_iso, date_compact, time_str):
    """Return the last departure per headsign strictly before time_str."""
    sql = """
    WITH base_services AS (
      SELECT c.service_id
      FROM calendar c
      WHERE :date_compact BETWEEN c.start_date AND c.end_date
        AND (
          (c.monday = 1 AND strftime('%w', :date_iso) = '1') OR
          (c.tuesday = 1 AND strftime('%w', :date_iso) = '2') OR
          (c.wednesday = 1 AND strftime('%w', :date_iso) = '3') OR
          (c.thursday = 1 AND strftime('%w', :date_iso) = '4') OR
          (c.friday = 1 AND strftime('%w', :date_iso) = '5') OR
          (c.saturday = 1 AND strftime('%w', :date_iso) = '6') OR
          (c.sunday = 1 AND strftime('%w', :date_iso) = '0')
        )
    ),
    exception_add AS (
      SELECT service_id FROM calendar_dates
      WHERE date = :date_compact AND exception_type = 1
    ),
    exception_remove AS (
      SELECT service_id FROM calendar_dates
      WHERE date = :date_compact AND exception_type = 2
    ),
    active_services AS (
      SELECT service_id FROM base_services
      UNION SELECT service_id FROM exception_add
      EXCEPT SELECT service_id FROM exception_remove
    ),
    ranked AS (
      SELECT st.departure_time, t.trip_headsign,
             ROW_NUMBER() OVER (PARTITION BY t.trip_headsign ORDER BY st.departure_time DESC) AS rn
      FROM stops s
      JOIN stop_times st ON st.stop_id = s.stop_id
      JOIN trips t ON t.trip_id = st.trip_id
      JOIN routes r ON r.route_id = t.route_id
      JOIN active_services a ON a.service_id = t.service_id
      WHERE r.route_short_name = :route
        AND s.stop_id_padded = :stop_id
        AND st.departure_time < :time
    )
    SELECT departure_time, trip_headsign
    FROM ranked
    WHERE rn = 1
    ORDER BY departure_time DESC;
    """
    return conn.execute(
        sql,
        {
            "date_iso": date_iso,
            "date_compact": date_compact,
            "route": route_short_name,
            "stop_id": stop_id_padded,
            "time": time_str,
        },
    ).fetchall()


def next_departures_all_routes(conn, stop_id_padded, date_iso, date_compact, time_str):
    sql = """
    WITH base_services AS (
      SELECT c.service_id
      FROM calendar c
      WHERE :date_compact BETWEEN c.start_date AND c.end_date
        AND (
          (c.monday = 1 AND strftime('%w', :date_iso) = '1') OR
          (c.tuesday = 1 AND strftime('%w', :date_iso) = '2') OR
          (c.wednesday = 1 AND strftime('%w', :date_iso) = '3') OR
          (c.thursday = 1 AND strftime('%w', :date_iso) = '4') OR
          (c.friday = 1 AND strftime('%w', :date_iso) = '5') OR
          (c.saturday = 1 AND strftime('%w', :date_iso) = '6') OR
          (c.sunday = 1 AND strftime('%w', :date_iso) = '0')
        )
    ),
    exception_add AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date_compact AND exception_type = 1
    ),
    exception_remove AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date_compact AND exception_type = 2
    ),
    active_services AS (
      SELECT service_id FROM base_services
      UNION
      SELECT service_id FROM exception_add
      EXCEPT
      SELECT service_id FROM exception_remove
    ),
    trip_bounds AS (
      SELECT trip_id,
             MIN(stop_sequence) AS min_seq,
             MAX(stop_sequence) AS max_seq
      FROM stop_times
      GROUP BY trip_id
    ),
    candidates AS (
      SELECT r.route_short_name, st.departure_time, t.trip_headsign,
             CAST(st.stop_sequence - tb.min_seq AS REAL)
               / NULLIF(tb.max_seq - tb.min_seq, 0) AS rel_pos
      FROM stops s
      JOIN stop_times st ON st.stop_id = s.stop_id
      JOIN trip_bounds tb ON tb.trip_id = st.trip_id
      JOIN trips t ON t.trip_id = st.trip_id
      JOIN routes r ON r.route_id = t.route_id
      JOIN active_services a ON a.service_id = t.service_id
      WHERE s.stop_id_padded = :stop_id
        AND st.departure_time >= :time
    ),
    routes_with_outbound AS (
      -- Routes that have at least one departure where this stop is in the first half
      -- of the trip (true outbound departures, not inbound arrivals at a hub).
      SELECT DISTINCT route_short_name FROM candidates WHERE rel_pos < 0.5
    ),
    ranked AS (
      SELECT c.route_short_name, c.departure_time, c.trip_headsign,
             ROW_NUMBER() OVER (PARTITION BY c.route_short_name ORDER BY c.departure_time) AS rn
      FROM candidates c
      WHERE
        -- Prefer outbound-only trips for hub stops (avoids "→ Downtown" at Downtown)
        (c.route_short_name IN (SELECT route_short_name FROM routes_with_outbound)
         AND c.rel_pos < 0.5)
        OR
        -- Fallback: show best available for terminal/end-of-line stops that only
        -- appear in the latter half of trips (e.g. Haystacks on route 75)
        c.route_short_name NOT IN (SELECT route_short_name FROM routes_with_outbound)
    )
    SELECT route_short_name, departure_time, trip_headsign
    FROM ranked
    WHERE rn = 1
    ORDER BY route_short_name, departure_time;
    """
    return conn.execute(
        sql,
        {
            "date_iso": date_iso,
            "date_compact": date_compact,
            "stop_id": stop_id_padded,
            "time": time_str,
        },
    ).fetchall()

def first_or_last_departure(conn, route_short_name, stop_id_padded, date_iso, date_compact, first=True):
    sql = """
    WITH base_services AS (
      SELECT c.service_id
      FROM calendar c
      WHERE :date_compact BETWEEN c.start_date AND c.end_date
        AND (
          (c.monday = 1 AND strftime('%w', :date_iso) = '1') OR
          (c.tuesday = 1 AND strftime('%w', :date_iso) = '2') OR
          (c.wednesday = 1 AND strftime('%w', :date_iso) = '3') OR
          (c.thursday = 1 AND strftime('%w', :date_iso) = '4') OR
          (c.friday = 1 AND strftime('%w', :date_iso) = '5') OR
          (c.saturday = 1 AND strftime('%w', :date_iso) = '6') OR
          (c.sunday = 1 AND strftime('%w', :date_iso) = '0')
        )
    ),
    exception_add AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date_compact AND exception_type = 1
    ),
    exception_remove AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date_compact AND exception_type = 2
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
        sql,
        {
            "date_iso": date_iso,
            "date_compact": date_compact,
            "route": route_short_name,
            "stop_id": stop_id_padded,
        },
    ).fetchone()
    return row["result"] if row else None


def get_schedule(route, text, stop_id=None, stop_name=None, kind="next", debug=False):
    conn = connect_db()
    if not conn:
        return {"error": "db_unavailable"}
    try:
        stop = resolve_stop(conn, route, text, stop_id=stop_id, stop_name=stop_name)
        if not stop:
            out = {"error": "stop_not_found"}
            if debug:
                out["debug"] = {
                    "route": route,
                    "stop_id": stop_id,
                    "stop_name": stop_name,
                    "stop_term": extract_stop_term(text) or text,
                }
            return out
        if isinstance(stop, dict) and "candidates" in stop:
            return {"error": "multiple_stops", "candidates": stop["candidates"]}

        q_date = parse_date(text)
        q_time = parse_time(text)
        date_iso = q_date.strftime("%Y-%m-%d")
        date_compact = q_date.strftime("%Y%m%d")

        exception_info = get_service_exceptions(conn, date_compact)

        if kind == "first":
            first_time = first_or_last_departure(conn, route, stop["stop_id_padded"], date_iso, date_compact, first=True)
            out = {"route": route, "stop": stop["stop_name"], "date": date_iso, "first_departure": first_time}
            if exception_info:
                out["exception"] = exception_info
            if debug:
                out["debug"] = {
                    "route": route,
                    "stop_id": stop["stop_id_padded"],
                    "date_iso": date_iso,
                    "date_compact": date_compact,
                    "time": None,
                    "kind": "first",
                }
            return out
        if kind == "last":
            last_time = first_or_last_departure(conn, route, stop["stop_id_padded"], date_iso, date_compact, first=False)
            out = {"route": route, "stop": stop["stop_name"], "date": date_iso, "last_departure": last_time}
            if exception_info:
                out["exception"] = exception_info
            if debug:
                out["debug"] = {
                    "route": route,
                    "stop_id": stop["stop_id_padded"],
                    "date_iso": date_iso,
                    "date_compact": date_compact,
                    "time": None,
                    "kind": "last",
                }
            return out
        if kind == "before":
            # Last departure strictly before q_time
            if not q_time:
                return {"error": "time_required_for_kind_before"}
            rows = last_departures_before(conn, route, stop["stop_id_padded"], date_iso, date_compact, q_time)
            out = {
                "route": route,
                "stop": stop["stop_name"],
                "date": date_iso,
                "time": q_time,
                "before_by_direction": [(r["departure_time"], r["trip_headsign"]) for r in rows],
            }
            if exception_info:
                out["exception"] = exception_info
            return out

        if not q_time:
            now = datetime.now(TZ)
            q_time = now.strftime("%H:%M:%S")

        rows = next_departures_per_headsign(conn, route, stop["stop_id_padded"], date_iso, date_compact, q_time)
        out = {
            "route": route,
            "stop": stop["stop_name"],
            "date": date_iso,
            "time": q_time,
            "next_by_direction": [(r["departure_time"], r["trip_headsign"]) for r in rows],
        }
        if exception_info:
            out["exception"] = exception_info
        if debug:
            out["debug"] = {
                "route": route,
                "stop_id": stop["stop_id_padded"],
                "date_iso": date_iso,
                "date_compact": date_compact,
                "time": q_time,
                "kind": "next",
            }
        return out
    finally:
        conn.close()

def get_schedule_all_routes(text, stop_id=None, debug=False):
    conn = connect_db()
    if not conn:
        return {"error": "db_unavailable"}
    try:
        if not stop_id:
            return {"error": "stop_not_found"}
        row = conn.execute(
            "SELECT stop_id_padded, stop_name FROM stops WHERE stop_id_padded = ?",
            (stop_id,),
        ).fetchone()
        if not row:
            return {"error": "stop_not_found"}

        q_date = parse_date(text)
        q_time = parse_time(text)
        date_iso = q_date.strftime("%Y-%m-%d")
        date_compact = q_date.strftime("%Y%m%d")
        if not q_time:
            now = datetime.now(TZ)
            q_time = now.strftime("%H:%M:%S")

        exception_info = get_service_exceptions(conn, date_compact)
        rows = next_departures_all_routes(conn, row["stop_id_padded"], date_iso, date_compact, q_time)
        out = {
            "stop": row["stop_name"],
            "date": date_iso,
            "time": q_time,
            "next_by_route": [(r["route_short_name"], r["departure_time"], r["trip_headsign"]) for r in rows],
        }
        if exception_info:
            out["exception"] = exception_info
        if debug:
            out["debug"] = {
                "stop_id": row["stop_id_padded"],
                "date_iso": date_iso,
                "date_compact": date_compact,
                "time": q_time,
                "kind": "next",
            }
        return out
    finally:
        conn.close()


def routes_serving_destination(destination: str, limit: int = 12) -> list[dict]:
    """
    Return routes whose stops contain `destination` in the stop name.
    Each dict has keys: route_id (short name), route_long_name.
    Used to answer "What routes go to UF?" type queries.
    """
    if not destination:
        return []
    conn = connect_db()
    if not conn:
        return []
    try:
        variants = expand_landmark_aliases(destination) or [destination]
        seen: set[str] = set()
        results: list[dict] = []
        for variant in variants:
            pattern = f"%{variant.lower()}%"
            rows = conn.execute(
                """
                SELECT DISTINCT r.route_short_name AS route_id,
                                r.route_long_name  AS route_long_name
                FROM stops s
                JOIN stop_times st ON st.stop_id = s.stop_id
                JOIN trips t       ON t.trip_id  = st.trip_id
                JOIN routes r      ON r.route_id = t.route_id
                WHERE LOWER(s.stop_name) LIKE ?
                ORDER BY CAST(r.route_short_name AS INTEGER), r.route_short_name
                LIMIT ?
                """,
                (pattern, limit),
            ).fetchall()
            for row in rows:
                route_id = row["route_id"]
                if route_id in seen:
                    continue
                seen.add(route_id)
                results.append({"route_id": route_id, "route_long_name": row["route_long_name"]})
                if len(results) >= limit:
                    return results
        return results
    except Exception:
        return []
    finally:
        conn.close()


def routes_serving_area(destination_hint: str, limit: int = 16) -> list[dict]:
    """
    Return routes serving a named area (e.g. 'UF', 'downtown') using the
    authoritative stop_areas.json inventory instead of fuzzy stop-name matching.

    Returns [] if the hint doesn't map to a known area or no stop IDs overlap
    with GTFS — falls back to routes_serving_destination() in the caller.
    """
    if not destination_hint:
        return []
    area_code = _AREA_ALIASES.get(destination_hint.lower().strip())
    if not area_code:
        return []
    stop_ids = _STOP_AREAS.get(area_code, [])
    if not stop_ids:
        return []

    conn = connect_db()
    if not conn:
        return []
    try:
        placeholders = ",".join("?" * len(stop_ids))
        rows = conn.execute(
            f"""
            SELECT DISTINCT r.route_short_name AS route_id,
                            r.route_long_name  AS route_long_name
            FROM stop_times st
            JOIN trips t ON t.trip_id  = st.trip_id
            JOIN routes r ON r.route_id = t.route_id
            WHERE st.stop_id IN ({placeholders})
            ORDER BY CAST(r.route_short_name AS INTEGER), r.route_short_name
            LIMIT ?
            """,
            (*stop_ids, limit),
        ).fetchall()
        return [{"route_id": r["route_id"], "route_long_name": r["route_long_name"]} for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _resolve_schedule_target_date(date_str: str | None = None) -> date:
    if date_str:
        try:
            return date.fromisoformat(date_str)
        except ValueError:
            pass
    return datetime.now(TZ).date()


def _describe_service_day(target: date) -> tuple[str, str]:
    dow = target.weekday()  # 0=Mon … 6=Sun
    if dow < 5:
        return f"{target.strftime('%A')} (weekday)", "weekday"
    if dow == 5:
        return "Saturday", "saturday"
    return "Sunday", "sunday"


def get_route_day_summary(route_id: str, date_str: str | None = None) -> dict | None:
    """
    Return a high-level schedule summary for a route on a given date.

    Result dict keys:
        route_id        str   — short name ('13')
        route_long_name str   — e.g. 'US 441 to Alight Apartments'
        date_iso        str   — 'YYYY-MM-DD'
        day_label       str   — 'Friday (weekday)', 'Saturday', 'Sunday'
        day_type        str   — 'weekday' | 'saturday' | 'sunday' | 'none'
        directions      list  — [{'headsign': ..., 'first': 'H:MM AM', 'last': 'H:MM PM', 'trips': n}, ...]
        runs_today      bool  — False if no service on this date

    Returns None if route_id not found in GTFS.
    """
    if not route_id:
        return None

    conn = connect_db()
    if not conn:
        return None

    try:
        target = _resolve_schedule_target_date(date_str)
        date_iso = target.isoformat()
        date_compact = target.strftime("%Y%m%d")
        day_label, day_type = _describe_service_day(target)

        # Confirm route exists
        rte = conn.execute(
            "SELECT route_short_name, route_long_name FROM routes WHERE route_short_name = ?",
            (route_id,),
        ).fetchone()
        if not rte:
            return None

        # Query first/last departure per headsign using same active_services CTE as the rest of the code.
        # trip_first_seq finds the minimum stop_sequence for each trip so that routes
        # where stop_sequence doesn't start at 1 (e.g. starts at 0 or 2) are handled correctly.
        sql = """
        WITH base_services AS (
          SELECT c.service_id FROM calendar c
          WHERE :date_compact BETWEEN c.start_date AND c.end_date
            AND (
              (c.monday    = 1 AND strftime('%w', :date_iso) = '1') OR
              (c.tuesday   = 1 AND strftime('%w', :date_iso) = '2') OR
              (c.wednesday = 1 AND strftime('%w', :date_iso) = '3') OR
              (c.thursday  = 1 AND strftime('%w', :date_iso) = '4') OR
              (c.friday    = 1 AND strftime('%w', :date_iso) = '5') OR
              (c.saturday  = 1 AND strftime('%w', :date_iso) = '6') OR
              (c.sunday    = 1 AND strftime('%w', :date_iso) = '0')
            )
        ),
        exception_add    AS (SELECT service_id FROM calendar_dates WHERE date = :date_compact AND exception_type = 1),
        exception_remove AS (SELECT service_id FROM calendar_dates WHERE date = :date_compact AND exception_type = 2),
        active_services  AS (
          SELECT service_id FROM base_services
          UNION  SELECT service_id FROM exception_add
          EXCEPT SELECT service_id FROM exception_remove
        ),
        trip_first_seq AS (
          SELECT trip_id, MIN(stop_sequence) AS min_seq
          FROM stop_times
          GROUP BY trip_id
        )
        SELECT t.trip_headsign,
               MIN(st.departure_time) AS first_dep,
               MAX(st.departure_time) AS last_dep,
               COUNT(DISTINCT t.trip_id) AS trips
        FROM trips t
        JOIN routes r          ON r.route_id   = t.route_id
        JOIN active_services a ON a.service_id  = t.service_id
        JOIN trip_first_seq tfs ON tfs.trip_id  = t.trip_id
        JOIN stop_times st     ON st.trip_id    = t.trip_id
                               AND st.stop_sequence = tfs.min_seq
        WHERE r.route_short_name = :route
        GROUP BY t.trip_headsign
        ORDER BY first_dep
        """
        rows = conn.execute(sql, {
            "date_iso": date_iso,
            "date_compact": date_compact,
            "route": route_id,
        }).fetchall()

        def _fmt(t: str) -> str:
            """'06:00:00' → '6:00 AM'  '13:30:00' → '1:30 PM'"""
            try:
                h, m, _ = t.split(":")
                h, m = int(h), int(m)
                suffix = "AM" if h < 12 else "PM"
                h12 = h % 12 or 12
                return f"{h12}:{m:02d} {suffix}"
            except Exception:
                return t

        def _to_minutes(t: str) -> int:
            try:
                h, m, _ = t.split(":")
                return int(h) * 60 + int(m)
            except Exception:
                return 0

        def _freq_label(first: str, last: str, trips: int) -> str | None:
            """Return human-readable frequency like 'every ~30 min' or 'every ~1 hr'."""
            if trips < 2:
                return None
            span = _to_minutes(last) - _to_minutes(first)
            if span <= 0:
                return None
            avg = span / (trips - 1)
            # Round to nearest 5 minutes
            rounded = max(5, round(avg / 5) * 5)
            if rounded >= 60 and rounded % 60 == 0:
                hrs = rounded // 60
                return f"every ~{hrs} hr"
            elif rounded >= 60:
                hrs = rounded // 60
                mins = rounded % 60
                return f"every ~{hrs} hr {mins} min"
            return f"every ~{rounded} min"

        directions = [
            {
                "headsign": r["trip_headsign"],
                "first": _fmt(r["first_dep"]),
                "last": _fmt(r["last_dep"]),
                "trips": r["trips"],
                "frequency": _freq_label(r["first_dep"], r["last_dep"], r["trips"]),
            }
            for r in rows
        ]

        return {
            "route_id": route_id,
            "route_long_name": rte["route_long_name"],
            "date_iso": date_iso,
            "day_label": day_label,
            "day_type": day_type,
            "directions": directions,
            "runs_today": len(directions) > 0,
        }

    except Exception:
        return None
    finally:
        conn.close()


def get_route_departure_schedule(route_id: str, date_str: str | None = None) -> dict | None:
    """Return full route departures for a date, grouped by headsign and origin stop."""
    if not route_id:
        return None

    conn = connect_db()
    if conn is None:
        return None

    try:
        target = _resolve_schedule_target_date(date_str)
        date_iso = target.isoformat()
        date_compact = target.strftime("%Y%m%d")
        day_label, day_type = _describe_service_day(target)

        rte = conn.execute(
            "SELECT route_short_name, route_long_name FROM routes WHERE route_short_name = ?",
            (route_id,),
        ).fetchone()
        if not rte:
            return None

        rows = conn.execute(
            """
            WITH base_services AS (
              SELECT c.service_id FROM calendar c
              WHERE :date_compact BETWEEN c.start_date AND c.end_date
                AND (
                  (c.monday    = 1 AND strftime('%w', :date_iso) = '1') OR
                  (c.tuesday   = 1 AND strftime('%w', :date_iso) = '2') OR
                  (c.wednesday = 1 AND strftime('%w', :date_iso) = '3') OR
                  (c.thursday  = 1 AND strftime('%w', :date_iso) = '4') OR
                  (c.friday    = 1 AND strftime('%w', :date_iso) = '5') OR
                  (c.saturday  = 1 AND strftime('%w', :date_iso) = '6') OR
                  (c.sunday    = 1 AND strftime('%w', :date_iso) = '0')
                )
            ),
            exception_add    AS (SELECT service_id FROM calendar_dates WHERE date = :date_compact AND exception_type = 1),
            exception_remove AS (SELECT service_id FROM calendar_dates WHERE date = :date_compact AND exception_type = 2),
            active_services  AS (
              SELECT service_id FROM base_services
              UNION  SELECT service_id FROM exception_add
              EXCEPT SELECT service_id FROM exception_remove
            ),
            trip_first_seq AS (
              SELECT trip_id, MIN(stop_sequence) AS min_seq
              FROM stop_times
              GROUP BY trip_id
            )
            SELECT t.trip_headsign,
                   s.stop_name AS origin_stop_name,
                   st.departure_time
            FROM trips t
            JOIN routes r           ON r.route_id    = t.route_id
            JOIN active_services a  ON a.service_id  = t.service_id
            JOIN trip_first_seq tfs ON tfs.trip_id   = t.trip_id
            JOIN stop_times st      ON st.trip_id    = t.trip_id
                                    AND st.stop_sequence = tfs.min_seq
            JOIN stops s            ON s.stop_id     = st.stop_id
            WHERE r.route_short_name = :route
            ORDER BY st.departure_time, t.trip_headsign, s.stop_name
            """,
            {
                "date_iso": date_iso,
                "date_compact": date_compact,
                "route": route_id,
            },
        ).fetchall()

        grouped: dict[tuple[str, str], dict] = {}
        seen_times: dict[tuple[str, str], set[str]] = {}
        for row in rows:
            headsign = row["trip_headsign"] or "Direction"
            origin_stop_name = row["origin_stop_name"] or ""
            key = (headsign, origin_stop_name)
            entry = grouped.setdefault(key, {
                "headsign": headsign,
                "origin_stop_name": origin_stop_name,
                "departures": [],
            })
            dep_time = row["departure_time"]
            if not dep_time:
                continue
            key_seen = seen_times.setdefault(key, set())
            if dep_time in key_seen:
                continue
            key_seen.add(dep_time)
            entry["departures"].append({
                "time": dep_time,
                "time_label": format_time_12h(dep_time),
            })

        directions = sorted(
            grouped.values(),
            key=lambda item: (
                item["departures"][0]["time"] if item["departures"] else "99:99:99",
                item["headsign"],
                item["origin_stop_name"],
            ),
        )
        total_departures = sum(len(item["departures"]) for item in directions)

        return {
            "route_id": route_id,
            "route_long_name": rte["route_long_name"],
            "date_iso": date_iso,
            "day_label": day_label,
            "day_type": day_type,
            "runs_today": total_departures > 0,
            "directions": directions,
            "total_departures": total_departures,
        }

    except Exception:
        return None
    finally:
        conn.close()


# ── Timetable helpers ─────────────────────────────────────────────────────────

# Maps GTFS service_id values → user-facing service type slugs
_SERVICE_ID_TO_SLUG: dict[str, str] = {
    "Weekday":         "weekday",
    "Mon-Thur":        "weekday",
    "Saturday":        "saturday",
    "Sunday":          "sunday",
    "Reduced_Service": "reduced",
    "Reduced-Mo-Th":   "reduced",
    "Reduced-Fr":      "reduced",
}

# Maps service type slugs → all possible GTFS service_id values
_SLUG_TO_SERVICE_IDS: dict[str, tuple[str, ...]] = {
    "weekday":  ("Weekday", "Mon-Thur"),
    "saturday": ("Saturday",),
    "sunday":   ("Sunday",),
    "reduced":  ("Reduced_Service", "Reduced-Mo-Th", "Reduced-Fr"),
}

# Human-readable labels for service type slugs
_SLUG_LABELS: dict[str, str] = {
    "weekday":  "Weekday",
    "saturday": "Saturday",
    "sunday":   "Sunday",
    "reduced":  "Reduced",
}


def _gtfs_secs(t: str) -> int | None:
    """Convert a GTFS HH:MM[:SS] time string (may exceed 24:00) to total seconds."""
    if not t:
        return None
    parts = t.strip().split(":")
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + (int(parts[2]) if len(parts) > 2 else 0)
    except (ValueError, IndexError):
        return None


def _select_key_stops(stops: list, max_stops: int = 8) -> list:
    """Pick up to max_stops evenly-spaced stops from an ordered stop list.

    Always includes the first and last stop.
    """
    n = len(stops)
    if n <= max_stops:
        return list(stops)
    if max_stops <= 2:
        return [stops[0], stops[-1]]
    indices: set[int] = {0, n - 1}
    inner = max_stops - 2
    for i in range(inner):
        idx = round((i + 1) * (n - 1) / (inner + 1))
        indices.add(idx)
    return [stops[i] for i in sorted(indices)]


def get_route_timetable(
    route_id: str,
    service_type: str = "weekday",
    direction: str | None = None,
) -> dict | None:
    """Return a timetable grid for a route, service type, and direction.

    route_id:     route short name ("1", "10", …)
    service_type: "weekday" | "saturday" | "sunday" | "reduced"
    direction:    trip_headsign; defaults to first alphabetically

    Response shape:
    {
        "route": "1",
        "route_name": "...",
        "service_type": "weekday",
        "service_label": "Weekday",
        "available_service_types": ["weekday", "saturday", "sunday"],
        "direction": "To Butler Plaza",
        "directions": ["To Butler Plaza", "To Downtown Station"],
        "stops": [{"stop_id": "0001", "stop_name": "...", "is_key_stop": true}, ...],
        "rows": [{"trip_id": "...", "times": ["6:30 AM", "6:42 AM", null, "7:00 AM"]}, ...]
    }
    Returns None if route not found or DB unavailable.
    """
    if not route_id:
        return None

    conn = connect_db()
    if conn is None:
        return None

    try:
        rte = conn.execute(
            "SELECT route_short_name, route_long_name FROM routes WHERE route_short_name = ?",
            (route_id,),
        ).fetchone()
        if not rte:
            return None

        # All service_ids present in DB for this route
        all_svc_rows = conn.execute(
            """
            SELECT DISTINCT t.service_id FROM trips t
            JOIN routes r ON r.route_id = t.route_id
            WHERE r.route_short_name = ?
            """,
            (route_id,),
        ).fetchall()
        route_service_ids: set[str] = {r["service_id"] for r in all_svc_rows}

        # Available service type slugs for this route (preserving canonical order)
        available_service_types = [
            slug
            for slug in ("weekday", "saturday", "sunday", "reduced")
            if any(sid in route_service_ids for sid in _SLUG_TO_SERVICE_IDS[slug])
        ]

        # Requested service type → matching service_ids present in DB
        wanted_ids = [
            sid for sid in _SLUG_TO_SERVICE_IDS.get(service_type, ("Weekday",))
            if sid in route_service_ids
        ]

        _empty = {
            "route": route_id,
            "route_name": rte["route_long_name"],
            "service_type": service_type,
            "service_label": _SLUG_LABELS.get(service_type, service_type.title()),
            "available_service_types": available_service_types,
            "direction": direction or "",
            "directions": [],
            "stops": [],
            "rows": [],
        }

        if not wanted_ids:
            return _empty

        ph = ",".join("?" * len(wanted_ids))

        # Available directions for this service type — include direction_id for labels
        dir_rows = conn.execute(
            f"""
            SELECT DISTINCT t.trip_headsign, t.direction_id FROM trips t
            JOIN routes r ON r.route_id = t.route_id
            WHERE r.route_short_name = ?
              AND t.service_id IN ({ph})
            ORDER BY t.direction_id, t.trip_headsign
            """,
            (route_id, *wanted_ids),
        ).fetchall()

        # Build direction objects with Outbound/Inbound labels resolved server-side.
        # direction_id is often NULL in RTS GTFS, so we fall back to list position
        # (index 0 = Outbound, index 1 = Inbound) for 2-direction routes.
        _dir_label = {0: "Outbound", 1: "Inbound"}
        raw_dirs = [r for r in dir_rows if r["trip_headsign"]]
        available_directions = []
        for i, r in enumerate(raw_dirs):
            dir_id = r["direction_id"]  # sqlite3.Row direct access; None when NULL
            if dir_id is None and len(raw_dirs) == 2:
                dir_id = i  # positional fallback
            available_directions.append({
                "headsign":    r["trip_headsign"],
                "direction_id": dir_id,
                "label":       _dir_label.get(dir_id),  # "Outbound" / "Inbound" / None
            })
        available_headsigns = [d["headsign"] for d in available_directions]

        # Resolve direction
        if not direction and available_directions:
            direction = available_directions[0]["headsign"]
        elif direction and direction not in available_headsigns:
            direction = available_directions[0]["headsign"] if available_directions else None

        if not direction:
            _empty["directions"] = available_directions
            return _empty

        # Representative trip (most stops) → key stop selection
        rep_row = conn.execute(
            f"""
            SELECT t.trip_id, COUNT(st.stop_id) AS stop_count FROM trips t
            JOIN routes r ON r.route_id = t.route_id
            JOIN stop_times st ON st.trip_id = t.trip_id
            WHERE r.route_short_name = ?
              AND t.trip_headsign = ?
              AND t.service_id IN ({ph})
            GROUP BY t.trip_id ORDER BY stop_count DESC LIMIT 1
            """,
            (route_id, direction, *wanted_ids),
        ).fetchone()

        key_stop_ids: list[str] = []
        stop_objects: list[dict] = []

        # key_stop_target_seqs maps stop_id → its stop_sequence in the rep trip.
        # Used when building time_map to pick the correct occurrence of a stop
        # that appears multiple times in a trip (e.g. lollipop/loop routes).
        key_stop_target_seqs: dict[str, int] = {}

        if rep_row:
            rep_stops = conn.execute(
                """
                SELECT s.stop_id_padded, s.stop_name, st.stop_sequence FROM stop_times st
                JOIN stops s ON s.stop_id = st.stop_id
                WHERE st.trip_id = ? ORDER BY st.stop_sequence
                """,
                (rep_row["trip_id"],),
            ).fetchall()
            selected = _select_key_stops(list(rep_stops), max_stops=8)
            key_stop_ids = [r["stop_id_padded"] for r in selected]
            key_stop_target_seqs = {r["stop_id_padded"]: int(r["stop_sequence"]) for r in selected}
            stop_objects = [
                {"stop_id": r["stop_id_padded"], "stop_name": r["stop_name"], "is_key_stop": True}
                for r in selected
            ]

            # Re-order key stops by average stop_sequence across ALL trips in this
            # direction.  Some routes have variant patterns where the rep trip visits
            # stops in a different order than the majority of runs; using the average
            # puts columns in the order most riders actually experience.
            stop_ph2 = ",".join("?" * len(key_stop_ids))
            avg_rows = conn.execute(
                f"""
                SELECT s.stop_id_padded,
                       AVG(CAST(st.stop_sequence AS REAL)) AS avg_seq
                FROM stop_times st
                JOIN stops s ON s.stop_id = st.stop_id
                JOIN trips t ON t.trip_id = st.trip_id
                JOIN routes r ON r.route_id = t.route_id
                WHERE r.route_short_name = ?
                  AND t.trip_headsign = ?
                  AND t.service_id IN ({ph})
                  AND s.stop_id_padded IN ({stop_ph2})
                GROUP BY s.stop_id_padded
                """,
                (route_id, direction, *wanted_ids, *key_stop_ids),
            ).fetchall()
            avg_seq_map = {r["stop_id_padded"]: r["avg_seq"] for r in avg_rows}
            order = sorted(
                range(len(key_stop_ids)),
                key=lambda i: avg_seq_map.get(key_stop_ids[i], 0),
            )
            key_stop_ids = [key_stop_ids[i] for i in order]
            stop_objects  = [stop_objects[i]  for i in order]

        # All trips ordered by first departure
        trip_rows = conn.execute(
            f"""
            WITH trip_first_seq AS (
                SELECT trip_id, MIN(stop_sequence) AS min_seq FROM stop_times GROUP BY trip_id
            )
            SELECT t.trip_id, st.departure_time AS first_dep FROM trips t
            JOIN routes r ON r.route_id = t.route_id
            JOIN trip_first_seq tfs ON tfs.trip_id = t.trip_id
            JOIN stop_times st ON st.trip_id = t.trip_id AND st.stop_sequence = tfs.min_seq
            WHERE r.route_short_name = ?
              AND t.trip_headsign = ?
              AND t.service_id IN ({ph})
            ORDER BY first_dep
            """,
            (route_id, direction, *wanted_ids),
        ).fetchall()

        rows: list[dict] = []
        if trip_rows and key_stop_ids:
            trip_ids = [r["trip_id"] for r in trip_rows]
            trip_ph = ",".join("?" * len(trip_ids))
            stop_ph = ",".join("?" * len(key_stop_ids))

            times_rows = conn.execute(
                f"""
                SELECT st.trip_id, s.stop_id_padded, st.stop_sequence,
                       COALESCE(st.departure_time, st.arrival_time) AS dep_time
                FROM stop_times st
                JOIN stops s ON s.stop_id = st.stop_id
                WHERE st.trip_id IN ({trip_ph})
                  AND s.stop_id_padded IN ({stop_ph})
                ORDER BY st.trip_id, st.stop_sequence
                """,
                (*trip_ids, *key_stop_ids),
            ).fetchall()

            # Build {trip_id: {stop_id_padded: time}}.
            # When a stop appears multiple times in a trip (lollipop / loop routes)
            # "keep earliest" picks the wrong occurrence — e.g. a stop that appears
            # at seq=2 AND seq=50, where seq=50 is the terminal, would get 6:07 AM
            # instead of the correct arrival time.
            # Fix: keep the occurrence whose stop_sequence is CLOSEST to the sequence
            # that stop had in the representative trip.
            time_map_raw: dict[str, dict[str, tuple[str, int]]] = {}
            for tr in times_rows:
                tid = tr["trip_id"]
                sid = tr["stop_id_padded"]
                dep = tr["dep_time"]
                seq = int(tr["stop_sequence"])
                if not dep:
                    continue
                target = key_stop_target_seqs.get(sid, 0)
                diff = abs(seq - target)
                if tid not in time_map_raw:
                    time_map_raw[tid] = {}
                if sid not in time_map_raw[tid] or diff < time_map_raw[tid][sid][1]:
                    time_map_raw[tid][sid] = (dep, diff)

            time_map: dict[str, dict[str, str]] = {
                tid: {sid: v[0] for sid, v in stops.items()}
                for tid, stops in time_map_raw.items()
            }

            for trip_row in trip_rows:
                tid = trip_row["trip_id"]
                trip_times = time_map.get(tid, {})
                # Safety net: null out any cell whose raw GTFS time goes backwards
                # relative to the previous column.  This hides confusing reverse-order
                # times produced by minority trip variants that visit stops in a
                # different order than the majority (which drove column ordering above).
                last_secs = -1
                times = []
                for sid in key_stop_ids:
                    raw = trip_times.get(sid)
                    if raw is None:
                        times.append(None)
                        continue
                    secs = _gtfs_secs(raw)
                    if secs is not None and secs >= last_secs:
                        times.append(format_time_12h(raw))
                        last_secs = secs
                    else:
                        times.append(None)
                rows.append({"trip_id": tid, "times": times})

        return {
            "route": route_id,
            "route_name": rte["route_long_name"],
            "service_type": service_type,
            "service_label": _SLUG_LABELS.get(service_type, service_type.title()),
            "available_service_types": available_service_types,
            "direction": direction,
            "directions": available_directions,  # [{headsign, direction_id}]
            "stops": stop_objects,               # [{stop_id, stop_name, is_key_stop}]
            "rows": rows,
        }

    finally:
        conn.close()


def get_route_stops(route_id: str, direction_hint: str | None = None) -> dict:
    """Return ordered list of stops for a route, optionally filtered by direction/headsign.

    Returns:
        {
          "status": "ok",
          "route": "1",
          "directions": [
            {
              "headsign": "Butler Plaza",
              "stops": [
                {"stop_id": "0001", "stop_name": "Rosa Parks RTS Downtown Station", "sequence": 1},
                ...
              ]
            },
            ...
          ]
        }
    """
    conn = connect_db()
    if conn is None:
        return {"status": "db_unavailable"}
    try:
        # Get distinct headsigns for this route
        headsigns_rows = conn.execute(
            """
            SELECT DISTINCT t.trip_headsign
            FROM trips t
            JOIN routes r ON r.route_id = t.route_id
            WHERE r.route_short_name = ?
            ORDER BY t.trip_headsign
            """,
            (route_id,),
        ).fetchall()

        if not headsigns_rows:
            return {"status": "route_not_found", "route": route_id}

        all_headsigns = [r["trip_headsign"] for r in headsigns_rows]

        # Filter by direction hint if provided
        if direction_hint:
            hint_lower = direction_hint.lower()
            filtered = [h for h in all_headsigns if hint_lower in h.lower()]
            target_headsigns = filtered if filtered else all_headsigns
        else:
            target_headsigns = all_headsigns

        directions = []
        for headsign in target_headsigns:
            # Get a representative trip for this headsign (longest trip = most stops)
            trip_row = conn.execute(
                """
                SELECT t.trip_id, COUNT(st.stop_id) AS stop_count
                FROM trips t
                JOIN routes r ON r.route_id = t.route_id
                JOIN stop_times st ON st.trip_id = t.trip_id
                WHERE r.route_short_name = ? AND t.trip_headsign = ?
                GROUP BY t.trip_id
                ORDER BY stop_count DESC
                LIMIT 1
                """,
                (route_id, headsign),
            ).fetchone()

            if not trip_row:
                continue

            stops_rows = conn.execute(
                """
                SELECT s.stop_id_padded, s.stop_name, st.stop_sequence
                FROM stop_times st
                JOIN stops s ON s.stop_id = st.stop_id
                WHERE st.trip_id = ?
                ORDER BY st.stop_sequence
                """,
                (trip_row["trip_id"],),
            ).fetchall()

            directions.append({
                "headsign": headsign,
                "stops": [
                    {
                        "stop_id": r["stop_id_padded"],
                        "stop_name": r["stop_name"],
                        "sequence": r["stop_sequence"],
                    }
                    for r in stops_rows
                ],
            })

        return {"status": "ok", "route": route_id, "directions": directions}
    finally:
        conn.close()
