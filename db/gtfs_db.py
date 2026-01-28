"""
GTFS-backed schedule database.

This module provides a small API surface the rest of the app can call.

It expects a SQLite database created by scripts/gtfs_ingest.py.

Key idea:
  stop_times stores dep_secs (seconds since service-day midnight).
  Times after midnight may appear as > 24:00 (e.g. 25:30:00).
  To support those, next_departures() searches both today's and yesterday's
  service days.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta


DB_PATH = os.environ.get("GTFS_DB_PATH", "data/gtfs.sqlite")


# ------------------------------------------------------------
# Low-level helpers
# ------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _like(s: str) -> str:
    return f"%{(s or '').strip()}%"


def _canonical_stop_codes(s: str) -> list[str]:
    """
    Return plausible variants of a public stop number (stop_code).
    Example: "0473" -> ["0473","473"]
    """
    raw = (s or "").strip()
    if not raw:
        return []
    digits = "".join(ch for ch in raw if ch.isdigit())
    out = set()
    if raw:
        out.add(raw)
    if digits:
        out.add(digits)
        out.add(digits.lstrip("0") or "0")
        if len(digits) <= 4:
            out.add(digits.zfill(4))
    return [x for x in out if x]


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _weekday_col(d: date) -> str:
    # GTFS calendar uses monday..sunday
    cols = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    return cols[d.weekday()]


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _active_service_ids(con: sqlite3.Connection, d: date) -> list[str]:
    """
    Return service_ids active on date d, honoring calendar_dates.

    If calendar tables are missing (some feeds), returns [].
    """
    if not _table_exists(con, "calendar"):
        return []

    dstr = _ymd(d)
    col = _weekday_col(d)

    # Base calendar services
    base = con.execute(
        f"""
        SELECT service_id
        FROM calendar
        WHERE start_date <= ? AND end_date >= ?
          AND {col} = 1
        """,
        (dstr, dstr),
    ).fetchall()
    active = {r["service_id"] for r in base}

    # Exceptions (1=added, 2=removed) (optional table)
    if _table_exists(con, "calendar_dates"):
        exc = con.execute(
            """
            SELECT service_id, exception_type
            FROM calendar_dates
            WHERE date = ?
            """,
            (dstr,),
        ).fetchall()
        for r in exc:
            sid = r["service_id"]
            et = int(r["exception_type"])
            if et == 1:
                active.add(sid)
            elif et == 2:
                active.discard(sid)

    return sorted(active)


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def db_info() -> dict:
    if not os.path.exists(DB_PATH):
        return {"ok": False, "db_path": DB_PATH, "error": "GTFS DB not found"}
    with _connect() as con:
        n_stops = con.execute("SELECT COUNT(*) AS n FROM stops").fetchone()["n"]
        n_routes = con.execute("SELECT COUNT(*) AS n FROM routes").fetchone()["n"]
        n_trips = con.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"]
        n_stop_times = con.execute("SELECT COUNT(*) AS n FROM stop_times").fetchone()["n"]
    return {
        "ok": True,
        "db_path": DB_PATH,
        "counts": {
            "stops": n_stops,
            "routes": n_routes,
            "trips": n_trips,
            "stop_times": n_stop_times,
        },
    }


def list_routes(limit: int = 500) -> list[dict]:
    """List routes in a shape compatible with the existing schedule API."""
    if not os.path.exists(DB_PATH):
        return []

    with _connect() as con:
        rows = con.execute(
            """
            SELECT route_id, route_short_name, route_long_name
            FROM routes
            ORDER BY CAST(route_short_name AS INTEGER) ASC, route_short_name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    out = []
    for r in rows:
        out.append(
            {
                # 'route_id' for the app = the public number riders use
                "route_id": (r["route_short_name"] or "").strip(),
                "route_name": (r["route_long_name"] or "").strip() or None,
                # keep GTFS internal route_id too, just in case
                "gtfs_route_id": r["route_id"],
            }
        )
    return out


def find_stops(q: str, limit: int = 25) -> list[dict]:
    if not q:
        return []
    if not os.path.exists(DB_PATH):
        return []

    with _connect() as con:
        rows = con.execute(
            """
            SELECT stop_id, stop_name
            FROM stops
            WHERE stop_name LIKE ?
            ORDER BY stop_name
            LIMIT ?
            """,
            (_like(q), int(limit)),
        ).fetchall()

    return [{"stop_id": r["stop_id"], "stop_name": r["stop_name"]} for r in rows]


def route_stops(
    route_id: str,
    service_id: str | None = None,  # kept for compatibility; not required
    q: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List distinct stops served by a route (route_short_name)."""
    if not route_id:
        return []
    if not os.path.exists(DB_PATH):
        return []

    with _connect() as con:
        params = [str(route_id)]
        where_q = ""
        if q:
            where_q = "AND s.stop_name LIKE ?"
            params.append(_like(q))

        params.append(int(limit))

        rows = con.execute(
            f"""
            SELECT DISTINCT s.stop_id, s.stop_name
            FROM routes r
            JOIN trips t ON t.route_id = r.route_id
            JOIN stop_times st ON st.trip_id = t.trip_id
            JOIN stops s ON s.stop_id = st.stop_id
            WHERE r.route_short_name = ?
            {where_q}
            ORDER BY s.stop_name
            LIMIT ?
            """,
            params,
        ).fetchall()

    return [{"stop_id": r["stop_id"], "stop_name": r["stop_name"]} for r in rows]


def next_departures_window(
    *,
    stop_code: str | None = None,
    stop_id: str | None = None,
    route_id: str | None = None,
    route_short_name: str | None = None,
    start_dt: datetime,
    end_dt: datetime,
    limit: int = 6,
) -> dict:
    """
    Return scheduled departures in a TIME WINDOW [start_dt, end_dt].

    This solves the common rider question:
      "schedule tomorrow 2pm stop 0473"
    which usually means "around 2pm" (not strictly after 2:00pm).

    Inputs:
      - stop_code: public stop number on sign (e.g. "0473") (preferred)
      - stop_id: GTFS internal stop_id (fallback)
      - route_short_name: public route number (e.g. "1") (optional filter)
      - start_dt/end_dt: window bounds in local time
      - limit: max rows returned

    Returns:
      {"rows": [ {departure_time, route_id, headsign, stop_id, service_date}, ... ]}
    """
    if not start_dt or not end_dt:
        return {"rows": []}
    if end_dt <= start_dt:
        return {"rows": []}
    if not os.path.exists(DB_PATH):
        return {"rows": []}

    # Normalize aliases
    route_id = (route_id or route_short_name or None)
    key_stop = (stop_code or stop_id or "").strip()
    if not key_stop:
        return {"rows": []}

    d_start = start_dt.date()
    d_end = end_dt.date()

    # Convert time-of-day to seconds since midnight
    start_sec = start_dt.hour * 3600 + start_dt.minute * 60 + start_dt.second
    end_sec = end_dt.hour * 3600 + end_dt.minute * 60 + end_dt.second

    with _connect() as con:
        # Resolve stop_code -> GTFS stop_id(s) when possible
        stop_ids: list[str] = []
        if stop_code:
            candidates = _canonical_stop_codes(stop_code)
            if candidates:
                in_codes = "(" + ",".join(["?"] * len(candidates)) + ")"
                rows = con.execute(
                    f"SELECT stop_id FROM stops WHERE stop_code IN {in_codes}",
                    candidates,
                ).fetchall()
                stop_ids = [r["stop_id"] for r in rows]

        # Fallback: treat provided value as stop_id directly
        if not stop_ids:
            stop_ids = _canonical_stop_codes(key_stop)

        if not stop_ids:
            return {"rows": []}

        # Collect candidate service dates to search:
        # - If window is same calendar day -> just that day
        # - If window crosses midnight -> search both days
        service_dates: list[date] = []
        service_dates.append(d_start)
        if d_end != d_start:
            service_dates.append(d_end)

        # Helper for safe IN (...)
        def in_clause(values: list[str]) -> tuple[str, list[str]]:
            if not values:
                return "(NULL)", []
            return "(" + ",".join(["?"] * len(values)) + ")", list(values)

        in_stops, p_stops = in_clause(stop_ids)

        where_route = ""
        p_route: list[str] = []
        if route_id:
            where_route = "AND r.route_short_name = ?"
            p_route.append(str(route_id))

        # Build UNION of per-day queries
        union_parts: list[str] = []
        params: list = []

        for sd in service_dates:
            sids = _active_service_ids(con, sd)
            if not sids:
                continue
            in_svc, p_svc = in_clause(sids)

            # Determine the window (seconds) we want on THIS service date
            # If sd is the start date -> [start_sec, end_of_day] or [start_sec, end_sec] if same day
            # If sd is the end date (cross-midnight case) -> [0, end_sec]
            if sd == d_start and sd == d_end:
                lo = start_sec
                hi = end_sec
            elif sd == d_start:
                lo = start_sec
                hi = 48 * 3600  # allow >24h times too
            else:
                lo = 0
                hi = end_sec

            union_parts.append(
                f"""
                SELECT st.departure_time AS departure_time,
                       r.route_short_name AS route_id,
                       t.trip_headsign AS headsign,
                       st.stop_id AS stop_id,
                       ? AS service_date,
                       st.dep_secs AS dep_secs
                FROM stop_times st
                JOIN trips t ON t.trip_id = st.trip_id
                JOIN routes r ON r.route_id = t.route_id
                WHERE st.stop_id IN {in_stops}
                  AND t.service_id IN {in_svc}
                  {where_route}
                  AND st.dep_secs >= ?
                  AND st.dep_secs <= ?
                """
            )

            # params for this part (match the SELECT placeholders order)
            params += [_ymd(sd)]
            params += p_stops + p_svc + p_route + [int(lo), int(hi)]

        if not union_parts:
            return {"rows": []}

        sql = f"""
        SELECT * FROM (
          {" UNION ALL ".join(union_parts)}
        )
        ORDER BY service_date, dep_secs
        LIMIT ?
        """
        params += [int(limit)]

        rows = con.execute(sql, params).fetchall()

    out = []
    for r in rows:
        out.append(
            {
                "departure_time": r["departure_time"],
                "route_id": r["route_id"],
                "headsign": r["headsign"],
                "stop_id": r["stop_id"],
                "service_date": r["service_date"],
            }
        )
    return {"rows": out}


def next_departures(
    *,
    stop_code: str | None = None,
    stop_id: str | None = None,
    route_id: str | None = None,
    route_short_name: str | None = None,
    when_dt: datetime,
    limit: int = 3,
    window_minutes: int = 180,
) -> dict:
    """
    Compatibility wrapper used by the agent: fetch the next N departures
    starting at when_dt within a reasonable window.
    """
    if not when_dt:
        return {"rows": []}
    end_dt = when_dt + timedelta(minutes=int(window_minutes))
    return next_departures_window(
        stop_code=stop_code,
        stop_id=stop_id,
        route_id=route_id,
        route_short_name=route_short_name,
        start_dt=when_dt,
        end_dt=end_dt,
        limit=limit,
    )
