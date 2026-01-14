import os, sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from db import schedule_db
from services.stop_suggest_service import get_stop_name, find_best_stop_for_destination
from utils.time_utils import time_to_secs
from utils.text_utils import tmsg

TZ = ZoneInfo("America/New_York")

def _open_sched_conn():
    info = schedule_db.db_info()
    db_path = info.get("db_path") or os.environ.get("DB_PATH", "data/schedule.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def _service_ids_for_date(conn, date_iso: str) -> list[str]:
    dt = datetime.fromisoformat(date_iso)
    dow = dt.weekday()
    dow_col = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][dow]

    base = conn.execute(
        f"""
        SELECT service_id
        FROM calendar
        WHERE start_date <= ? AND end_date >= ? AND {dow_col} = 1
        """,
        (date_iso, date_iso),
    ).fetchall()
    service_ids = {r["service_id"] for r in base}

    ex = conn.execute(
        """
        SELECT service_id, exception_type
        FROM calendar_dates
        WHERE date = ?
        """,
        (date_iso,),
    ).fetchall()

    for r in ex:
        sid = r["service_id"]
        et = int(r["exception_type"])
        if et == 2 and sid in service_ids:
            service_ids.remove(sid)
        elif et == 1:
            service_ids.add(sid)

    return sorted(service_ids)

def _day_label(dt: datetime) -> str:
    if dt.weekday() <= 4:
        return "Weekday"
    if dt.weekday() == 5:
        return "Saturday"
    return "Sunday"

def schedule_next_departures(schedule_stop_id: str, route_id: str | None, when_dt: datetime, limit: int = 3):
    date_iso = when_dt.date().isoformat()
    now_secs = time_to_secs(when_dt)

    with _open_sched_conn() as conn:
        service_ids = _service_ids_for_date(conn, date_iso)
        if not service_ids:
            return {"date": date_iso, "service_ids": [], "rows": []}

        for sid in service_ids:
            if route_id:
                rows = conn.execute(
                    """
                    SELECT st.route_id, st.stop_id, s.stop_name, t.headsign,
                           st.departure_time, st.departure_secs
                    FROM stop_times st
                    JOIN trips t ON t.trip_id = st.trip_id
                    JOIN stops s ON s.stop_id = st.stop_id
                    WHERE st.stop_id = ?
                      AND st.route_id = ?
                      AND t.service_id = ?
                      AND st.departure_secs >= ?
                    ORDER BY st.departure_secs ASC
                    LIMIT ?
                    """,
                    (schedule_stop_id, route_id, sid, now_secs, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT st.route_id, st.stop_id, s.stop_name, t.headsign,
                           st.departure_time, st.departure_secs
                    FROM stop_times st
                    JOIN trips t ON t.trip_id = st.trip_id
                    JOIN stops s ON s.stop_id = st.stop_id
                    WHERE st.stop_id = ?
                      AND t.service_id = ?
                      AND st.departure_secs >= ?
                    ORDER BY st.departure_secs ASC
                    LIMIT ?
                    """,
                    (schedule_stop_id, sid, now_secs, limit),
                ).fetchall()

            if rows:
                return {"date": date_iso, "service_id": sid, "rows": [dict(r) for r in rows]}

        return {"date": date_iso, "service_ids": service_ids, "rows": []}

def schedule_next_departures_by_bustime_stop(route_id: str | None, bustime_stop_id: str, when_dt: datetime, lang: str):
    # Get stop name from Bustime
    stop_name = get_stop_name(route_id or "", bustime_stop_id) if route_id else None

    # Find schedule stop(s) by name search
    candidates = schedule_db.find_stops(stop_name or "", limit=5) if stop_name else []
    if not candidates:
        # fallback: try search by "Reitz" etc from stop_name if exists
        if stop_name:
            short = stop_name.split("/")[0]
            candidates = schedule_db.find_stops(short, limit=5)

    if not candidates:
        return {
            "answer": tmsg(
                lang,
                f"I couldn’t find schedule entries that match Stop {bustime_stop_id}. (Schedule DB uses different stop IDs.)",
                f"No pude encontrar horarios que coincidan con Stop {bustime_stop_id}. (La base de datos usa IDs diferentes.)"
            ),
            "sources": [{"type": "schedule_db_no_match", "stop_id": bustime_stop_id}],
        }

    schedule_stop_id = candidates[0].get("stop_id")
    result = schedule_next_departures(schedule_stop_id, route_id, when_dt, limit=3)

    if not result.get("rows"):
        return {
            "answer": tmsg(
                lang,
                f"I couldn't find scheduled departures right now for Stop {bustime_stop_id} ({stop_name or ''}).",
                f"No pude encontrar salidas programadas ahora para Stop {bustime_stop_id} ({stop_name or ''})."
            ),
            "sources": [{"type": "schedule_db_none", "stop_id": bustime_stop_id, "route_id": route_id}],
        }

    rows = result["rows"]
    lines = []
    for r in rows:
        dep = r.get("departure_time")
        rt = r.get("route_id")
        headsign = (r.get("headsign") or "").strip()
        if headsign:
            lines.append(f"{dep} — Route {rt} ({headsign})")
        else:
            lines.append(f"{dep} — Route {rt}")

    return {
        "answer": (
            tmsg(
                lang,
                f"No real-time ETA available (or it’s over 45 min). Next scheduled times near '{stop_name}' (matched in schedule DB):\n- ",
                f"No hay ETA en tiempo real (o es mayor de 45 min). Próximos horarios cerca de '{stop_name}' (coincidencia en BD):\n- "
            ) + "\n- ".join(lines)
        ),
        "sources": [{"type": "schedule_db", "matched_stop_name": stop_name, "service_id": result.get("service_id")}],
    }

def schedule_window_by_destination(route_id: str, destination_hint: str, when_dt: datetime, lang: str):
    # Find a likely Bustime stop near destination, then bridge to schedule
    best = find_best_stop_for_destination(route_id, destination_hint)
    if not best:
        return {
            "answer": tmsg(
                lang,
                f"I couldn’t find a stop on Route {route_id} that matches '{destination_hint}'. Try a different landmark.",
                f"No pude encontrar una parada en la Ruta {route_id} que coincida con '{destination_hint}'. Prueba otro lugar."
            ),
            "sources": [{"type": "no_stop_match", "route_id": route_id}],
        }

    # For now: just return schedule next departures at that stop/time
    bustime_stop_id = str(best.get("id"))
    return schedule_next_departures_by_bustime_stop(route_id, bustime_stop_id, when_dt, lang)
