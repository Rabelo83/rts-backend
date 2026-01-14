import os
import re
import json
import sqlite3
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI

import rts_api
from db import schedule_db

TZ = ZoneInfo("America/New_York")

# OpenAI key must be separate from Bustime key.
# Set it in Render as environment variable OPENAI_API_KEY.
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip() or None
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ------------------------------------------------------------
# Basic helpers
# ------------------------------------------------------------
def digits_only(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")


def normalize_stop_id(s: str | None) -> str | None:
    """
    Normalize stop ID to 4 digits:
      "1" -> "0001"
      "01" -> "0001"
      "001" -> "0001"
      "0001" -> "0001"
      "1192" -> "1192"
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
    Recognizes: "route 9", "rt 21", "bus 9", "bus #9", "route:12", "bus number 9"
    """
    t = (text or "").lower()

    m = re.search(r"\b(route|rt|bus)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)

    m = re.search(r"\bbus\s*number\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(1)

    return None


def extract_stop_id(text: str) -> str | None:
    """Prefers patterns like 'stop 1192', but also supports bare numbers."""
    t = (text or "").lower()

    m = re.search(r"\bstop\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # Last resort: any 1–4 digit number
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
        "cuantos minutos", "cuántos minutos", "llega", "llegada",
        "en vivo", "tiempo real", "ubicacion", "ubicación"
    ]
    return any(k in t for k in rt_words)


def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "timetable", "first bus", "last bus",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada",
        "cuantos minutos", "cuántos minutos", "tiempo real", "ubicacion", "ubicación"
    ]
    return any(k in t for k in keywords)


def guess_destination_hint(text: str) -> str | None:
    t = (text or "").lower()
    if "reitz" in t:
        return "Reitz Union"
    if "oaks" in t:
        return "Oaks Mall"
    if "downtown" in t:
        return "Downtown"
    if "hub" in t:
        return "Hub"
    if "uf" in t or "campus" in t:
        return "UF Campus"
    if "rosa" in t or "parks" in t:
        return "Rosa Parks"
    return None


def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


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
# Agent helpers (LLM extraction + stop suggestions)
# ------------------------------------------------------------
def llm_extract_intent(text: str) -> dict:
    """
    Use OpenAI to extract intent + IDs.
    If OpenAI is missing/invalid, return safe defaults (no error spam).
    """
    # If OpenAI is not configured, fall back to regex heuristics.
    if client is None:
        return {"intent": "general", "route_id": None, "stop_id": None, "destination_hint": None, "language": "en"}

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "You extract transit intent for Gainesville RTS. "
        "Return ONLY JSON with keys: intent, route_id, stop_id, destination_hint, language. "
        "Rules: "
        "- intent is one of: eta, schedule, vehicle_location, general. "
        "- route_id is route number like '9' (string). "
        "- stop_id is 4-digit stop ID if provided; otherwise null. "
        "- destination_hint is a place name like 'Reitz Union' if mentioned. "
        "- language is 'es' if the user writes Spanish, else 'en'. "
        "- If unsure, intent='general'."
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        obj = json.loads(raw)

        intent = (obj.get("intent") or "general").strip()
        route_id = digits_only(obj.get("route_id") or "") or None
        stop_id = normalize_stop_id(obj.get("stop_id") or "") if obj.get("stop_id") else None
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
    except Exception:
        # IMPORTANT: don't spam stack traces in production for auth errors
        return {"intent": "general", "route_id": None, "stop_id": None, "destination_hint": None, "language": "en"}


def suggest_stops(route_id: str, text: str, limit: int = 8) -> list[dict]:
    hint = (guess_destination_hint(text) or "").strip()
    q = hint if hint else None

    try:
        stops = schedule_db.route_stops(route_id, service_id="mon_fri", q=q, limit=max(50, limit * 10))
    except Exception:
        stops = []

    out = []
    for s in stops[:limit]:
        out.append({"id": s.get("stop_id"), "name": s.get("stop_name")})
    return out


def fmt_stop_list(lang: str, route_id: str, candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        sid = c.get("id")
        nm = c.get("name") or ""
        sid_norm = normalize_stop_id(sid)  # <-- normalize here too
        if sid_norm:
            lines.append(f"- Stop {sid_norm}: {nm}".strip())

    if not lines:
        return tmsg(
            lang,
            f"I found Route {route_id}, but I still need a Stop ID from the stop sign.",
            f"Encontré la Ruta {route_id}, pero todavía necesito el Stop ID del letrero."
        )

    return tmsg(
        lang,
        "I can’t calculate ETA without the boarding Stop ID. Here are stops on the route that match your message.\n"
        "Reply with ONE Stop ID:\n" + "\n".join(lines),
        "No puedo calcular el ETA sin el Stop ID de la parada. Estas paradas coinciden con tu mensaje.\n"
        "Responde con UN Stop ID:\n" + "\n".join(lines),
    )


# ------------------------------------------------------------
# Agent: REALTIME (<=45 min) first, then SCHEDULE
# ------------------------------------------------------------
def try_transit_answer(message: str) -> dict | None:
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
    destination_hint = (extracted.get("destination_hint") or "").strip() or (guess_destination_hint(msg) or "")

    # ALWAYS normalize stop_id if we got anything
    stop_id = normalize_stop_id(stop_id)

    # If missing stop_id, suggest stops
    if not stop_id:
        if route_id:
            candidates = suggest_stops(route_id, (destination_hint + " " + msg).strip(), limit=8)
            if candidates:
                return {"answer": fmt_stop_list(lang, route_id, candidates), "sources": [{"type": "stop_suggestions", "route_id": route_id}]}

        return {
            "answer": tmsg(
                lang,
                "To check the next bus time, I need the Stop ID (the 4-digit number on the stop sign). If you tell me your location/landmark (Reitz Union, UF, Downtown, Oaks Mall), I can suggest the correct stop.",
                "Para verificar el próximo bus, necesito el Stop ID (el número de 4 dígitos en el letrero). Si me dices tu ubicación o un lugar cercano (Reitz Union, UF, Downtown, Oaks Mall), puedo sugerirte la parada correcta."
            ),
            "sources": [{"type": "need_stop_id"}],
        }

    # If schedule is explicitly requested and realtime isn't, go straight to schedule
    prefer_schedule = (intent == "schedule") or (wants_schedule(msg) and not wants_realtime(msg))

    # 1) Realtime predictions (<=45 min) only if not prefer_schedule
    predictions = []
    if not prefer_schedule:
        try:
            data = rts_api.get_predictions(stop_id)
            preds = data.get("prd", [])

            if route_id:
                preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

            for p in preds[:8]:
                predictions.append({
                    "route": p.get("rt"),
                    "destination": p.get("des"),
                    "minutes": p.get("prdctdn"),
                    "arrival_time": p.get("prdtm"),
                    "vehicle_id": p.get("vid"),
                    "delayed": p.get("dly"),
                })
        except Exception:
            pass

        usable = []
        for p in predictions:
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
                mins = p.get("minutes")
                rt = p.get("route") or ""
                dest = p.get("destination") or ""
                if str(mins).upper() == "DUE":
                    lines.append(tmsg(lang, f"Route {rt} to {dest}: DUE", f"Ruta {rt} hacia {dest}: YA"))
                else:
                    lines.append(tmsg(lang, f"Route {rt} to {dest}: {mins} min", f"Ruta {rt} hacia {dest}: {mins} min"))

            return {
                "answer": tmsg(lang, "Real-time ETA:\n- ", "ETA en tiempo real:\n- ") + "\n- ".join(lines),
                "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
            }

    # 2) Schedule fallback
    now_dt = datetime.now(TZ)
    result = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=now_dt, limit=3)

    if result.get("rows"):
        stop_name = result["rows"][0].get("stop_name")
        service_id = result.get("service_id")
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

        return {
            "answer": (
                tmsg(
                    lang,
                    f"No real-time ETA available (or it’s over 45 minutes). Next scheduled times for Stop {stop_id}"
                    + (f" ({stop_name})" if stop_name else "")
                    + f" — {day_label} ({service_id}):\n- ",
                    f"No hay ETA en tiempo real (o es mayor de 45 min). Próximos horarios programados para Stop {stop_id}"
                    + (f" ({stop_name})" if stop_name else "")
                    + f" — {day_label} ({service_id}):\n- "
                )
                + "\n- ".join(lines)
            ),
            "sources": [{"type": "schedule_db", "stop_id": stop_id, "route_id": route_id, "service_id": service_id}],
        }

    return {
        "answer": tmsg(
            lang,
            f"I couldn't find real-time ETAs (<=45 min) or scheduled departures right now for Stop {stop_id}. Try another stop or tell me your location.",
            f"No pude encontrar ETAs (<=45 min) ni horarios programados en este momento para Stop {stop_id}. Prueba otra parada o dime tu ubicación."
        ),
        "sources": [{"type": "none_found", "stop_id": stop_id, "route_id": route_id}],
    }
