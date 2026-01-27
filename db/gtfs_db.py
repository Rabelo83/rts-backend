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


def next_departures(
    *,
    # Accept both names (so agent_service can call stop_code=xxxx):
    stop_code: str | None = None,
    stop_id: str | None = None,
    # Accept both route names:
    route_short_name: str | None = None,
    route_id: str | None = None,
    when_dt: datetime,
    limit: int = 3,
) -> dict:
    """
    Return next scheduled departures.

    Preferred input:
      - stop_code: the public stop number on the sign (e.g. "0473")

    Fallback input:
      - stop_id: GTFS internal stop_id (sometimes equals stop_code in some feeds)

    Returns:
      {"rows": [ {departure_time, route_id, headsign, stop_id, service_date}, ... ]}
    """
    if not when_dt:
        return {"rows": []}
    if not os.path.exists(DB_PATH):
        return {"rows": []}

    # Normalize aliases
    route_public = (route_id or route_short_name or None)

    key = (stop_code or stop_id or "").strip()
    if not key:
        return {"rows": []}

    d_today = when_dt.date()
    d_yday = d_today - timedelta(days=1)
    sec_now = when_dt.hour * 3600 + when_dt.minute * 60 + when_dt.second

    with _connect() as con:
        # 1) If stop_code provided, map it -> actual GTFS stop_id(s) using stops.stop_code (if available).
        stop_ids: list[str] = []
        if stop_code:
            codes = _canonical_stop_codes(stop_code)

            if codes:
                try:
                    # stops.stop_code exists only if ingest created it (our updated ingest does).
                    in_codes = "(" + ",".join(["?"] * len(codes)) + ")"
                    rows = con.execute(
                        f"SELECT stop_id FROM stops WHERE stop_code IN {in_codes}",
                        codes,
                    ).fetchall()
                    stop_ids = [r["stop_id"] for r in rows]
                except sqlite3.OperationalError:
                    # Older DB schema without stop_code column; ignore and fall back below.
                    stop_ids = []

        # 2) Fallback: treat provided key as stop_id directly (try variants)
        if not stop_ids:
            stop_ids = _canonical_stop_codes(key)

        if not stop_ids:
            return {"rows": []}

        sids_today = _active_service_ids(con, d_today)
        sids_yday = _active_service_ids(con, d_yday)

        # If calendar tables missing, we can't filter reliably; return empty.
        if not sids_today and not sids_yday:
            return {"rows": []}

        def in_clause(values: list[str]) -> tuple[str, list[str]]:
            if not values:
                return "(NULL)", []
            return "(" + ",".join(["?"] * len(values)) + ")", list(values)

        in_today, p_today = in_clause(sids_today)
        in_yday, p_yday = in_clause(sids_yday)
        in_stops, p_stops = in_clause(stop_ids)

        where_route = ""
        p_route: list[str] = []
        if route_public:
            where_route = "AND r.route_short_name = ?"
            p_route.append(str(route_public))

        # Today: dep_secs >= now
        q_today = f"""
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
              AND t.service_id IN {in_today}
              {where_route}
              AND st.dep_secs >= ?
        """

        # Yesterday: dep_secs >= (now + 86400) to catch 25:xx style times
        q_yday = f"""
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
              AND t.service_id IN {in_yday}
              {where_route}
              AND st.dep_secs >= ?
        """

        params = []
        # today query params
        params += [_ymd(d_today)]
        params += p_stops + p_today + p_route + [sec_now]
        # yesterday query params
        params += [_ymd(d_yday)]
        params += p_stops + p_yday + p_route + [sec_now + 86400]
        # limit
        params += [int(limit)]

        rows = con.execute(
            f"""
            SELECT * FROM (
              {q_today}
              UNION ALL
              {q_yday}
            )
            ORDER BY dep_secs
            LIMIT ?
            """,
            params,
        ).fetchall()

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
