import os
import re
import json
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import rts_api

# OpenAI is OPTIONAL (agent still works without it)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

TZ = ZoneInfo("America/New_York")


# ------------------------------------------------------------
# Basic helpers
# ------------------------------------------------------------
def normalize_stop_id(s: str) -> str | None:
    """
    Normalize a stop ID to 4 digits:
      '1' -> '0001'
      '01' -> '0001'
      '001' -> '0001'
      '0001' -> '0001'
      '1192' -> '1192'
    """
    if not s:
        return None
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return None
    if len(digits) > 4:
        digits = digits[-4:]
    return digits.zfill(4)


def digits_only(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")


def extract_route_id_regex(text: str) -> str | None:
    """
    Try to find route number in message.
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


def extract_stop_id_regex(text: str) -> str | None:
    """
    Extract stop ID from free text.
    Supports:
      "stop 1" / "stop id 1" / "stop #1" -> 0001
      "#1192" -> 1192
      Plain number ONLY if the entire message is digits:
        - 3-4 digits -> stop id (pad to 4)
        - 1-2 digits -> we DO NOT assume stop id (could be route)
    """
    t = (text or "").lower().strip()

    # Explicit stop patterns
    m = re.search(r"\bstop\s*(id)?\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(2))

    # Hashtag pattern (people paste #473 etc.)
    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # If the entire message is just digits, only accept if len>=3
    if re.fullmatch(r"[0-9]{1,6}", t):
        if len(t) >= 3:
            return normalize_stop_id(t)
        return None

    return None


def wants_schedule(text: str) -> bool:
    """
    Detect schedule intent, including common typos.
    """
    t = (text or "").lower()
    schedule_words = [
        "schedule", "sched", "schedual", "schedul", "timetable",
        "first bus", "first run", "last bus", "last run",
        "what time", "when does", "start", "end",
        "weekday", "weekdays", "mon-fri", "mon fri", "m/f", "m-f",
        # Spanish
        "horario", "tabla", "primero", "ultimo", "último", "a que hora", "a qué hora",
    ]
    return any(k in t for k in schedule_words)


def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    rt_words = [
        "eta", "minutes", "mins", "min", "prediction", "predictions", "arrive", "arrival",
        "next bus", "where is", "vehicle", "location", "real-time", "realtime",
        # Spanish
        "cuantos minutos", "cuántos minutos", "llega", "llegada", "en vivo", "tiempo real", "ubicacion", "ubicación",
    ]
    return any(k in t for k in rt_words)


def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "mins", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "sched", "schedual", "timetable", "first bus", "last bus",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada", "cuantos minutos", "tiempo real", "ubicacion", "ubicación",
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
    if "rosa" in t and "park" in t:
        return "Rosa Parks"
    if "uf" in t or "campus" in t:
        return "UF Campus"
    return None


def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


def detect_language_simple(text: str) -> str:
    # simple heuristic
    t = (text or "").lower()
    if any(w in t for w in ["hola", "horario", "ruta", "parada", "llega", "cuántos", "ubicación"]):
        return "es"
    return "en"


def parse_when_dt_from_message(msg: str) -> datetime:
    """
    Minimal time parsing:
      - "tomorrow" -> +1 day
      - time like "2pm", "2:15pm", "14:00"
    If no time found -> current time.
    """
    now = datetime.now(TZ)
    base = now

    t = (msg or "").lower()
    if "tomorrow" in t or "mañana" in t:
        base = (now + timedelta(days=1)).replace(hour=now.hour, minute=now.minute, second=0, microsecond=0)

    # time: 2pm / 2:30pm / 14:00
    m = re.search(r"\b([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm)?\b", t)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        ap = (m.group(3) or "").lower()

        if ap == "pm" and hh != 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0

        # clamp
        hh = max(0, min(23, hh))
        mm = max(0, min(59, mm))

        base = base.replace(hour=hh, minute=mm, second=0, microsecond=0)

    return base


# ------------------------------------------------------------
# OpenAI intent extraction (optional)
# ------------------------------------------------------------
def llm_extract_intent(message: str) -> dict:
    """
    If OPENAI_API_KEY is present and valid, use OpenAI to extract:
      intent, route_id, stop_id, destination_hint, language
    Otherwise return safe fallback.
    """
    fallback = {
        "intent": "general",
        "route_id": None,
        "stop_id": None,
        "destination_hint": None,
        "language": detect_language_simple(message),
    }

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return fallback

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "You extract transit intent for Gainesville RTS. "
        "Return ONLY JSON with keys: intent, route_id, stop_id, destination_hint, language. "
        "Rules: "
        "- intent is one of: eta, schedule, vehicle_location, general. "
        "- route_id is route number like '9' (string). "
        "- stop_id is 1-4 digit stop ID if provided; otherwise null. "
        "- destination_hint is a place name like 'Reitz Union' if mentioned. "
        "- language is 'es' if the user writes Spanish, else 'en'. "
        "- If unsure, intent='general'."
    )

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
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

    except Exception as e:
        # IMPORTANT: do not break the agent if OpenAI fails
        print("llm_extract_intent_error:", repr(e))
        print(traceback.format_exc())
        return fallback


# ------------------------------------------------------------
# Stop suggestions (schedule DB)
# ------------------------------------------------------------
def suggest_stops_by_route(route_id: str, message: str, limit: int = 8) -> list[dict]:
    hint = (guess_destination_hint(message) or "").strip()
    q = hint if hint else None

    try:
        stops = schedule_db.route_stops(route_id, service_id="mon_fri", q=q, limit=max(50, limit * 10))
    except Exception:
        stops = []

    # light scoring if no q
    if not q:
        t = (message or "").lower()
        scored = []
        for s in stops:
            name = (s.get("stop_name") or "").lower()
            score = 0
            for token in ["reitz", "hub", "downtown", "oaks", "butler", "campus", "uf", "rosa", "park"]:
                if token in t and token in name:
                    score += 2
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        stops = [s for _, s in scored] or stops

    out = []
    for s in stops[:limit]:
        out.append({"id": s.get("stop_id"), "name": s.get("stop_name")})
    return out


def suggest_stops_by_name(message: str, limit: int = 8) -> list[dict]:
    """
    If user says a landmark like "Rosa Parks" but no stop_id,
    try schedule_db.find_stops() and show top matches.
    """
    hint = guess_destination_hint(message) or ""
    q = hint.strip() or ""

    # If no known hint, try a cheap extraction: take longest word tokens
    if not q:
        # pick a couple meaningful words
        words = [w for w in re.findall(r"[a-zA-Z]{3,}", message or "") if w.lower() not in {"route", "stop", "bus", "eta"}]
        q = " ".join(words[:2]).strip()

    if not q:
        return []

    try:
        hits = schedule_db.find_stops(q, limit=limit)
    except Exception:
        hits = []

    out = []
    for h in hits[:limit]:
        out.append({"id": h.get("stop_id"), "name": h.get("stop_name")})
    return out


def fmt_stop_list(lang: str, title: str, candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        sid = c.get("id")
        nm = c.get("name") or ""
        if sid:
            lines.append(f"- Stop {sid}: {nm}".strip())

    if not lines:
        return tmsg(
            lang,
            "I still need the 4-digit Stop ID from the stop sign.",
            "Todavía necesito el Stop ID de 4 dígitos del letrero."
        )

    return tmsg(
        lang,
        f"{title}\nReply with ONE Stop ID:\n" + "\n".join(lines),
        f"{title}\nResponde con UN Stop ID:\n" + "\n".join(lines),
    )


# ------------------------------------------------------------
# Schedule queries (use schedule_db module)
# ------------------------------------------------------------
def schedule_next_departures(stop_id: str, route_id: str | None, when_dt: datetime, limit: int = 3) -> dict:
    """
    Delegate to db/schedule_db.py helper (if present),
    otherwise use stop_times table via schedule_db methods if you have them.
    """
    # You already have schedule_db in your project; it likely has this functionality.
    # We use schedule_db.next_departures if you created it; else we fallback to route_stops/last_departure_any approach.
    try:
        if hasattr(schedule_db, "next_departures"):
            return schedule_db.next_departures(stop_id=stop_id, route_id=route_id, when_dt=when_dt, limit=limit)
    except Exception:
        pass

    # Fallback: if no helper exists, return empty (so agent shows a friendly message)
    return {"rows": []}


def format_realtime_answer(lang: str, usable_preds: list[dict]) -> str:
    lines = []
    for p in usable_preds[:3]:
        mins = p.get("minutes")
        rt = p.get("route") or ""
        dest = p.get("destination") or ""
        if str(mins).upper() == "DUE":
            lines.append(tmsg(lang, f"Route {rt} to {dest}: DUE", f"Ruta {rt} hacia {dest}: YA"))
        else:
            lines.append(tmsg(lang, f"Route {rt} to {dest}: {mins} min", f"Ruta {rt} hacia {dest}: {mins} min"))

    return tmsg(lang, "Real-time ETA:\n- ", "ETA en tiempo real:\n- ") + "\n- ".join(lines)


# ------------------------------------------------------------
# Core agent logic
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
    route_id = extracted.get("route_id")
    stop_id = extracted.get("stop_id")
    destination_hint = (extracted.get("destination_hint") or "").strip()

    # Regex fallback if LLM didn't extract
    if intent == "general":
        if not route_id:
            route_id = extract_route_id_regex(msg)
        if not stop_id:
            stop_id = extract_stop_id_regex(msg)
        if not destination_hint:
            destination_hint = guess_destination_hint(msg) or ""

    # Decide schedule vs realtime
    prefer_schedule = (intent == "schedule") or (wants_schedule(msg) and not wants_realtime(msg))

    # If stop_id missing:
    if not stop_id:
        if route_id:
            candidates = suggest_stops_by_route(route_id, (destination_hint + " " + msg).strip(), limit=8)
            if candidates:
                return {
                    "answer": fmt_stop_list(
                        lang,
                        f"I can’t calculate without the boarding Stop ID. Here are Route {route_id} stops that match your message.",
                        candidates
                    ),
                    "sources": [{"type": "stop_suggestions", "route_id": route_id}],
                }

        # try name-only search (Rosa Parks, etc.)
        name_hits = suggest_stops_by_name(msg, limit=8)
        if name_hits:
            return {
                "answer": fmt_stop_list(
                    lang,
                    "I need the Stop ID. These stops match what you typed:",
                    name_hits
                ),
                "sources": [{"type": "stop_name_search"}],
            }

        return {
            "answer": tmsg(
                lang,
                "To check the next bus time, I need the Stop ID (the 4-digit number on the stop sign).",
                "Para verificar el próximo bus, necesito el Stop ID (el número de 4 dígitos en el letrero)."
            ),
            "sources": [{"type": "need_stop_id"}],
        }

    # If schedule requested, go schedule FIRST
    if prefer_schedule:
        when_dt = parse_when_dt_from_message(msg)
        result = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=when_dt, limit=3)

        rows = result.get("rows") or []
        if rows:
            lines = []
            for r in rows[:3]:
                dep = r.get("departure_time") or r.get("time") or ""
                rt = r.get("route_id") or r.get("route") or route_id or ""
                headsign = (r.get("headsign") or r.get("destination") or "").strip()
                if headsign:
                    lines.append(f"{dep} — Route {rt} ({headsign})")
                else:
                    lines.append(f"{dep} — Route {rt}")

            return {
                "answer": tmsg(
                    lang,
                    f"Scheduled times for Stop {stop_id} ({when_dt.strftime('%a %b %d %I:%M%p')}):\n- " + "\n- ".join(lines),
                    f"Horarios programados para Stop {stop_id} ({when_dt.strftime('%a %b %d %I:%M%p')}):\n- " + "\n- ".join(lines),
                ),
                "sources": [{"type": "schedule_db", "stop_id": stop_id, "route_id": route_id}],
            }

        return {
            "answer": tmsg(
                lang,
                f"I couldn’t find scheduled departures for Stop {stop_id} at that time. Try another stop ID.",
                f"No encontré horarios para Stop {stop_id} a esa hora. Prueba otra parada."
            ),
            "sources": [{"type": "schedule_db_none", "stop_id": stop_id, "route_id": route_id}],
        }

    # Otherwise REALTIME predictions
    predictions = []
    try:
        data = rts_api.get_predictions(stop_id)
        preds = data.get("prd", []) or []

        if route_id:
            preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

        for p in preds[:10]:
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

    # Keep <=45 min or DUE
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
        return {
            "answer": format_realtime_answer(lang, usable),
            "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
        }

    return {
        "answer": tmsg(
            lang,
            f"No real-time ETAs (<=45 min) found for Stop {stop_id}. Try another stop or ask for schedule.",
            f"No hay ETAs en tiempo real (<=45 min) para Stop {stop_id}. Prueba otra parada o pide el horario."
        ),
        "sources": [{"type": "realtime_none", "stop_id": stop_id, "route_id": route_id}],
    }


def handle_agent_message(message: str) -> dict:
    """
    This is the function your routes/agent_api.py imports.
    It MUST exist, or Render will crash on import.
    """
    transit = try_transit_answer(message)
    if transit:
        return {
            "answer": transit.get("answer", ""),
            "sources": transit.get("sources", []),
        }

    # Non-transit fallback (simple)
    return {
        "answer": "I can help with RTS ETAs and schedules. Try: 'ETA for Route 38 stop 1192' or 'schedule for Route 1 stop 0001 tomorrow 2pm'.",
        "sources": [{"type": "fallback"}],
    }
