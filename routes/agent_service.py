import os
import re
import json
import traceback
import sys
from pathlib import Path
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
# Optional Backend Basics schedule engine
# ------------------------------------------------------------
BACKEND_BASICS_AVAILABLE = False
BB_ANSWER_FN = None
try:
    # Repo root is one level above routes/
    backend_basics_db = Path(__file__).resolve().parents[1] / "Backend Basics" / "db"
    if backend_basics_db.exists():
        sys.path.insert(0, str(backend_basics_db))
        import answering_layer as _bb_answering_layer

        BB_ANSWER_FN = _bb_answering_layer.answer_question
        BACKEND_BASICS_AVAILABLE = True
except Exception as e:
    print("backend_basics_import_error:", repr(e))


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
    Stop ID extraction:
      "stop 473" -> 0473
      "#473" -> 0473
      digits-only message is handled separately in try_transit_answer()
    """
    t = (text or "").lower().strip()

    m = re.search(r"\bstop\s*(id)?\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(2))

    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    return None


def wants_schedule(text: str) -> bool:
    t = (text or "").lower()
    schedule_words = [
        "schedule", "sched", "schedual", "schedul", "timetable",
        "first bus", "first run", "last bus", "last run",
        "what time", "when does", "start", "end",
        "weekday", "weekdays", "mon-fri", "mon fri", "m/f", "m-f",
        # Spanish
        "horario", "tabla", "primero", "ultimo", "último", "a que hora", "a qué hora",
        "mañana", "tomorrow",
    ]
    return any(k in t for k in schedule_words)


def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    rt_words = [
        "eta", "minutes", "mins", "min", "prediction", "predictions", "arrive", "arrival",
        "next bus", "where is", "vehicle", "location", "real-time", "realtime",
        # Spanish
        "cuantos minutos", "cuántos minutos", "llega", "llegada", "en vivo", "tiempo real",
        "ubicacion", "ubicación",
    ]
    return any(k in t for k in rt_words)



def has_explicit_timeframe(text: str) -> bool:
    t = (text or '').lower()
    # explicit times like 2pm, 2:30 pm
    if re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", t):
        return True
    # explicit dates like 2026-01-31 or 01/31/2026
    if re.search(r"20\d{2}-\d{2}-\d{2}", t) or re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", t):
        return True
    # time hints
    time_words = [
        'after', 'before', 'around', 'at', 'by',
        'today', 'tomorrow', 'tonight',
        'morning', 'afternoon', 'evening',
        'weekday', 'weekdays', 'weekend',
        'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
        # Spanish (ASCII only)
        'hoy', 'manana', 'tarde', 'noche',
        'lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo',
    ]
    return any(k in t for k in time_words)


def humanize_answer(text: str, lang: str) -> str:
    if not text:
        return text
    if os.getenv('HUMANIZE_ENABLED', 'true').lower() == 'false':
        return text
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if OpenAI is None or not api_key:
        return text
    try:
        client = OpenAI(api_key=api_key)
        model = os.getenv('HUMANIZE_MODEL', 'gpt-4o-mini')
        sys_msg = (
            'You are a friendly RTS assistant. Rewrite the answer to be clear and human. '
            'Preserve all times, stop IDs, and route numbers exactly. Do not add facts.'
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': sys_msg},
                {'role': 'user', 'content': text},
            ],
            temperature=0.2,
        )
        out = (resp.choices[0].message.content or '').strip()
        return out or text
    except Exception:
        return text

def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "mins", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "sched", "schedual", "timetable", "first bus", "last bus",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada", "cuantos minutos", "tiempo real",
        "ubicacion", "ubicación", "mañana",
    ]
    return any(k in t for k in keywords)


def guess_destination_hint(text: str) -> str | None:
    t = (text or "").lower()
    if "reitz" in t:
        return "Reitz"
    if "oaks" in t:
        return "Oaks"
    if "downtown" in t:
        return "Downtown"
    if "hub" in t:
        return "Hub"
    if "rosa" in t and "park" in t:
        return "Rosa Parks"
    if "uf" in t or "campus" in t:
        return "UF"
    return None


def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


def detect_language_simple(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ["hola", "horario", "ruta", "parada", "llega", "cuántos", "ubicación", "mañana"]):
        return "es"
    return "en"


def _history_text(history) -> str:
    if not history:
        return ""
    parts = []
    for item in history:
        if isinstance(item, dict):
            role = (item.get("role") or "").lower()
            if role and role != "user":
                continue
            content = item.get("content") or ""
        else:
            content = str(item)
        content = content.strip()
        if content:
            parts.append(content)
    # keep last 6 user messages to preserve context like place/time
    return " ".join(parts[-6:])


def _last_assistant_message(history) -> str:
    if not history:
        return ""
    for item in reversed(history):
        if isinstance(item, dict):
            role = (item.get("role") or "").lower()
            if role in ("assistant", "bot"):
                return (item.get("content") or "").strip()
    return ""

def _assistant_asked_route_number(text: str) -> bool:
    t = (text or "").lower()
    return ("route number" in t) or ("what route" in t) or ("which route" in t)

def _assistant_asked_time(text: str) -> bool:
    t = (text or "").lower()
    return (
        "specify a time" in t
        or "around" in t
        or "first or last" in t
        or "first/last" in t
        or "first service" in t
        or "last service" in t
    )

def _explicit_date_or_weekday(text: str) -> bool:
    t = (text or "").lower()
    if re.search(r"20\d{2}-\d{2}-\d{2}", t) or re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", t):
        return True
    if any(w in t for w in ("today", "tomorrow", "tonight")):
        return True
    if any(w in t for w in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
        return True
    return False

def _is_next_request(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in (
        "next",
        "next one",
        "the next one",
        "next bus",
        "next route",
        "next departure",
        "soonest",
    )

def _has_next_intent(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in ("next", "soonest", "upcoming", "leaving", "depart"))


def _last_user_with_context(history) -> str:
    if not history:
        return ""
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "").lower()
        if role and role != "user":
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if has_explicit_timeframe(content) or guess_destination_hint(content) or re.search(r"\b(from|at|near)\b", content.lower()):
            return content
    return ""

def _last_user_route(history) -> str | None:
    if not history:
        return None
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "").lower()
        if role and role != "user":
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        rid = extract_route_id_regex(content)
        if rid:
            return rid
    return None


def _is_confirmation(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in ("yes", "y", "yeah", "yep", "si", "s")


def _is_rejection(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in ("no", "n", "nope", "nah")


def _extract_confirm_stop_id(assistant_text: str) -> str | None:
    if not assistant_text:
        return None
    m = re.search(r"\bStop\s+([0-9]{4})\b", assistant_text)
    return m.group(1) if m else None


def _extract_confirm_landmark(assistant_text: str) -> str | None:
    if not assistant_text:
        return None
    m = re.search(r"schedules\s+for\s+(.+?)\?", assistant_text, re.IGNORECASE)
    return m.group(1).strip() if m else None



def parse_when_dt_from_message(msg: str) -> datetime:
    """
    Supports:
      - "tomorrow"/"mañana" -> +1 day
      - time like "2pm", "2:15pm", "14:00"
    If no time found -> current time.
    """
    now = datetime.now(TZ)
    base = now

    t = (msg or "").lower()
    if "tomorrow" in t or "mañana" in t:
        base = (now + timedelta(days=1)).replace(hour=now.hour, minute=now.minute, second=0, microsecond=0)

    m = re.search(r"\b([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm)?\b", t)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        ap = (m.group(3) or "").lower()

        if ap == "pm" and hh != 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0

        hh = max(0, min(23, hh))
        mm = max(0, min(59, mm))

        base = base.replace(hour=hh, minute=mm, second=0, microsecond=0)

    return base


# ------------------------------------------------------------
# OpenAI intent extraction (optional)
# ------------------------------------------------------------
def llm_extract_intent(message: str) -> dict:
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
        "- destination_hint is a place name if mentioned. "
        "- language is 'es' if Spanish, else 'en'. "
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
        print("llm_extract_intent_error:", repr(e))
        print(traceback.format_exc())
        return fallback


# ------------------------------------------------------------
# Stop suggestions (Bustime-only by route)
# ------------------------------------------------------------
def _tokenize_for_stop_match(text: str) -> list[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    tokens = [x for x in t.split() if len(x) >= 3]
    drop = {"route", "rt", "stop", "bus", "eta", "at", "to", "from", "the", "and", "for",
            "ruta", "parada", "autobus", "autobús", "bus", "a", "de", "en", "el", "la"}
    return [x for x in tokens if x not in drop]


def _score_stop_name(stop_name: str, tokens: list[str]) -> int:
    name = (stop_name or "").lower()
    score = 0
    for tok in tokens:
        if tok in name:
            score += 2
        elif len(tok) >= 4 and tok[:4] in name:
            score += 1
    return score


def suggest_stops_by_route(route_id: str, message: str, limit: int = 8) -> list[dict]:
    route_id = digits_only(route_id or "")
    if not route_id:
        return []

    tokens = _tokenize_for_stop_match(message)
    hint = guess_destination_hint(message)
    if hint:
        tokens += _tokenize_for_stop_match(hint)
    tokens = list(dict.fromkeys(tokens))

    if not tokens:
        return []

    try:
        dirs_data = rts_api.get_directions(route_id) or {}
        dirs_raw = dirs_data.get("directions", []) or []
    except Exception:
        dirs_raw = []

    dir_ids: list[str] = []
    for d in dirs_raw:
        if isinstance(d, dict):
            dir_id = d.get("dir") or d.get("id") or d.get("direction") or d.get("dirId")
        else:
            dir_id = d
        if dir_id:
            dir_ids.append(str(dir_id))

    if not dir_ids:
        dir_ids = ["NORTHBOUND", "SOUTHBOUND", "EASTBOUND", "WESTBOUND", "INBOUND", "OUTBOUND"]

    seen: set[tuple[str, str]] = set()
    scored: list[tuple[int, dict]] = []

    for dir_id in dir_ids:
        try:
            stops_data = rts_api.get_stops(route_id, dir_id) or {}
            stops_raw = stops_data.get("stops", []) or []
        except Exception:
            stops_raw = []

        for s in stops_raw:
            if not isinstance(s, dict):
                continue
            sid = normalize_stop_id(s.get("stpid") or "")
            nm = (s.get("stpnm") or "").strip()
            if not sid or not nm:
                continue
            key = (sid, nm)
            if key in seen:
                continue
            seen.add(key)

            score = _score_stop_name(nm, tokens)
            if score > 0:
                scored.append((score, {"id": sid, "name": nm}))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]


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
def format_realtime_answer(lang: str, usable_preds: list[dict]) -> str:
    lines = []
    for p in usable_preds[:3]:
        mins = p.get("minutes")
        rt = p.get("route") or ""
        dest = p.get("destination") or ""
        if isinstance(mins, str) and mins.upper() == "DUE":
            lines.append(tmsg(lang, f"Route {rt} to {dest}: DUE", f"Ruta {rt} hacia {dest}: YA"))
        else:
            lines.append(tmsg(lang, f"Route {rt} to {dest}: {mins} min", f"Ruta {rt} hacia {dest}: {mins} min"))

    return tmsg(lang, "Real-time ETA:\n- ", "ETA en tiempo real:\n- ") + "\n- ".join(lines)


# ------------------------------------------------------------
# Core agent logic
# ------------------------------------------------------------
def try_transit_answer(message: str, history=None) -> dict | None:
    msg = (message or "").strip()
    ctx = _history_text(history)
    msg_ctx = (ctx + ' ' + msg).strip() if ctx else msg
    if not msg:
        return None

    # Handle confirmation replies using last assistant prompt
    if _is_confirmation(msg):
        last_assistant = _last_assistant_message(history)
        stop_id = _extract_confirm_stop_id(last_assistant)
        if stop_id:
            msg = f"ETA stop {stop_id}"
            msg_ctx = msg
        else:
            landmark = _extract_confirm_landmark(last_assistant)
            if landmark:
                # Preserve prior route context when user confirms a landmark
                prev = _last_user_with_context(history)
                route = extract_route_id_regex(prev or "")
                if route:
                    msg_ctx = f"route {route} schedule from {landmark}"
                else:
                    msg_ctx = f"schedule from {landmark}"
    elif _is_rejection(msg):
        last_assistant = _last_assistant_message(history)
        if _extract_confirm_stop_id(last_assistant):
            return {
                "answer": tmsg(
                    detect_language_simple(msg),
                    "Okay, tell me the stop ID or stop name.",
                    "Esta bien, dime el ID de la parada o el nombre."
                ),
                "sources": [{"type": "need_stop_after_reject"}],
            }
        if _extract_confirm_landmark(last_assistant):
            return {
                "answer": tmsg(
                    detect_language_simple(msg),
                    "No problem. Which stop or landmark should I use?",
                    "No hay problema. Que parada o lugar debo usar?"
                ),
                "sources": [{"type": "need_landmark_after_reject"}],
            }

    # If assistant asked for a route number and user replies with digits, carry prior context
    if re.fullmatch(r"\d{1,2}", msg):
        last_assistant = _last_assistant_message(history)
        if _assistant_asked_route_number(last_assistant):
            prev = _last_user_with_context(history)
            msg = f"route {msg}"
            msg_ctx = f"{prev} {msg}".strip() if prev else msg

    # If assistant asked for a time and user replies "next", inject a concrete time.
    if _is_next_request(msg):
        last_assistant = _last_assistant_message(history)
        if _assistant_asked_time(last_assistant):
            prev = _last_user_with_context(history)
            prev_route = _last_user_route(history)
            now = datetime.now(TZ)
            hour = now.hour % 12
            hour = 12 if hour == 0 else hour
            ampm = "am" if now.hour < 12 else "pm"
            time_str = f"{hour}:{now.minute:02d} {ampm}"
            if prev:
                # Keep existing date/week context if provided (tomorrow, Tuesday, etc.)
                if _explicit_date_or_weekday(prev):
                    msg_ctx = f"{prev} around {time_str}"
                else:
                    msg_ctx = f"{prev} around {time_str}"
                if prev_route and not extract_route_id_regex(msg_ctx):
                    msg_ctx = f"route {prev_route} {msg_ctx}"
                msg = msg_ctx
            else:
                if prev_route:
                    msg_ctx = f"route {prev_route} schedule around {time_str}"
                else:
                    msg_ctx = f"schedule around {time_str}"
                msg = msg_ctx

    # Digits-only messages: clarify route vs stop OR auto-ETA for likely stop IDs
    if re.fullmatch(r"\d{1,6}", msg):
        lang = detect_language_simple(msg)

        # 1-2 digits: ambiguous (route vs stop)
        if len(msg) <= 2:
            return {
                "answer": tmsg(
                    lang,
                    f"Did you mean Route {msg} or Stop {msg.zfill(4)}?\nReply: 'route {msg}' or 'stop {msg.zfill(4)}'.",
                    f"¿Te refieres a la Ruta {msg} o la Parada {msg.zfill(4)}?\nResponde: 'ruta {msg}' o 'parada {msg.zfill(4)}'."
                ),
                "sources": [{"type": "clarify_route_vs_stop"}],
            }

        # 3-4 digits: treat as Stop ID and run ETA
        if len(msg) <= 4:
            msg = f"ETA stop {msg.zfill(4)}"
        else:
            return {
                "answer": tmsg(
                    lang,
                    "That number looks too long to be a Stop ID. Please type 'stop ####' or 'route ##'.",
                    "Ese número parece demasiado largo para ser un Stop ID. Escribe 'parada ####' o 'ruta ##'."
                ),
                "sources": [{"type": "clarify_number"}],
            }

    if not is_transit_keywords(msg_ctx):
        return None

    extracted = llm_extract_intent(msg_ctx)
    lang = extracted.get("language", "en")
    intent = extracted.get("intent", "general")
    route_id = extracted.get("route_id")
    stop_id = extracted.get("stop_id")
    destination_hint = (extracted.get("destination_hint") or "").strip()

    # Regex fallback for any missing fields
    if not route_id:
        route_id = extract_route_id_regex(msg_ctx)
    if not stop_id:
        stop_id = extract_stop_id_regex(msg_ctx)
    if not destination_hint:
        destination_hint = guess_destination_hint(msg_ctx) or ""

    has_time = has_explicit_timeframe(msg_ctx)
    prefer_schedule = has_time or (intent == "schedule") or (wants_schedule(msg_ctx) and not wants_realtime(msg_ctx))

    # If user asks for "next" with a route + landmark, prefer schedule (no stop_id yet)
    if not prefer_schedule and route_id and destination_hint and not stop_id and _has_next_intent(msg_ctx):
        prefer_schedule = True


    if prefer_schedule and not route_id:
        return {
            "answer": tmsg(
                lang,
                "Got it. What route number should I use?",
                "Entiendo. ?Que numero de ruta debo usar?"
            ),
            "sources": [{"type": "need_route_schedule"}],
        }

    if prefer_schedule and route_id and not stop_id and not destination_hint and not re.search(r"\b(from|at|near)\b", msg_ctx.lower()):
        return {
            "answer": tmsg(
                lang,
                "Which stop or landmark should I use? For example: 'from Rosa Parks'",
                "?Que parada o lugar debo usar? Por ejemplo: 'desde Rosa Parks'"
            ),
            "sources": [{"type": "need_stop_schedule"}],
        }

    if prefer_schedule and route_id and destination_hint and not stop_id and not re.search(r"\b(from|at|near)\b", msg_ctx.lower()):
        return {
            "answer": tmsg(
                lang,
                f"Do you want schedules for {destination_hint}? Reply yes or no.",
                f"Quieres horarios para {destination_hint}? Responde si o no."
            ),
            "sources": [{"type": "confirm_landmark_schedule"}],
        }

    # Schedule questions (Backend Basics preferred)
    if prefer_schedule and BACKEND_BASICS_AVAILABLE and BB_ANSWER_FN:
        try:
            # If "next" with no explicit time, inject current time so schedules can answer.
            if _has_next_intent(msg_ctx) and not has_explicit_timeframe(msg_ctx):
                now = datetime.now(TZ)
                hour = now.hour % 12
                hour = 12 if hour == 0 else hour
                ampm = "am" if now.hour < 12 else "pm"
                time_str = f"{hour}:{now.minute:02d} {ampm}"
                msg_ctx = f"{msg_ctx} around {time_str}"
            res = BB_ANSWER_FN(msg_ctx)
            if isinstance(res, dict):
                answer_text = res.get("response_text") or str(res)
            else:
                answer_text = str(res)
            # If multiple headsigns are present, ask for direction/landmark
            if isinstance(res, dict):
                raw = res.get("raw") or {}
                next_by_dir = raw.get("next_by_direction") or []
                headsigns = [h for _, h in next_by_dir if h]
                uniq = sorted(set(headsigns))
                if len(uniq) > 1 and not destination_hint:
                    options = "; ".join(uniq)
                    return {
                        "answer": tmsg(
                            lang,
                            f"I can help—are you headed toward {options}? Reply with the destination or direction.",
                            f"Puedo ayudar—¿vas hacia {options}? Responde con el destino o la direccion."
                        ),
                        "sources": [{"type": "need_direction_schedule"}],
                    }
            answer_text = humanize_answer(answer_text, lang)
            return {
                "answer": answer_text,
                "sources": [{"type": "backend_basics_schedule"}],
            }
        except Exception as e:
            print("backend_basics_answer_error:", repr(e))

    # If stop_id missing (ETA flow only):
    if not prefer_schedule and not stop_id:
        if route_id:
            candidates = suggest_stops_by_route(route_id, (destination_hint + " " + msg).strip(), limit=8)

            if len(candidates) == 1:
                stop_id = candidates[0]["id"]
            elif len(candidates) > 1:
                return {
                    "answer": tmsg(
                        lang,
                        f"Did you mean Stop {candidates[0]['id']}: {candidates[0]['name']}? Reply yes or no.",
                        f"Te refieres a la parada {candidates[0]['id']}: {candidates[0]['name']}? Responde si o no."
                    ),
                    "sources": [{"type": "stop_suggestions_bustime", "route_id": route_id}],
                }

        if not stop_id:
            return {
                "answer": tmsg(
                    lang,
                    "To check ETA, I need either a 4-digit Stop ID or a route number + place (example: 'ETA Route 1 at Reitz').",
                    "Para ver el ETA, necesito el Stop ID de 4 dígitos o una ruta + lugar (ej: 'ETA Ruta 1 en Reitz')."
                ),
                "sources": [{"type": "need_stop_or_route"}],
            }

    # Schedule questions (Backend Basics required)
    if prefer_schedule:
        return {
            "answer": humanize_answer(tmsg(
                lang,
                "Schedule database is unavailable right now. Please try again later.",
                "La base de datos de horarios no esta disponible en este momento. Intenta mas tarde."
            ), lang),
            "sources": [{"type": "backend_basics_unavailable"}],
        }

    # Real-time predictions (Bustime)
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

    # Keep <=45 min or DUE  (Option B uses schedule if none in this window)
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
            "answer": humanize_answer(format_realtime_answer(lang, usable), lang),
            "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
        }

    # If no real-time ETAs, fall back to schedule when possible
    if BACKEND_BASICS_AVAILABLE and BB_ANSWER_FN and route_id and (destination_hint or "from" in msg_ctx.lower() or "leaving" in msg_ctx.lower()):
        try:
            if _has_next_intent(msg_ctx) and not has_explicit_timeframe(msg_ctx):
                now = datetime.now(TZ)
                hour = now.hour % 12
                hour = 12 if hour == 0 else hour
                ampm = "am" if now.hour < 12 else "pm"
                time_str = f"{hour}:{now.minute:02d} {ampm}"
                msg_ctx = f"{msg_ctx} around {time_str}"
            res = BB_ANSWER_FN(msg_ctx)
            if isinstance(res, dict):
                answer_text = res.get("response_text") or str(res)
            else:
                answer_text = str(res)
            answer_text = humanize_answer(answer_text, lang)
            return {
                "answer": answer_text,
                "sources": [{"type": "backend_basics_schedule_fallback"}],
            }
        except Exception:
            pass

    return {
        "answer": humanize_answer(tmsg(
            lang,
            f"No real-time ETAs (<=45 min) found for Stop {stop_id}.",
            f"No hay ETAs en tiempo real (<=45 min) para la parada {stop_id}."
        ), lang),
        "sources": [{"type": "realtime_none", "stop_id": stop_id, "route_id": route_id}],
    }

def handle_agent_message(message: str, history=None) -> dict:
    transit = try_transit_answer(message, history=history)
    if transit:
        return {
            "answer": transit.get("answer", ""),
            "sources": transit.get("sources", []),
        }

    return {
        "answer": "I can help with RTS ETAs and schedules. Try: 'ETA Route 1 at Reitz' or type a Stop ID like '0473'.",
        "sources": [{"type": "fallback"}],
    }
