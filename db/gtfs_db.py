import os
import sqlite3
from datetime import datetime

GTFS_DB_PATH = os.environ.get("GTFS_DB_PATH", "data/gtfs.db")


def _connect():
    return sqlite3.connect(GTFS_DB_PATH)


def _date_yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _secs_of_day(dt: datetime) -> int:
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def _weekday_col(dt: datetime) -> str:
    # Monday=0 ... Sunday=6
    cols = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    return cols[dt.weekday()]


def resolve_stop_id_by_code(stop_code: str) -> str | None:
    if not stop_code:
        return None

    code = str(stop_code).strip()
    # Try exact, then try without leading zeros
    variants = [code, code.lstrip("0") or "0"]

    con = _connect()
    cur = con.cursor()

    for v in variants:
        # Compare stop_code with and without leading zeros
        cur.execute(
            """
            SELECT stop_id
            FROM stops
            WHERE stop_code = ?
               OR ltrim(stop_code, '0') = ltrim(?, '0')
            LIMIT 1
            """,
            (v, v),
        )
        row = cur.fetchone()
        if row:
            con.close()
            return row[0]

    con.close()
    return None


def active_service_ids_for_date(dt: datetime) -> set[str]:
    d = _date_yyyymmdd(dt)
    dow = _weekday_col(dt)

    con = _connect()
    cur = con.cursor()

    service_ids = set()

    # Base services from calendar.txt (if present)
    try:
        cur.execute(
            f"""
            SELECT service_id
            FROM calendar
            WHERE {dow} = '1'
              AND start_date <= ?
              AND end_date >= ?
            """,
            (d, d),
        )
        service_ids.update([r[0] for r in cur.fetchall()])
    except Exception:
        pass

    # Apply exceptions from calendar_dates.txt (if present)
    try:
        cur.execute(
            """
            SELECT service_id, exception_type
            FROM calendar_dates
            WHERE date = ?
            """,
            (d,),
        )
        for sid, ex in cur.fetchall():
            # 1 = added service, 2 = removed service
            if str(ex) == "1":
                service_ids.add(sid)
            elif str(ex) == "2" and sid in service_ids:
                service_ids.remove(sid)
    except Exception:
        pass

    con.close()
    return service_ids


def next_departures(stop_code: str, route_short_name: str | None, when_dt: datetime, limit: int = 3) -> dict:
    """
    Return next scheduled departures at this stop_code after when_dt (local time).
    Filters to route_short_name if provided (e.g., '1').
    """
    stop_id = resolve_stop_id_by_code(stop_code)
    if not stop_id:
        return {"rows": []}

    service_ids = active_service_ids_for_date(when_dt)
    if not service_ids:
        # If GTFS uses frequencies only or missing calendars, this could be empty.
        # We return nothing rather than guessing.
        return {"rows": []}

    secs = _secs_of_day(when_dt)

    con = _connect()
    cur = con.cursor()

    # Build dynamic placeholders for service_ids
    sid_list = list(service_ids)
    sid_placeholders = ",".join(["?"] * len(sid_list))

    params = []
    sql = f"""
        SELECT st.departure_time, r.route_short_name, t.trip_headsign
        FROM stop_times st
        JOIN trips t ON t.trip_id = st.trip_id
        JOIN routes r ON r.route_id = t.route_id
        WHERE st.stop_id = ?
          AND st.dep_secs IS NOT NULL
          AND st.dep_secs >= ?
          AND t.service_id IN ({sid_placeholders})
    """
    params.extend([stop_id, secs])
    params.extend(sid_list)

    if route_short_name:
        sql += " AND r.route_short_name = ?"
        params.append(str(route_short_name))

    sql += " ORDER BY st.dep_secs ASC LIMIT ?"
    params.append(int(limit))

    cur.execute(sql, params)
    rows = cur.fetchall()
    con.close()

    out = []
    for dep_time, rshort, headsign in rows:
        out.append({
            "departure_time": dep_time,
            "route_id": rshort,
            "headsign": headsign or "",
        })

    return {"rows": out}
