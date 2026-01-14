import os
import re
import json
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from openai import OpenAI

import rts_api
import webqa
from config import API_KEY

from db import schedule_db


TZ = ZoneInfo("America/New_York")

client = OpenAI(api_key=API_KEY) if API_KEY else OpenAI()

# ----------------------------
# Normalizers / extractors
# ----------------------------
def digits_only(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")

def normalize_stop_id(s: str) -> str | None:
    """
    Accept stop "1", "01", "001", "0001" and normalize to "0001".
    Also accepts longer strings and keeps last 4 digits.
    """
    if not s:
        return None
    d = digits_only(s)
    if not d:
        return None
    if len(d) > 4:
        d = d[-4:]
    return d.zfill(4)

def extract_route_id_regex(text: str) -> str | None:
    t = (text or "").lower()

    # route 9 / rt 9 / bus 9 / bus #9 / route:12
    m = re.search(r"\b(route|rt|bus)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)

    # "bus number 9"
    m = re.search(r"\bbus\s*number\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(1)

    return None

def extract_stop_id_regex(text: str) -> str | None:
    t = (text or "").lower()

    # "stop 1192"
    m = re.search(r"\bstop\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # "#1192"
    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # last resort: any 1-4 digit number (so "stop 1" works)
    m = re.search(r"\b([0-9]{1,4})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    return None

def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "timetable", "first bus", "last bus",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada",
        "cuantos minutos", "cuántos minutos", "tiempo real",
        "ubicacion", "ubicación", "mañana"
    ]
    return any(k in t for k in keywords)

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
    return None

def detect_language(text: str) -> str:
    t = (text or "").lower()
    spanish_markers = ["hola", "mañana", "ruta", "parada", "horario", "cuántos", "a qué hora", "ubicación"]
    return "es" if any(w in t for w in spanish_markers) else "en"

def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


# ----------------------------
# Minimal time parsing (optional)
# tomorrow 10am, mañana 10am, etc.
# ----------------------------
def parse_when_dt(message: str) -> datetime:
    msg = (message or "").lower()
    base = datetime.now(TZ)

    # tomorrow / mañana
    if "tomorrow" in msg or "mañana" in msg:
        base = base + timedelta(days=1)

    # time like 10am / 10:30am / 15:20
    m = re.search(r"\b([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm)?\b", msg)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or "0")
        ap = (m.group(3) or "").lower()

        if ap == "pm" and hh < 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0

        # If user wrote 15:00 etc, ap is empty and hh is already 24h-style
        try:
            base = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except Exception:
            pass

    return base


# ----------------------------
# Schedule DB helpers
# ----------------------------
def _open_sched_conn():
    info = schedule_db.db_info()
    db_path = info.get("db_path") or os.environ.get("DB_PATH", "data/schedule.db")
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def _time_to_secs(dt: datetime) -> int:
    return dt.hour * 3600 + dt.minute * 60 + dt.second

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


# ----------------------------
# Stop suggestions (Bustime first!)
# ----------------------------
def suggest_stops(route_id: str, user_text: str, limit: int = 8) -> list[dict]:
    """
    Bustime stops are the BEST because they contain real numeric stop IDs.
    If bustime fails, we fallback to schedule_db, but we only keep numeric stop IDs.
    """
    t = (user_text or "").lower()
    hint = guess_destination_hint(user_text) or ""
    tokens = []
    for tk in ["reitz", "hub", "downtown", "oaks", "butler", "campus", "uf"]:
        if tk in t:
            tokens.append(tk)

    # Try bustime stops
    try:
        dirs = rts_api.get_directions(route_id).get("directions", []) or []
        dir_ids = []
        for d in dirs:
            dir_ids.append(d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d)
        dir_ids = [str(x) for x in dir_ids if x is not None]

        all_stops = []
        for d in dir_ids[:4]:
            st = rts_api.get_stops(route_id, d).get("stops", []) or []
            all_stops.extend(st)

        # score + filter
        scored = []
        for s in all_stops:
            sid = s.get("stpid")
            name = (s.get("stpnm") or "")
            if not sid:
                continue
            score = 0
            nm = name.lower()
            if hint and hint.lower() in nm:
                score += 3
            for tk in tokens:
                if tk in nm:
                    score += 2
            if score > 0:
                scored.append((score, {"id": normalize_stop_id(str(sid)) or str(sid), "name": name}))

        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        seen = set()
        for _, item in scored:
            sid = item["id"]
            if sid and sid not in seen:
                seen.add(sid)
                out.append(item)
            if len(out) >= limit:
                break

        if out:
            return out
    except Exception:
        pass

    # Fallback: schedule_db (ONLY numeric stop IDs)
    try:
        rows = schedule_db.route_stops(route_id, service_id="mon_fri", q=hint if hint else None, limit=250)
    except Exception:
        rows = []

    out = []
    for r in rows:
        sid_raw = r.get("stop_id")
        sid = normalize_stop_id(str(sid_raw)) if sid_raw is not None else None
        if not sid:
            continue
        out.append({"id": sid, "name": r.get("stop_name") or ""})
        if len(out) >= limit:
            break

    return out

def fmt_stop_list(lang: str, route_id: str, candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        sid = c.get("id")
        nm = (c.get("name") or "").strip()
        if sid:
            lines.append(f"- Stop {sid}: {nm}".strip())

    if not lines:
        return tmsg(
            lang,
            f"I found Route {route_id}, but I still need a Stop ID from the stop sign.",
            f"Encontré la Ruta {route_id}, pero todavía necesito el Stop ID del letrero."
        )

    return tmsg(
        lang,
        f"I can’t calculate ETA without the boarding Stop ID. Here are stops on Route {route_id} that match your message.\nReply with ONE Stop ID:\n" + "\n".join(lines),
        f"No puedo calcular el ETA sin el Stop ID de la parada. Estas paradas en la Ruta {route_id} coinciden con tu mensaje.\nResponde con UN Stop ID:\n" + "\n".join(lines),
    )


# ----------------------------
# LLM intent extraction
# ----------------------------
def llm_extract_intent(text: str) -> dict:
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "Extract transit intent for Gainesville RTS.\n"
        "Return ONLY JSON with keys: intent, route_id, stop_id, destination_hint, language.\n"
        "intent must be one of: eta, schedule, vehicle_location, general.\n"
        "route_id must be a route number like '9' (string) or null.\n"
        "stop_id must be digits like '1192' or '1' (string) or null.\n"
        "destination_hint can be 'Reitz Union' etc.\n"
        "language is 'es' if Spanish, else 'en'."
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
        stop_id = normalize_stop_id(obj.get("stop_id") or "") if obj.get("stop_id") else None
        destination_hint = (obj.get("destination_hint") or "").strip() or None
        language = (obj.get("language") or detect_language(text)).strip().lower()
        if language not in ("en", "es"):
            language = detect_language(text)

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
        return {
            "intent": "general",
            "route_id": None,
            "stop_id": None,
            "destination_hint": None,
            "language": detect_language(text),
        }


# ----------------------------
# Main handler
# ----------------------------
def handle_agent_message(message: str) -> dict:
    msg = (message or "").strip()
    if not msg:
        return {"answer": "", "sources": []}

    # If it doesn't look like transit, use webqa
    if not is_transit_keywords(msg):
        result = webqa.answer(msg)
        if isinstance(result, tuple):
            return {"answer": str(result[0]), "sources": list(result[1]) if len(result) > 1 else []}
        if isinstance(result, dict):
            return {"answer": str(result.get("answer") or result.get("text") or ""), "sources": result.get("sources") or []}
        return {"answer": str(result), "sources": []}

    extracted = llm_extract_intent(msg)
    lang = extracted.get("language", "en")
    intent = extracted.get("intent", "general")
    route_id = extracted.get("route_id")
    stop_id = extracted.get("stop_id")
    destination_hint = extracted.get("destination_hint") or (guess_destination_hint(msg) or "")

    # Regex fallback if LLM doesn't catch it
    if not route_id:
        route_id = extract_route_id_regex(msg)
    if not stop_id:
        stop_id = extract_stop_id_regex(msg)

    # Optional: support "tomorrow 10am"
    when_dt = parse_when_dt(msg)

    # Missing stop: suggest stops if route is known
    if not stop_id:
        if route_id:
            candidates = suggest_stops(route_id, (destination_hint + " " + msg).strip(), limit=8)
            if candidates:
                return {
                    "answer": fmt_stop_list(lang, route_id, candidates),
                    "sources": [{"type": "stop_suggestions", "route_id": route_id}],
                }

        return {
            "answer": tmsg(
                lang,
                "To check the next bus time, I need the Stop ID (the 4-digit number on the stop sign). If you tell me your location/landmark (Reitz Union, UF, Downtown, Oaks Mall), I can suggest the correct stop.",
                "Para verificar el próximo bus, necesito el Stop ID (el número de 4 dígitos en el letrero). Si me dices tu ubicación o un lugar cercano (Reitz Union, UF, Downtown, Oaks Mall), puedo sugerirte la parada correcta."
            ),
            "sources": [{"type": "need_stop_id"}],
        }

    # If user is asking schedule (and NOT realtime), skip realtime
    prefer_schedule = (intent == "schedule") or (wants_schedule(msg) and not wants_realtime(msg))

    # 1) Realtime predictions (<=45 min) if allowed
    if not prefer_schedule:
        predictions = []
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
        except Exception as e:
            print("predictions_error:", repr(e))
            print(traceback.format_exc())
            predictions = []

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

    # 2) Schedule fallback (uses schedule.db)
    result = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=when_dt, limit=3)

    if result.get("rows"):
        stop_name = (result["rows"][0].get("stop_name") or "").strip()
        service_id = result.get("service_id")

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
                    + f" — service {service_id}:\n- ",
                    f"No hay ETA en tiempo real (o es mayor de 45 min). Próximos horarios programados para Stop {stop_id}"
                    + (f" ({stop_name})" if stop_name else "")
                    + f" — servicio {service_id}:\n- "
                )
                + "\n- ".join(lines)
            ),
            "sources": [{"type": "schedule_db", "stop_id": stop_id, "route_id": route_id, "service_id": service_id}],
        }

    return {
        "answer": tmsg(
            lang,
            f"I couldn't find real-time ETAs (<=45 min) or scheduled departures for Stop {stop_id}. Try a different stop, or tell me your landmark and route.",
            f"No pude encontrar ETAs (<=45 min) ni horarios programados para Stop {stop_id}. Prueba otra parada o dime tu lugar y ruta."
        ),
        "sources": [{"type": "none_found", "stop_id": stop_id, "route_id": route_id}],
    }
