import os
import re
import json
import sqlite3
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import rts_api
import webqa
from db import schedule_db

TZ = ZoneInfo("America/New_York")


# ------------------------------------------------------------
# Text + ID helpers
# ------------------------------------------------------------
def digits_only(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")


def normalize_stop_id(s: str) -> str | None:
    """
    Normalize a stop ID to 4 digits:
    - "1" -> "0001"
    - "01" -> "0001"
    - "001" -> "0001"
    - "0001" -> "0001"
    - "1192" -> "1192"
    """
    if not s:
        return None
    d = digits_only(s)
    if not d:
        return None
    if len(d) > 4:
        d = d[-4:]
    return d.zfill(4)


def extract_route_id(text: str) -> str | None:
    """
    Find route number from text like:
    'route 9', 'rt 21', 'bus 9', 'bus #9', 'route:12'
    """
    t = (text or "").lower()
    m = re.search(r"\b(route|rt|bus)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)
    return None


def extract_stop_id(text: str) -> str | None:
    """
    Extract stop ID from text.
    Important: accept 1-4 digits after stop keywords.
    Examples:
    - "stop 1" -> 0001
    - "stop id 01" -> 0001
    - "#001" -> 0001
    - message is ONLY "1" or "0001" -> treat as stop
    """
    t = (text or "").strip().lower()
    if not t:
        return None

    # If message is ONLY digits (1-4), treat as a stop id
    if re.fullmatch(r"[0-9]{1,4}", t):
        return normalize_stop_id(t)

    # stop id / stop / stp patterns
    m = re.search(r"\b(stop\s*id|stop)\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(2))

    # hash format "#1", "#001"
    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # last resort: if they wrote "at 0001" etc. (only if we see the word "stop")
    if "stop" in t:
        m = re.search(r"\b([0-9]{1,4})\b", t)
        if m:
            return normalize_stop_id(m.group(1))

    return None


def wants_schedule(text: str) -> bool:
    t = (text or "").lower()
    schedule_words = [
        "schedule", "timetable", "first bus", "first run", "last bus", "last run",
        "what time", "when does", "start", "end",
        # Spanish
        "horario", "tabla", "primero", "ultimo", "último", "a que hora", "a qué hora"
    ]
    return any(k in t for k in schedule_words)


def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    rt_words = [
        "eta", "minutes", "min", "prediction", "predictions", "arrive", "arrival",
        "next bus", "where is", "vehicle", "location", "real-time", "realtime",
        # Spanish
        "cuantos minutos", "cuántos minutos", "llega", "llegada", "en vivo", "tiempo real",
        "ubicacion", "ubicación"
    ]
    return any(k in t for k in rt_words)


def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "timetable", "first bus", "last bus",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada", "cuantos minutos", "tiempo real",
        "ubicacion", "ubicación"
    ]
    return any(k in t for k in keywords)


def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


def detect_language(text: str) -> str:
    t = (text or "").lower()
    # super simple heuristic
    if any(w in t for w in [" horario", "ruta", "parada", "llega", "ubicación", "cuántos"]):
        return "es"
    return "en"


# ------------------------------------------------------------
# Schedule DB querying (direct SQL on schedule.db)
# ------------------------------------------------------------
def _open_sched_conn():
    info = schedule_db.db_info()
    db_path = info.get("db_path") or os.environ.get("DB_PATH", "data/schedule.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _service_ids_for_date(conn, date_iso: str) -> list[str]:
    dt = datetime.fromisoformat(date_iso)
    dow = dt.weekday()  # Mon=0..Sun=6
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


def _time_to_secs(dt: datetime) -> int:
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def schedule_next_departures(stop_id: str, route_id: str | None, when_dt: datetime, limit: int = 3):
    date_iso = when_dt.date().isoformat()
    now_secs = _time_to_secs(when_dt)

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
                    (stop_id, route_id, sid, now_secs, limit),
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
                    (stop_id, sid, now_secs, limit),
                ).fetchall()

            if rows:
                return {"date": date_iso, "service_id": sid, "rows": [dict(r) for r in rows]}

        return {"date": date_iso, "service_ids": service_ids, "rows": []}


# ------------------------------------------------------------
# Core Transit answer
# ------------------------------------------------------------
def try_transit_answer(message: str) -> dict | None:
    msg = (message or "").strip()
    if not msg:
        return None

    if not is_transit_keywords(msg) and not re.fullmatch(r"[0-9]{1,4}", msg.strip()):
        return None

    lang = detect_language(msg)

    # parse ids (no OpenAI needed)
    route_id = extract_route_id(msg)
    stop_id = extract_stop_id(msg)

    # if user only typed a stop id, treat it as realtime predictions for that stop
    if stop_id and not route_id and re.fullmatch(r"[0-9]{1,4}", msg.strip()):
        route_id = None

    if not stop_id:
        return {
            "answer": tmsg(
                lang,
                "To check bus times, I need the Stop ID (the 4-digit number on the stop sign). Example: 1192.",
                "Para verificar horarios, necesito el Stop ID (el número de 4 dígitos en el letrero). Ejemplo: 1192."
            ),
            "sources": [{"type": "need_stop_id"}],
        }

    # prefer schedule if they asked schedule words (and NOT realtime words)
    prefer_schedule = wants_schedule(msg) and not wants_realtime(msg)

    # 1) Realtime predictions (only if not prefer_schedule)
    if not prefer_schedule:
        try:
            data = rts_api.get_predictions(stop_id)
            preds = data.get("prd", []) or []

            if route_id:
                preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

            if preds:
                lines = []
                sources = []
                for p in preds[:3]:
                    rt = p.get("rt")
                    des = p.get("des")
                    mins = p.get("prdctdn")
                    lines.append(f"- Route {rt} to {des}: {mins} min" if str(mins).isdigit() else f"- Route {rt} to {des}: {mins}")
                if route_id:
                    sources.append({"type": "realtime", "route_id": str(route_id), "stop_id": stop_id})
                else:
                    sources.append({"type": "realtime", "stop_id": stop_id})

                return {
                    "answer": tmsg(lang, "Real-time ETA:\n", "ETA en tiempo real:\n") + "\n".join(lines),
                    "sources": sources,
                }
        except Exception as e:
            print("predictions_error:", repr(e))
            print(traceback.format_exc())

    # 2) Schedule fallback (from schedule.db)
    try:
        now_dt = datetime.now(TZ)
        result = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=now_dt, limit=3)

        if result.get("rows"):
            stop_name = result["rows"][0].get("stop_name") or ""
            service_id = result.get("service_id") or ""
            day_label = _day_label(now_dt)

            lines = []
            for r in result["rows"]:
                rt = r.get("route_id")
                dep = r.get("departure_time")
                headsign = (r.get("headsign") or "").strip()
                if headsign:
                    lines.append(f"{dep} — Route {rt} ({headsign})")
                else:
                    lines.append(f"{dep} — Route {rt}")

            header = tmsg(
                lang,
                f"Next scheduled times for Stop {stop_id}" + (f" ({stop_name})" if stop_name else "") + f" — {day_label} ({service_id}):\n",
                f"Próximos horarios programados para Stop {stop_id}" + (f" ({stop_name})" if stop_name else "") + f" — {day_label} ({service_id}):\n",
            )
            return {
                "answer": header + "- " + "\n- ".join(lines),
                "sources": [{"type": "schedule_db", "stop_id": stop_id, "route_id": route_id, "service_id": service_id}],
            }
    except Exception as e:
        print("schedule_fallback_error:", repr(e))
        print(traceback.format_exc())

    return {
        "answer": tmsg(
            lang,
            f"I couldn't find real-time ETAs or scheduled departures right now for Stop {stop_id}. Try another stop.",
            f"No pude encontrar ETAs en tiempo real ni horarios programados ahora para Stop {stop_id}. Prueba otra parada."
        ),
        "sources": [{"type": "none_found", "stop_id": stop_id, "route_id": route_id}],
    }


# ------------------------------------------------------------
# This is what routes/agent_api.py imports
# ------------------------------------------------------------
def handle_agent_message(message: str) -> dict:
    """
    Main handler used by /api/agent.
    1) Try transit answer (realtime/schedule)
    2) Else fallback to webqa (if available)
    """
    transit = try_transit_answer(message)
    if transit:
        return {"answer": transit.get("answer", ""), "sources": transit.get("sources", [])}

    # fallback to web Q&A
    try:
        result = webqa.answer(message)

        if isinstance(result, tuple):
            answer = str(result[0]) if len(result) > 0 else ""
            sources = list(result[1]) if (len(result) > 1 and isinstance(result[1], (list, tuple))) else []
            return {"answer": answer, "sources": sources}

        if isinstance(result, dict):
            answer = str(result.get("answer") or result.get("text") or "")
            src = result.get("sources") or result.get("citations") or []
            sources = list(src) if isinstance(src, (list, tuple)) else []
            return {"answer": answer, "sources": sources}

        return {"answer": str(result), "sources": []}

    except Exception as e:
        print("webqa_error:", repr(e))
        print(traceback.format_exc())
        return {
            "answer": "Sorry — the assistant had an internal error.",
            "sources": [{"type": "error", "detail": str(e)}],
        }
