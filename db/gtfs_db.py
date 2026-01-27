"""GTFS-backed schedule database.

This module provides a small API surface the rest of the app can call.

It expects a SQLite database created by scripts/gtfs_ingest.py.

Key idea:
  - stops.stop_code should store the public stop number riders type (often 4 digits).
  - stops.stop_id is the internal GTFS stop_id used by stop_times.
  - stop_times.dep_secs stores seconds since service-day midnight.
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


def _canonical_stop_tokens(value: str) -> list[str]:
    """Return plausible stop strings to match (handles zero-padding).

    Examples:
      "0473" -> {"0473","473"}
      "473"  -> {"473","0473"}
    """
    raw = (value or "").strip()
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
    cols = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    return cols[d.weekday()]


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _active_service_ids(con: sqlite3.Connection, d: date) -> list[str]:
    """Return service_ids active on date d, honoring calendar_dates if present."""
    dstr = _ymd(d)
    col = _weekday_col(d)

    if not _table_exists(con, "calendar"):
        return []

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
            et = int(r["exception_type"] or 0)
            if et == 1:
                active.add(sid)
            elif et == 2:
                active.discard(sid)

    return sorted(active)


def _in_clause(values: list[str]) -> tuple[str, list[str]]:
    if not values:
        return "(NULL)", []
    return "(" + ",".join(["?"] * len(values)) + ")", list(values)


def _resolve_gtfs_stop_ids(
    con: sqlite3.Connection,
    *,
    stop_code: str | None,
    stop_id: str | None,
) -> list[str]:
    """Resolve to GTFS stop_id(s) that appear in stop_times.stop_id.

    Priority:
      1) If stop_code provided, try stops.stop_code
      2) Fallback to stops.stop_id direct match (common if feed doesn't use stop_code)
      3) Final fallback: treat provided token as stop_times.stop_id values
    """
    tokens = _canonical_stop_tokens(stop_code or stop_id or "")
    if not tokens:
        return []

    stop_ids: list[str] = []

    # If stops table exists, try mapping via stops table first
    if _table_exists(con, "stops"):
        # 1) stop_code mapping (best)
        if stop_code:
            # stop_code column may or may not exist depending on ingest version
            # We'll try it safely: if it errors, we skip.
            try:
                in_codes, p_codes = _in_clause(tokens)
                rows = con.execute(
                    f"SELECT stop_id FROM stops WHERE stop_code IN {in_codes}",
                    p_codes,
                ).fetchall()
                stop_ids.extend([r["stop_id"] for r in rows])
            except Exception:
                pass

        # 2) direct stop_id mapping via stops.stop_id
        if not stop_ids:
            in_ids, p_ids = _in_clause(tokens)
            rows = con.execute(
                f"SELECT stop_id FROM stops WHERE stop_id IN {in_ids}",
                p_ids,
            ).fetchall()
            stop_ids.extend([r["stop_id"] for r in rows])

    # 3) final fallback: assume tokens themselves are stop_times.stop_id values
    if not stop_ids:
        stop_ids = tokens

    # de-dupe
    seen = set()
    out = []
    for s in stop_ids:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def db_info() -> dict:
    if not os.path.exists(DB_PATH):
        return {"ok": False, "db_path": DB_PATH, "error": "GTFS DB not found"}
    with _connect() as con:
        n_stops = con.execute("SELECT COUNT(*) AS n FROM stops").fetchone()["n"] if _table_exists(con, "stops") else 0
        n_routes = con.execute("SELECT COUNT(*) AS n FROM routes").fetchone()["n"] if _table_exists(con, "routes") else 0
        n_trips = con.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"] if _table_exists(con, "trips") else 0
        n_stop_times = con.execute("SELECT COUNT(*) AS n FROM stop_times").fetchone()["n"] if _table_exists(con, "stop_times") else 0
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
    if not os.path.exists(DB_PATH):
        return []
    with _connect() as con:
        if not _table_exists(con, "routes"):
            return []
        rows = con.execute(
            """
            SELECT route_id, route_short_name, route_long_name
            FROM routes
            ORDER BY CAST(route_short_name AS INTEGER) ASC, route_short_name ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    return [
        {
            "route_id": (r["route_short_name"] or "").strip(),
            "route_name": (r["route_long_name"] or "").strip() or None,
            "gtfs_route_id": r["route_id"],
        }
        for r in rows
    ]


def find_stops(q: str, limit: int = 25) -> list[dict]:
    if not q or not os.path.exists(DB_PATH):
        return []
    with _connect() as con:
        if not _table_exists(con, "stops"):
            return []
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


def next_departures(
    *,
    # aliases supported:
    stop_code: str | None = None,   # public stop number (what riders type)
    stop_id: str | None = None,     # GTFS stop_id (internal)
    route_short_name: str | None = None,  # public route number (what riders use)
    route_id: str | None = None,          # alias
    when_dt: datetime,
    limit: int = 3,
) -> dict:
    """Return next scheduled departures.

    Returns:
      {"rows": [ {departure_time, route_id, headsign, stop_id, service_date}, ... ]}
    """
    if not when_dt or (not stop_code and not stop_id):
        return {"rows": []}
    if not os.path.exists(DB_PATH):
        return {"rows": []}

    # normalize route alias
    route_short_name = (route_short_name or route_id or None)
    key = stop_code or stop_id

    d_today = when_dt.date()
    d_yday = d_today - timedelta(days=1)
    sec_now = when_dt.hour * 3600 + when_dt.minute * 60 + when_dt.second

    with _connect() as con:
        if not _table_exists(con, "stop_times") or not _table_exists(con, "trips") or not _table_exists(con, "routes"):
            return {"rows": []}

        stop_ids = _resolve_gtfs_stop_ids(con, stop_code=stop_code, stop_id=stop_id)
        if not stop_ids:
            return {"rows": []}

        sids_today = _active_service_ids(con, d_today)
        sids_yday = _active_service_ids(con, d_yday)
        if not sids_today and not sids_yday:
            return {"rows": []}

        in_stops, p_stops = _in_clause(stop_ids)
        in_today, p_today = _in_clause(sids_today)
        in_yday, p_yday = _in_clause(sids_yday)

        where_route = ""
        p_route: list[str] = []
        if route_short_name:
            where_route = "AND r.route_short_name = ?"
            p_route.append(str(route_short_name))

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

        params: list = []
        params += [_ymd(d_today)]
        params += p_stops + p_today + p_route + [sec_now]
        params += [_ymd(d_yday)]
        params += p_stops + p_yday + p_route + [sec_now + 86400]
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
                "query": {"stop": key, "route": route_short_name},
            }
        )
    return {"rows": out}
