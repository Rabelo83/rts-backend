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
STOP_AREAS_PATH = BASE_DIR / "data" / "stop_areas.json"

# Map user-facing place names → Area codes in stop_areas.json
_AREA_ALIASES: dict[str, str] = {
    "uf": "UF",
    "university of florida": "UF",
    "university florida": "UF",
    "campus": "UF",
    "uf campus": "UF",
    "gator": "UF",
    "reitz": "UF",
    "the hub": "UF",
    "downtown": "CG",
    "downtown gainesville": "CG",
    "rosa parks": "CG",
    "transit center": "CG",
    "alachua": "AL",
    "alachua county": "AL",
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

    if "today" in text:
        return today
    if "tomorrow" in text:
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
    params = {"like": f"%{name_like}%"}
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
    return [{"stop_id_padded": r["stop_id_padded"], "stop_name": r["stop_name"]} for r in rows]


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
    ranked AS (
      SELECT r.route_short_name, st.departure_time, t.trip_headsign,
             ROW_NUMBER() OVER (PARTITION BY r.route_short_name, t.trip_headsign ORDER BY st.departure_time) AS rn
      FROM stops s
      JOIN stop_times st ON st.stop_id = s.stop_id
      JOIN trips t ON t.trip_id = st.trip_id
      JOIN routes r ON r.route_id = t.route_id
      JOIN active_services a ON a.service_id = t.service_id
      WHERE s.stop_id_padded = :stop_id
        AND st.departure_time >= :time
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
        pattern = f"%{destination.lower()}%"
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
        return [{"route_id": r["route_short_name"], "route_long_name": r["route_long_name"]} for r in rows]
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
        # Resolve date
        if date_str:
            try:
                target = date.fromisoformat(date_str)
            except ValueError:
                target = date.today()
        else:
            target = date.today()

        date_iso = target.isoformat()
        date_compact = target.strftime("%Y%m%d")
        dow = target.weekday()  # 0=Mon … 6=Sun

        if dow < 5:
            day_label = f"{target.strftime('%A')} (weekday)"
            day_type = "weekday"
        elif dow == 5:
            day_label = "Saturday"
            day_type = "saturday"
        else:
            day_label = "Sunday"
            day_type = "sunday"

        # Confirm route exists
        rte = conn.execute(
            "SELECT route_short_name, route_long_name FROM routes WHERE route_short_name = ?",
            (route_id,),
        ).fetchone()
        if not rte:
            return None

        # Query first/last departure per headsign using same active_services CTE as the rest of the code
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
        )
        SELECT t.trip_headsign,
               MIN(st.departure_time) AS first_dep,
               MAX(st.departure_time) AS last_dep,
               COUNT(DISTINCT t.trip_id) AS trips
        FROM trips t
        JOIN routes r       ON r.route_id  = t.route_id
        JOIN active_services a ON a.service_id = t.service_id
        JOIN stop_times st  ON st.trip_id = t.trip_id AND st.stop_sequence = 1
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
