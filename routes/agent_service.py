import os
import re
import json
import sqlite3
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

import rts_api
from config import API_KEY

from openai import OpenAI
client = OpenAI(api_key=API_KEY) if API_KEY else OpenAI()

from db import schedule_db
from utils.text_utils import normalize_stop_id, digits_only
from routes.stop_suggest_service import suggest_stops


TZ = ZoneInfo("America/New_York")


# ============================================================
# Text helpers
# ============================================================
def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "timetable", "first bus", "last bus", "tomorrow",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada", "cuantos minutos",
        "cuántos minutos", "tiempo real", "en vivo", "ubicacion", "ubicación", "mañana"
    ]
    return any(k in t for k in keywords)


def wants_schedule(text: str) -> bool:
    t = (text or "").lower()
    words = [
        "schedule", "timetable", "first bus", "first run", "last bus", "last run",
        "what time", "when does", "start", "end", "tomorrow", "around", "at ",
        # Spanish
        "horario", "tabla", "primero", "ultimo", "último", "a que hora", "a qué hora", "mañana", "como a"
    ]
    return any(w in t for w in words)


def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    words = [
        "eta", "minutes", "min", "prediction", "predictions", "arrive", "arrival",
        "next bus", "where is", "vehicle", "location", "real-time", "realtime",
        # Spanish
        "cuantos minutos", "cuántos minutos", "llega", "llegada", "en vivo", "tiempo real", "ubicacion", "ubicación"
    ]
    return any(w in t for w in words)


def extract_route_id(text: str) -> Optional[str]:
    """
    Extract route number from free text.
    Accepts: 'route 9', 'rt 21', 'bus 9', 'bus #9', 'route:12', 'bus number 9'
    """
    t = (text or "").lower()

    m = re.search(r"\b(route|rt|bus)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return digits_only(m.group(2)) or None

    m = re.search(r"\bbus\s*number\s*([0-9]{1,3})\b", t)
    if m:
        return digits_only(m.group(1)) or None

    return None


def extract_stop_id(text: str) -> Optional[str]:
    """
    Extract stop id from text.
    IMPORTANT: accepts 1..6 digits and normalizes to 4 digits:
      1 -> 0001, 01 -> 0001, 001 -> 0001, 0001 -> 0001, 1192 -> 1192
    """
    t = (text or "").lower().strip()

    # "stop 1" / "stop id 1" / "stop: 1192"
    m = re.search(r"\bstop(\s*id)?\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(2))

    # "#1192"
    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # If the whole message is just digits (common in chat)
    if re.fullmatch(r"[0-9]{1,6}", t):
        return normalize_stop_id(t)

    # Last resort: a standalone 4-digit number
    m = re.search(r"\b([0-9]{4})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    return None


def guess_destination_hint(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "reitz" in t:
        return "Reitz Union"
    if "oaks" in t:
        return "Oaks Mall"
    if "downtown" in t or "centro" in t:
        return "Downtown"
    if "hub" in t:
        return "Hub"
    if "uf" in t or "campus" in t or "universidad" in t:
        return "UF Campus"
    return None


# ============================================================
# LLM extraction (for nicer “human” understanding)
# ============================================================
def llm_extract_intent(text: str) -> Dict[str, Any]:
    """
    Uses OpenAI to extract:
      intent: eta | schedule | vehicle_location | general
      route_id: string digits or null
      stop_id: 4-digit string or null
      destination_hint: string or null
      language: en|es
    """
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "You extract transit intent for Gainesville RTS (bus). "
        "Return ONLY JSON with keys: intent, route_id, stop_id, destination_hint, language. "
        "Rules: "
        "- intent must be one of: eta, schedule, vehicle_location, general. "
        "- route_id is the route number like '9' (string) or null. "
        "- stop_id is the STOP SIGN number and can be 1-6 digits; if present normalize to 4 digits in your JSON (e.g., 1 -> 0001). Otherwise null. "
        "- destination_hint is a place name if mentioned (Reitz Union, UF, Downtown, Oaks Mall, Hub). Otherwise null. "
        "- language is 'es' if Spanish, else 'en'. "
        "- If unsure, intent='general'."
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        obj = json.loads(raw)

        intent = (obj.get("intent") or "general").strip()
        route_id = digits_only(obj.get("route_id") or "") or None
        stop_id = obj.get("stop_id")
        stop_id = normalize_stop_id(stop_id) if stop_id else None
        destination_hint = (obj.get("destination_hint") or "").strip() or None
        language = (obj.get("language") or "en").strip().lower()
        if language not in ("en", "es"):
            language = "en"

        return {
            "intent": intent,
            "route_id": route_id,
            "stop_id": stop_id,
            "destination_hint": destination_hint,
            "language": language,
        }
    except Exception as e:
        print("llm_extract_intent_error:", repr(e))
        print(traceback.format_exc())
        return {"intent": "general", "route_id": None, "stop_id": None, "destination_hint": None, "language": "en"}


# ============================================================
# Schedule DB access (next departures)
# ============================================================
def _open_sched_conn() -> sqlite3.Connection:
    info = schedule_db.db_info() or {}
    db_path = info.get("db_path") or os.environ.get("DB_PATH", "data/schedule.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _time_to_secs(dt: datetime) -> int:
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def _service_ids_for_date(conn: sqlite3.Connection, date_iso: str) -> List[str]:
    """
    Picks service_ids active on a given date, applying calendar_dates exceptions.
    date_iso: "YYYY-MM-DD"
    """
    dt = datetime.fromisoformat(date_iso)
    dow = dt.weekday()  # Mon=0 .. Sun=6
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


def schedule_next_departures(stop_id: str, route_id: Optional[str], when_dt: datetime, limit: int = 3) -> Dict[str, Any]:
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


# ============================================================
# Main agent logic (Realtime <=45 first, then Schedule)
# ============================================================
def answer_transit(message: str) -> Optional[Dict[str, Any]]:
    msg = (message or "").strip()
    if not msg:
        return None

    if not is_transit_keywords(msg):
        return None

    extracted = llm_extract_intent(msg)
    lang = extracted.get("language", "en")
    intent = extracted.get("intent", "general")
    route_id = extracted.get("route_id") or extract_route_id(msg)
    stop_id = extracted.get("stop_id") or extract_stop_id(msg)
    destination_hint = extracted.get("destination_hint") or guess_destination_hint(msg)

    # If user asks schedule clearly, do NOT use realtime
    prefer_schedule = (intent == "schedule") or (wants_schedule(msg) and not wants_realtime(msg))

    # If missing stop_id → suggest stops (Bustime-first from stop_suggest_service)
    if not stop_id:
        if route_id:
            candidates = suggest_stops(route_id, (destination_hint or "") + " " + msg, limit=8)
            if candidates:
                lines = []
                for c in candidates:
                    sid = c.get("id")
                    nm = (c.get("name") or "").strip()
                    if sid and nm:
                        lines.append(f"- {sid} — {nm}")
                    elif sid:
                        lines.append(f"- {sid}")

                return {
                    "answer": tmsg(
                        lang,
                        f"I can help — I just need the Stop ID (the 4-digit number on the stop sign). Here are likely stops for Route {route_id}. Reply with ONE stop number:\n"
                        + "\n".join(lines),
                        f"Puedo ayudarte — solo necesito el Stop ID (el número de 4 dígitos en la parada). Estas son paradas probables para la Ruta {route_id}. Responde con UN número:\n"
                        + "\n".join(lines),
                    ),
                    "sources": [{"type": "stop_suggestions", "route_id": route_id}],
                }

        return {
            "answer": tmsg(
                lang,
                "To check times, I need the Stop ID (the 4-digit number on the stop sign). Tell me your landmark (Reitz Union, UF, Downtown, Oaks Mall) and your route number if you know it.",
                "Para verificar los tiempos, necesito el Stop ID (el número de 4 dígitos de la parada). Dime un lugar cercano (Reitz Union, UF, Downtown, Oaks Mall) y el número de ruta si lo sabes.",
            ),
            "sources": [{"type": "need_stop_id"}],
        }

    # Normalize stop_id AGAIN for safety (handles 1/01/001/0001)
    stop_id = normalize_stop_id(stop_id)

    # -------------------------
    # 1) REALTIME (<=45 min)
    # -------------------------
    if not prefer_schedule:
        try:
            data = rts_api.get_predictions(stop_id)
            preds = data.get("prd", []) or []

            if route_id:
                preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

            cleaned = []
            for p in preds[:10]:
                cleaned.append({
                    "route": p.get("rt"),
                    "destination": p.get("des"),
                    "minutes": p.get("prdctdn"),
                    "arrival_time": p.get("prdtm"),
                    "vehicle_id": p.get("vid"),
                    "delayed": p.get("dly"),
                })

            usable = []
            for p in cleaned:
                m = p.get("minutes")
                if m is None:
                    continue
                if isinstance(m, str) and m.upper() == "DUE":
                    usable.append(p)
                    continue
                try:
                    mi = int(m)
                    if mi <= 45:
                        usable.append(p)
                except Exception:
                    pass

            if usable:
                lines = []
                for p in usable[:3]:
                    rt = p.get("route") or ""
                    dest = p.get("destination") or ""
                    mins = p.get("minutes")

                    if str(mins).upper() == "DUE":
                        lines.append(tmsg(lang, f"Route {rt} to {dest}: DUE", f"Ruta {rt} hacia {dest}: YA"))
                    else:
                        lines.append(tmsg(lang, f"Route {rt} to {dest}: {mins} min", f"Ruta {rt} hacia {dest}: {mins} min"))

                return {
                    "answer": tmsg(lang, "Real-time ETA:\n", "ETA en tiempo real:\n") + "- " + "\n- ".join(lines),
                    "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
                }
        except Exception as e:
            print("predictions_error:", repr(e))
            print(traceback.format_exc())
            # continue to schedule

    # -------------------------
    # 2) SCHEDULE FALLBACK
    # -------------------------
    now_dt = datetime.now(TZ)
    result = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=now_dt, limit=3)

    if result.get("rows"):
        stop_name = result["rows"][0].get("stop_name") or ""
        service_id = result.get("service_id") or ""
        lines = []
        for r in result["rows"]:
            rt = r.get("route_id")
            dep = r.get("departure_time")
            headsign = (r.get("headsign") or "").strip()
            if headsign:
                lines.append(f"{dep} — Route {rt} ({headsign})")
            else:
                lines.append(f"{dep} — Route {rt}")

        return {
            "answer": tmsg(
                lang,
                f"Next scheduled times for Stop {stop_id}"
                + (f" ({stop_name})" if stop_name else "")
                + (f" (service: {service_id})" if service_id else "")
                + ":\n- ",
                f"Próximos horarios para Stop {stop_id}"
                + (f" ({stop_name})" if stop_name else "")
                + (f" (servicio: {service_id})" if service_id else "")
                + ":\n- ",
            ) + "\n- ".join(lines),
            "sources": [{"type": "schedule_db", "stop_id": stop_id, "route_id": route_id, "service_id": service_id}],
        }

    return {
        "answer": tmsg(
            lang,
            f"I couldn’t find real-time ETAs (<=45 min) and I also couldn’t find schedule departures right now for Stop {stop_id}. Try another stop or tell me your landmark and I’ll suggest a better stop.",
            f"No pude encontrar ETAs (<=45 min) y tampoco horarios para Stop {stop_id} en este momento. Prueba otra parada o dime un lugar cercano y te sugiero una mejor parada.",
        ),
        "sources": [{"type": "none_found", "stop_id": stop_id, "route_id": route_id}],
    }
