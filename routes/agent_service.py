import os
import re
import json
import traceback
import sys
from pathlib import Path
from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo
import logging

import rts_api
import sqlite3

# Deterministic schedule lookup (GTFS DB)
try:
    from routes import schedule_service
except Exception:
    schedule_service = None
# OpenAI is OPTIONAL (agent still works without it)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# Configure logger
logger = logging.getLogger(__name__)

TZ = ZoneInfo("America/New_York")
GTFS_DB_PATH = Path(__file__).resolve().parents[1] / "Backend Basics" / "db" / "rts_gtfs.sqlite"

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
    logger.error("backend_basics_import_error: %s", repr(e))

SCHEDULE_CACHE: dict = {}
PREDICTION_CACHE: dict = {}
SCHEDULE_CACHE_TTL = int(os.getenv("SCHEDULE_CACHE_TTL", "60"))
PREDICTION_CACHE_TTL = int(os.getenv("PREDICTION_CACHE_TTL", "20"))

def _cache_get(cache: dict, key, ttl: int):
    entry = cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts <= ttl:
        return value
    cache.pop(key, None)
    return None

def _cache_set(cache: dict, key, value):
    cache[key] = (time.time(), value)

def get_schedule_cached(route, text, stop_id=None, stop_name=None, kind="next", debug=False):
    if not schedule_service:
        return {"error": "db_unavailable"}
    key = (route or "", text or "", stop_id or "", stop_name or "", kind or "", bool(debug))
    cached = _cache_get(SCHEDULE_CACHE, key, SCHEDULE_CACHE_TTL)
    if cached is not None:
        return cached
    data = schedule_service.get_schedule(route, text, stop_id=stop_id, stop_name=stop_name, kind=kind, debug=debug)
    _cache_set(SCHEDULE_CACHE, key, data)
    return data

def get_predictions_cached(stop_id: str):
    key = stop_id or ""
    cached = _cache_get(PREDICTION_CACHE, key, PREDICTION_CACHE_TTL)
    if cached is not None:
        return cached
    data = rts_api.get_predictions(stop_id)
    _cache_set(PREDICTION_CACHE, key, data)
    return data

def infer_routes_from_predictions(stop_id: str) -> dict[str, dict]:
    """
    Returns {route: {"directions": set(), "destinations": set()}} from Bustime predictions.
    """
    if not stop_id:
        return {}
    try:
        data = get_predictions_cached(stop_id) or {}
        preds = data.get("prd", []) or []
    except Exception:
        return {}
    routes: dict[str, dict] = {}
    for p in preds:
        rt = str(p.get("rt") or "").strip()
        if not rt:
            continue
        entry = routes.setdefault(rt, {"directions": set(), "destinations": set()})
        rtdir = (p.get("rtdir") or "").strip()
        des = (p.get("des") or "").strip()
        if rtdir:
            entry["directions"].add(rtdir)
        if des:
            entry["destinations"].add(des)
    return routes


def ensure_backend_basics() -> bool:
    global BACKEND_BASICS_AVAILABLE, BB_ANSWER_FN
    if BACKEND_BASICS_AVAILABLE and BB_ANSWER_FN:
        return True
    try:
        backend_basics_db = Path(__file__).resolve().parents[1] / "Backend Basics" / "db"
        if not backend_basics_db.exists():
            return False
        if str(backend_basics_db) not in sys.path:
            sys.path.insert(0, str(backend_basics_db))
        import answering_layer as _bb_answering_layer

        BB_ANSWER_FN = _bb_answering_layer.answer_question
        BACKEND_BASICS_AVAILABLE = True
        return True
    except Exception as e:
        logger.error("backend_basics_import_error_runtime: %s", repr(e))
        return False


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


def extract_any_stop_candidate(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"\b([0-9]{3,4})\b", text)
    if m:
        return normalize_stop_id(m.group(1))
    return None


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

    # Heuristic: "arrive at 1612" or "at 1612" in a transit query
    m = re.search(r"\b(at|arrive at|arrival at)\s+([0-9]{3,6})\b", t)
    if m:
        return normalize_stop_id(m.group(2))

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
    if "noon" in t or "midnight" in t:
        return True
    # explicit dates like 2026-01-31 or 01/31/2026
    if re.search(r"20\d{2}-\d{2}-\d{2}", t) or re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", t):
        return True
    # time hints
    time_words = [
        'after', 'before', 'around', 'by',
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
    if os.getenv('HUMANIZE_ENABLED', 'false').lower() == 'false':
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

def extract_origin_place(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"(from|leaving|at)\s+(.+?)(?:\s+on|\s+at|\s+around|\?|$)", text, re.IGNORECASE)
    if m:
        cand = m.group(2).strip()
        if re.search(r"\d", cand) or re.search(r"\b(am|pm)\b", cand.lower()):
            return None
        return cand
    return None


def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


def detect_language_simple(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ["hola", "horario", "ruta", "parada", "llega", "cuántos", "ubicación", "mañana"]):
        return "es"
    return "en"

PLACE_TOKEN_RE = re.compile(r"[^a-z0-9]+")
PLACE_SYNONYMS = {
    "rosa parks downtown station": {
        "rosa parks",
        "rosa parks downtown station",
        "downtown station",
        "rosa parks station",
        "rosa parks transfer",
        "rosa parks transfer station",
        "downtown transfer station",
    },
}

def _normalize_place(text: str | None) -> str:
    if not text:
        return ""
    norm = PLACE_TOKEN_RE.sub(" ", text.lower()).strip()
    if not norm:
        return ""
    for canonical, variants in PLACE_SYNONYMS.items():
        for variant in variants:
            vnorm = PLACE_TOKEN_RE.sub(" ", variant.lower()).strip()
            if not vnorm:
                continue
            if vnorm in norm or norm in vnorm:
                return canonical
    return norm

def _filter_headsigns_by_origin(
    headsigns: list[str],
    origin_hint: str | None,
    stop_name: str | None = None
) -> tuple[list[str], bool]:
    origin_norm = _normalize_place(origin_hint) or _normalize_place(stop_name)
    if not origin_norm:
        return headsigns, False
    trimmed = []
    for h in headsigns:
        base = re.sub(r"^(to|toward|towards)\s+", "", h or "", flags=re.IGNORECASE)
        norm = _normalize_place(base)
        if norm and norm == origin_norm:
            continue
        trimmed.append(h)
    if trimmed:
        return trimmed, True
    return headsigns, False


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

def _history_summary_for_llm(history) -> str:
    """
    Produce a short summary of prior user turns for passing to the LLM.
    Falls back to simple concatenation when LLM is unavailable or history is short.
    """
    fallback = _history_text(history)
    if not history:
        return ""
    if len(fallback) <= 220:
        return fallback
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return fallback
    try:
        client = OpenAI(api_key=api_key)
        summary_model = os.getenv("SUMMARY_MODEL", os.getenv("HUMANIZE_MODEL", "gpt-4o-mini"))
        messages = [
            {
                "role": "system",
                "content": (
                    "Summarize the rider's recent RTS requests in <=45 words. "
                    "Keep stop IDs, route numbers, landmarks, and times. English only."
                ),
            },
            {
                "role": "user",
                "content": fallback,
            },
        ]
        resp = client.chat.completions.create(
            model=summary_model,
            messages=messages,
            temperature=0.2,
            max_tokens=120,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or fallback
    except Exception:
        return fallback


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
        or "please include a time" in t
        or "first or last" in t
        or "first/last" in t
        or "first service" in t
        or "last service" in t
    )

def _assistant_asked_direction(text: str) -> bool:
    t = (text or "").lower()
    return (
        "are you headed toward" in t
        or "estas yendo hacia" in t
        or "¿estas yendo hacia" in t
    )

def _assistant_asked_for_stop_or_landmark(text: str) -> bool:
    """Check if assistant asked 'Which stop or landmark should I use for Route X?'"""
    t = (text or "").lower()
    return (
        "which stop or landmark should i use" in t
        or "que parada o lugar debo usar" in t
        or "¿que parada o lugar debo usar" in t
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
        "next?",
    )

def _has_next_intent(text: str) -> bool:
    t = (text or "").lower()
    if "first" in t or "last" in t:
        return False
    return any(kw in t for kw in ("next", "soonest", "upcoming", "leaving", "depart"))

def _normalize_time_tokens(text: str) -> str:
    if not text:
        return text
    t = text.lower()
    t = re.sub(r"\bnoon time\b", "noon", t)
    t = re.sub(r"\bmidnight time\b", "midnight", t)
    # normalize odd separators like "12..00pm" -> "12:00pm" (allow 1-digit minutes)
    def _pad_minutes(match):
        hh = match.group(1)
        mm = match.group(2) or "0"
        ap = match.group(3)
        if len(mm) == 1:
            mm = mm.zfill(2)
        return f"{hh}:{mm} {ap}"
    t = re.sub(r"\b(\d{1,2})\D{1,3}(\d{1,2})\s*(am|pm)\b", _pad_minutes, t)
    return t

def _has_strong_context(text: str) -> bool:
    if not text:
        return False
    has_route = bool(extract_route_id_regex(text))
    has_stop = bool(extract_stop_id_regex(text))
    has_time = has_explicit_timeframe(text)
    has_place_keywords = bool(re.search(r"\b(from|at|near|leaving|stop)\b", text.lower()))

    if has_route or has_stop:
        return True
    if guess_destination_hint(text) and (has_route or has_stop or has_time or has_place_keywords):
        return True
    if has_time:
        return True
    if has_place_keywords:
        return True
    return False


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

    m = re.search(r"\b([0-9]{1,2})(?::([0-9]{1,2}))?\s*(am|pm)?\b", t)
    if m:
        hh = int(m.group(1))
        mm_raw = m.group(2) or "0"
        if len(mm_raw) == 1:
            mm_raw = mm_raw.zfill(2)
        mm = int(mm_raw)
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
def llm_extract_intent(message: str, history_summary: str | None = None) -> dict:
    fallback = {
        "intent": "general",
        "route_id": None,
        "stop_id": None,
        "destination_hint": None,
        "language": detect_language_simple(message),
        "direction": None,
        "stop_name": None,
        "origin_hint": None,
        "timeframe": None,
        "confidence": 0.0,
        "needs": [],
    }

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return fallback

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "You extract transit intent for Gainesville RTS. "
        "Return ONLY JSON with keys: intent, route_id, stop_id, stop_name, direction, "
        "destination_hint, origin_hint, timeframe, language, confidence, needs. "
        "Rules: "
        "- intent is one of: eta, schedule, vehicle_location, general, clarification. "
        "- route_id is route number like '9' (string). "
        "- stop_id is 1-4 digit stop ID if provided; otherwise null. "
        "- stop_name is a textual landmark/stop if given. "
        "- direction is textual headsign/destination ('To Oaks Mall') if given. "
        "- destination_hint/origin_hint capture place names. "
        "- timeframe is a short text description of when (e.g., 'tomorrow around 3pm'). "
        "- language is 'es' if Spanish, else 'en'. "
        "- confidence is 0-1 float reflecting certainty. "
        "- needs is an array containing any missing info the rider should provide "
          "from: route, stop, direction, time. "
        "- If unsure, intent='general'."
    )

    user_payload = {
        "message": message,
        "history_summary": history_summary or "",
    }

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
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
        direction = (obj.get("direction") or "").strip() or None
        stop_name = (obj.get("stop_name") or "").strip() or None
        origin_hint = (obj.get("origin_hint") or "").strip() or None
        timeframe = (obj.get("timeframe") or "").strip() or None
        language = (obj.get("language") or "en").strip().lower()
        if language not in ("en", "es"):
            language = "en"
        confidence = 0.0
        try:
            confidence = float(obj.get("confidence") or 0)
        except Exception:
            confidence = 0.0
        needs = obj.get("needs") or []
        if not isinstance(needs, list):
            needs = []

        return {
            "intent": intent,
            "route_id": route_id,
            "stop_id": stop_id,
            "destination_hint": destination_hint,
            "direction": direction,
            "stop_name": stop_name,
            "origin_hint": origin_hint,
            "timeframe": timeframe,
            "language": language,
            "confidence": confidence,
            "needs": needs,
        }

    except Exception as e:
        logger.error("llm_extract_intent_error: %s\n%s", repr(e), traceback.format_exc())
        return fallback


def llm_extract_intent_hybrid(message: str, history: list = None) -> dict:
    """
    Enhanced LLM extraction that receives FULL conversation history
    for context-aware extraction. This enables follow-up questions like
    "what about after 3:30pm?" to preserve route/stop from previous turns.

    Option 3 (Hybrid): Use LLM for extraction with full context,
    then use deterministic database queries for execution.
    """
    fallback = {
        "intent": "general",
        "route_id": None,
        "stop_id": None,
        "destination_hint": None,
        "language": detect_language_simple(message),
        "direction": None,
        "stop_name": None,
        "origin_hint": None,
        "timeframe": None,
        "confidence": 0.0,
        "needs": [],
    }

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return fallback

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    history = history or []

    system = (
        "You extract transit intent for Gainesville RTS with full conversation context. "
        "Return ONLY JSON with keys: intent, route_id, stop_id, stop_name, direction, "
        "destination_hint, origin_hint, timeframe, language, confidence, needs. "
        "Rules: "
        "- intent is one of: eta, schedule, vehicle_location, general, clarification. "
        "- route_id is route number like '9' (string). "
        "- stop_id is 1-4 digit stop ID if provided; otherwise null. "
        "- stop_name is a textual landmark/stop if given. "
        "- direction is textual headsign/destination ('To Oaks Mall') if given. "
        "- destination_hint/origin_hint capture place names. "
        "- timeframe is a short text description of when (e.g., 'tomorrow around 3pm'). "
        "- language is 'es' if Spanish, else 'en'. "
        "- confidence is 0-1 float reflecting certainty. "
        "- needs is an array containing any missing info the rider should provide "
          "from: route, stop, direction, time. "
        "- IMPORTANT: If the user references previous conversation (e.g., 'what about after 3:30pm?'), "
          "carry forward the route, stop, and other context from history. "
        "- If unsure, intent='general'."
    )

    # Build conversation messages with history
    messages = [{"role": "system", "content": system}]

    # Add conversation history (last 4 turns to keep context manageable)
    for turn in history[-8:]:  # 8 messages = 4 back-and-forth turns
        if isinstance(turn, dict) and turn.get("role") and turn.get("content"):
            messages.append({
                "role": turn["role"],
                "content": turn["content"]
            })

    # Add current user message
    messages.append({
        "role": "user",
        "content": f"Extract transit intent from: {message}"
    })

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        obj = json.loads(raw)

        intent = (obj.get("intent") or "general").strip()
        route_id = digits_only(obj.get("route_id") or "") or None
        stop_id = normalize_stop_id(obj.get("stop_id") or "") if obj.get("stop_id") else None
        destination_hint = (obj.get("destination_hint") or "").strip() or None
        direction = (obj.get("direction") or "").strip() or None
        stop_name = (obj.get("stop_name") or "").strip() or None
        origin_hint = (obj.get("origin_hint") or "").strip() or None
        timeframe = (obj.get("timeframe") or "").strip() or None
        language = (obj.get("language") or "en").strip().lower()
        if language not in ("en", "es"):
            language = "en"
        confidence = 0.0
        try:
            confidence = float(obj.get("confidence") or 0)
        except Exception:
            confidence = 0.0
        needs = obj.get("needs") or []
        if not isinstance(needs, list):
            needs = []

        return {
            "intent": intent,
            "route_id": route_id,
            "stop_id": stop_id,
            "destination_hint": destination_hint,
            "direction": direction,
            "stop_name": stop_name,
            "origin_hint": origin_hint,
            "timeframe": timeframe,
            "language": language,
            "confidence": confidence,
            "needs": needs,
        }

    except Exception as e:
        logger.error("llm_extract_intent_hybrid_error: %s\n%s", repr(e), traceback.format_exc())
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

def build_direction_prompt(options: str, lang: str, ctx_info: dict | None = None) -> str:
    base = tmsg(
        lang,
        f"Which direction are you headed toward: {options}? Reply with the destination or direction.",
        f"¿Hacia cual direccion vas: {options}? Responde con el destino o la direccion."
    )
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return base
    try:
        client = OpenAI(api_key=api_key)
        clarify_model = os.getenv("CLARIFY_MODEL", os.getenv("HUMANIZE_MODEL", "gpt-4o-mini"))
        ctx = ctx_info or {}
        user_payload = {
            "options": options,
            "language": lang or "en",
            "route": ctx.get("route"),
            "stop": ctx.get("stop"),
            "time": ctx.get("time"),
        }
        resp = client.chat.completions.create(
            model=clarify_model,
            temperature=0.3,
            max_tokens=120,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You rewrite clarification questions for the RTS Gainesville assistant. "
                        "Keep them concise (<=25 words) and match the rider's language (English or Spanish). "
                        "Mention the route/stop if provided. Ask the rider to choose a direction."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or base
    except Exception:
        return base


def format_time_12h(hhmmss: str) -> str:
    if not hhmmss:
        return hhmmss
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", hhmmss.strip())
    if not m:
        return hhmmss
    hh = int(m.group(1))
    mm = int(m.group(2))
    ap = "AM" if hh < 12 else "PM"
    h12 = hh % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{mm:02d} {ap}"


def normalize_times_in_text(text: str) -> str:
    if not text:
        return text
    def repl(m):
        return format_time_12h(m.group(0))
    return re.sub(r"\b\d{1,2}:\d{2}:\d{2}\b", repl, text)


def route_serves_stop(route_id: str, stop_id_padded: str) -> bool:
    if not route_id or not stop_id_padded:
        return False
    if not GTFS_DB_PATH.exists():
        return True  # avoid blocking if GTFS isn't available
    try:
        conn = sqlite3.connect(GTFS_DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT 1
            FROM stops s
            JOIN stop_times st ON st.stop_id = s.stop_id
            JOIN trips t ON t.trip_id = st.trip_id
            JOIN routes r ON r.route_id = t.route_id
            WHERE TRIM(r.route_short_name) = ?
              AND s.stop_id_padded = ?
            LIMIT 1
            """,
            (str(route_id), str(stop_id_padded)),
        ).fetchone()
        return bool(row)
    except Exception:
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ------------------------------------------------------------
# Core agent logic
# ------------------------------------------------------------
def try_transit_answer(message: str, history=None) -> dict | None:
    msg_raw = (message or "").strip()
    msg = _normalize_time_tokens(msg_raw)
    ctx = _history_text(history)
    history_summary = _history_summary_for_llm(history)
    msg_has_strong_context = _has_strong_context(msg)
    last_assistant = _last_assistant_message(history)
    direction_followup = _assistant_asked_direction(last_assistant) and not msg_has_strong_context
    # If user provides a stop ID without a route, don't carry prior route context.
    if extract_stop_id_regex(msg) and not extract_route_id_regex(msg):
        msg_ctx = msg
    elif direction_followup:
        prev = _last_user_with_context(history)
        if prev:
            msg_ctx = f"{prev} {msg}".strip()
        elif ctx:
            msg_ctx = (ctx + " " + msg).strip()
        else:
            msg_ctx = msg
    elif ctx and not msg_has_strong_context:
        msg_ctx = (ctx + " " + msg).strip()
    else:
        if msg_has_strong_context:
            prev = _last_user_with_context(history) or ctx
            lacks_route = not extract_route_id_regex(msg)
            lacks_stop = not extract_stop_id_regex(msg)
            lacks_place = not guess_destination_hint(msg)
            if prev and (lacks_route and lacks_stop and lacks_place):
                msg_ctx = f"{msg} {prev}".strip()
            else:
                msg_ctx = msg
        else:
            msg_ctx = msg
    if not msg:
        return None

    # Handle confirmation replies using last assistant prompt
    normalized_msg = msg.lower()

    if _is_confirmation(normalized_msg):
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
                if prev:
                    # Keep original time/date/first/last context
                    msg_ctx = prev
                elif route:
                    msg_ctx = f"route {route} schedule from {landmark}"
                else:
                    msg_ctx = f"schedule from {landmark}"
    elif _is_rejection(normalized_msg):
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

    # If assistant asked for stop/landmark and user replies with a place name, extract route from assistant message
    if not extract_route_id_regex(msg) and not extract_stop_id_regex(msg):
        last_assistant = _last_assistant_message(history)
        if _assistant_asked_for_stop_or_landmark(last_assistant):
            # Extract route from assistant's message: "Which stop or landmark should I use for Route 43?"
            route_match = re.search(r"route\s+(\d+)", last_assistant.lower())
            if route_match:
                route_num = route_match.group(1)
                prev = _last_user_with_context(history) or ""
                msg_ctx = f"route {route_num} {prev} from {msg}".strip()
        elif has_explicit_timeframe(msg) and history:
            prev = _last_user_with_context(history)
            if prev:
                msg_ctx = f"{prev} {msg}".strip()

    # If assistant asked for a time and user replies "next", inject a concrete time.
    if _is_next_request(normalized_msg):
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
            buttons = [
                {"label": f"Route {msg}", "action": f"route {msg}"},
                {"label": f"Stop {msg.zfill(4)}", "action": f"stop {msg.zfill(4)}"}
            ]
            return _with_meta({
                "answer": tmsg(
                    lang,
                    f"Did you mean Route {msg} or Stop {msg.zfill(4)}?",
                    f"¿Te refieres a la Ruta {msg} o la Parada {msg.zfill(4)}?"
                ),
                "buttons": buttons,
                "sources": [{"type": "clarify_route_vs_stop"}],
            })

        # 3-4 digits: treat as Stop ID and run ETA
        if len(msg) <= 4:
            msg = f"ETA stop {msg.zfill(4)}"
            msg_ctx = msg
        else:
            return _with_meta({
                "answer": tmsg(
                    lang,
                    "That number looks too long to be a Stop ID. Please type 'stop ####' or 'route ##'.",
                    "Ese número parece demasiado largo para ser un Stop ID. Escribe 'parada ####' o 'ruta ##'."
                ),
                "sources": [{"type": "clarify_number"}],
            })

    if not is_transit_keywords(msg_ctx):
        return None

    # Option 3 (Hybrid): ALWAYS use LLM extraction with full conversation history
    # This enables context-aware extraction for follow-up questions like "what about after 3:30pm?"
    extracted = llm_extract_intent_hybrid(msg_ctx, history=history)

    # Start with basic regex extraction
    lang = detect_language_simple(msg_ctx)
    route_id = extract_route_id_regex(msg_ctx)
    stop_id = extract_stop_id_regex(msg_ctx)
    destination_hint = guess_destination_hint(msg_ctx) or ""
    direction_hint = ""
    stop_name_hint = ""
    origin_hint = extract_origin_place(msg_ctx) or ""
    timeframe_hint = msg_ctx if has_explicit_timeframe(msg_ctx) else ""
    intent = "schedule" if (wants_schedule(msg_ctx) and not wants_realtime(msg_ctx)) else ("eta" if wants_realtime(msg_ctx) else "general")
    llm_needs: list[str] = []

    # Override with LLM extraction (which has conversation context)
    lang = extracted.get("language", lang)
    intent = extracted.get("intent", intent)
    route_id = route_id or extracted.get("route_id")
    stop_id = stop_id or extracted.get("stop_id")
    direction_hint = (extracted.get("direction") or "").strip()
    stop_name_hint = (extracted.get("stop_name") or "").strip()
    llm_destination = (extracted.get("destination_hint") or "").strip()
    llm_origin = (extracted.get("origin_hint") or "").strip()
    llm_timeframe = (extracted.get("timeframe") or "").strip()
    if llm_destination and not destination_hint:
        destination_hint = llm_destination
    if llm_origin and not origin_hint:
        origin_hint = llm_origin
    if llm_timeframe and not timeframe_hint:
        timeframe_hint = llm_timeframe
    llm_needs = extracted.get("needs") or []

    if direction_hint and not destination_hint:
        destination_hint = direction_hint
    if not origin_hint:
        origin_hint = extract_origin_place(msg_ctx) or ""
    if not timeframe_hint and has_explicit_timeframe(msg_ctx):
        timeframe_hint = msg_ctx

    prefer_schedule = False

    def _with_meta(payload, meta_updates=None):
        meta = {
            "route": route_id,
            "stop_id": stop_id,
            "destination": destination_hint or stop_name_hint or "",
            "intent": intent,
            "language": lang,
            "needs": llm_needs,
            "prefer_schedule": prefer_schedule,
            "timeframe": timeframe_hint,
        }
        if meta_updates:
            meta.update(meta_updates)
        payload = dict(payload)
        payload["meta"] = meta
        return payload

    # If route is known and we still don't have a stop_id, use a loose 3-4 digit token.
    if route_id and not stop_id:
        cand = extract_any_stop_candidate(msg_ctx)
        if cand:
            stop_id = cand

    has_time = bool(timeframe_hint) or has_explicit_timeframe(msg_ctx)
    prefer_schedule = has_time or (intent == "schedule") or (wants_schedule(msg_ctx) and not wants_realtime(msg_ctx))

    # If user asks for "next" with a route + landmark, prefer schedule (no stop_id yet)
    if not prefer_schedule and route_id and destination_hint and not stop_id and _has_next_intent(msg_ctx) and not wants_realtime(msg_ctx):
        prefer_schedule = True

    wants_first = "first" in msg_ctx.lower()
    wants_last = "last" in msg_ctx.lower()

    if stop_id and not route_id:
        route_map = infer_routes_from_predictions(stop_id)
        if route_map:
            if len(route_map) == 1:
                route_id = next(iter(route_map.keys()))
                dirs = list(route_map[route_id]["directions"])
                if len(dirs) == 1 and not direction_hint:
                    direction_hint = dirs[0]
                if not destination_hint and route_map[route_id]["destinations"]:
                    destination_hint = next(iter(route_map[route_id]["destinations"]))
            elif prefer_schedule:
                buttons = []
                for rt, info in list(route_map.items())[:5]:
                    dests = sorted(info["destinations"])
                    label = f"Route {rt}"
                    if dests:
                        label = f"{label} ({', '.join(dests[:2])})"
                    buttons.append({"label": label, "action": f"route {rt}"})
                return _with_meta({
                    "answer": tmsg(
                        lang,
                        f"Which route should I use for Stop {stop_id}?",
                        f"¿Qué ruta debo usar para la parada {stop_id}?"
                    ),
                    "buttons": buttons,
                    "sources": [{"type": "need_route_from_stop"}],
                })


    if prefer_schedule and not route_id and stop_id and not wants_first and not wants_last and schedule_service:
        data = schedule_service.get_schedule_all_routes(timeframe_hint or msg_ctx, stop_id=stop_id)
        if data.get("error") == "db_unavailable":
            return _with_meta({
                "answer": tmsg(
                    lang,
                    "Schedule database is unavailable right now. Please try again later.",
                    "La base de datos de horarios no esta disponible en este momento. Intenta mas tarde.",
                ),
                "sources": [{"type": "backend_basics_unavailable"}],
            })
        if data.get("error") == "stop_not_found":
            return _with_meta({
                "answer": tmsg(
                    lang,
                    "I couldn't find that stop in the schedule database. Please check the 4-digit Stop ID.",
                    "No pude encontrar esa parada en la base de datos. Verifica el Stop ID de 4 digitos.",
                ),
                "sources": [{"type": "schedule_stop_not_found"}],
            })
        next_by_route = data.get("next_by_route") or []
        if not next_by_route:
            return _with_meta({
                "answer": tmsg(
                    lang,
                    "No scheduled trips found after that time for this stop. Try another time.",
                    "No encontre viajes programados despues de esa hora para esta parada. Intenta otra hora.",
                ),
                "sources": [{"type": "schedule_no_time"}],
            })
        lines = [
            f"Next scheduled departures from {data.get('stop')} on {data.get('date')} after {format_time_12h(data.get('time'))}:"
        ]
        for rt, t, headsign in next_by_route[:10]:
            if headsign:
                lines.append(f"- Route {rt} to {headsign}: {format_time_12h(t)}")
            else:
                lines.append(f"- Route {rt}: {format_time_12h(t)}")
        return _with_meta({
            "answer": "\n".join(lines),
            "sources": [{"type": "schedule_all_routes"}],
        })

    if prefer_schedule and not route_id:
        return _with_meta({
            "answer": tmsg(
                lang,
                "I can pull the schedule once I know the route number. Which RTS route are you asking about?",
                "Puedo revisar el horario cuando sepa el numero de ruta. ¿Que ruta de RTS necesitas?"
            ),
            "sources": [{"type": "need_route_schedule"}],
        })

    if prefer_schedule and route_id and not stop_id and not destination_hint and not re.search(r"\b(from|at|near)\b", msg_ctx.lower()):
        return _with_meta({
            "answer": tmsg(
                lang,
                "Which stop or landmark should I use for Route {}? For example: 'from Rosa Parks' or a 4-digit Stop ID.".format(route_id),
                "¿Que parada o lugar debo usar para la ruta {}? Ejemplo: 'desde Rosa Parks' o el Stop ID de 4 digitos.".format(route_id)
            ),
            "sources": [{"type": "need_stop_schedule"}],
        })

    # Skip yes/no confirmation; let the schedule engine disambiguate stops if needed.

    # If asking first/last, ensure the query includes the keyword for the schedule engine.
    if (wants_first or wants_last) and route_id:
        if wants_first and "first" not in msg_ctx.lower():
            msg_ctx = f"first {msg_ctx}"
        if wants_last and "last" not in msg_ctx.lower():
            msg_ctx = f"last {msg_ctx}"

    # Schedule questions (deterministic, direct DB)
    if prefer_schedule and schedule_service and route_id:
        kind = "first" if wants_first else ("last" if wants_last else "next")
        stop_name = None if direction_followup else (stop_name_hint or destination_hint or None)
        data = get_schedule_cached(route_id, msg_ctx, stop_id=stop_id, stop_name=stop_name, kind=kind)
        if data.get("error") == "multiple_stops":
            cands = data.get("candidates") or []
            lines = [f"- {c['stop_name']} (Stop {c['stop_id_padded']})" for c in cands]

            # Create button options for frontend
            buttons = [
                {
                    "label": f"Stop {c['stop_id_padded']} - {c['stop_name']}",
                    "action": f"stop {c['stop_id_padded']}"
                }
                for c in cands[:5]  # Limit to 5 buttons
            ]

            return _with_meta({
                "answer": tmsg(
                    lang,
                    "Multiple stops match. Choose one:",
                    "Coinciden varias paradas. Elige una:",
                ),
                "buttons": buttons,
                "sources": [{"type": "schedule_stop_disambiguate"}],
            })
        if data.get("error") == "stop_not_found":
            return _with_meta({
            "answer": tmsg(
                lang,
                f"I couldn't match that stop to Route {route_id}. Please provide the 4-digit Stop ID from the sign or name a nearby landmark.",
                f"No pude encontrar esa parada para la ruta {route_id}. Dame el Stop ID de 4 digitos del letrero o un lugar cercano.",
            ),
                "sources": [{"type": "schedule_stop_not_found"}],
            })
        if data.get("error") == "db_unavailable":
            return _with_meta({
                "answer": tmsg(
                    lang,
                    "Schedule database is unavailable right now. Please try again later.",
                    "La base de datos de horarios no esta disponible en este momento. Intenta mas tarde.",
                ),
                "sources": [{"type": "backend_basics_unavailable"}],
            })

        # Format deterministic schedule output
        if kind == "first" and data.get("first_departure"):
            return _with_meta({
                "answer": f"First departure for route {data['route']} from {data['stop']} on {data['date']}: {format_time_12h(data['first_departure'])}",
                "sources": [{"type": "schedule_first"}],
            })
        if kind == "last" and data.get("last_departure"):
            return _with_meta({
                "answer": f"Last departure for route {data['route']} from {data['stop']} on {data['date']}: {format_time_12h(data['last_departure'])}",
                "sources": [{"type": "schedule_last"}],
            })

        next_by_dir = data.get("next_by_direction") or []
        headsigns = [h for _, h in next_by_dir if h]
        uniq = sorted(set(headsigns))
        origin = origin_hint or extract_origin_place(msg_ctx)
        filtered_heads, removed = _filter_headsigns_by_origin(uniq, origin, data.get("stop"))
        if removed:
            uniq = filtered_heads
            next_by_dir = [p for p in next_by_dir if p[1] in uniq]
            data["next_by_direction"] = next_by_dir
        if len(uniq) > 1 and (not destination_hint or (origin and destination_hint.lower() == origin.lower())):
            options = "; ".join(uniq)
            prompt = build_direction_prompt(
                options,
                lang,
                {
                    "route": route_id,
                    "stop": data.get("stop"),
                    "time": data.get("date"),
                },
            )
            return _with_meta({
                "answer": prompt,
                "sources": [{"type": "need_direction_schedule"}],
            })

        if data.get("time"):
            lines = [
                f"Next departures for route {data['route']} from {data['stop']} on {data['date']} after {format_time_12h(data['time'])}:"
            ]
            for t, headsign in next_by_dir:
                lines.append(f"- {format_time_12h(t)} ({headsign})")
            return _with_meta({
                "answer": "\n".join(lines),
                "sources": [{"type": "schedule_next"}],
            })

        return _with_meta({
            "answer": tmsg(
                lang,
                "I couldn't find departures after that time. Try a specific time (e.g., 'after 4:15pm') or provide the 4-digit Stop ID from the sign.",
                "No encontre salidas despues de esa hora. Intenta con una hora exacta (ej: 'despues de 4:15pm') o comparte el Stop ID de 4 digitos.",
            ),
            "sources": [{"type": "schedule_no_time"}],
        })

    # Schedule questions (Backend Basics preferred)
    if prefer_schedule and ensure_backend_basics() and BB_ANSWER_FN:
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
                raw = res.get("raw") or {}
                # If schedule engine still asks for a time, retry once with injected time.
                if "include a time" in (answer_text or "").lower():
                    now = datetime.now(TZ)
                    hour = now.hour % 12
                    hour = 12 if hour == 0 else hour
                    ampm = "am" if now.hour < 12 else "pm"
                    time_str = f"{hour}:{now.minute:02d} {ampm}"
                    res = BB_ANSWER_FN(f"{msg_ctx} around {time_str}")
                    if isinstance(res, dict):
                        answer_text = res.get("response_text") or str(res)
                        raw = res.get("raw") or {}
                    else:
                        answer_text = str(res)
                        raw = {}
                # If multiple headsigns remain and user didn't pick direction, ask to clarify.
                next_by_dir = raw.get("next_by_direction") or []
                headsigns = [h for _, h in next_by_dir if h]
                uniq = sorted(set(headsigns))
                origin = origin_hint or extract_origin_place(msg_ctx)
                filtered_heads, removed = _filter_headsigns_by_origin(uniq, origin, raw.get("stop"))
                if removed:
                    uniq = filtered_heads
                    next_by_dir = [p for p in next_by_dir if p[1] in uniq]
                    raw["next_by_direction"] = next_by_dir
                if len(uniq) > 1 and (not destination_hint or (origin and destination_hint.lower() == origin.lower())):
                    options = "; ".join(uniq)
                    prompt = build_direction_prompt(
                        options,
                        lang,
                        {
                            "route": raw.get("route"),
                            "stop": raw.get("stop"),
                            "time": raw.get("date"),
                        },
                    )
                    return _with_meta({
                        "answer": prompt,
                        "sources": [{"type": "need_direction_schedule"}],
                    })
                # Direction disambiguation: if user said "leaving/from X", prefer headsigns
                # that do NOT contain the origin place name.
                origin = origin_hint or extract_origin_place(msg_ctx)
                if origin and isinstance(raw, dict):
                    next_by_dir = raw.get("next_by_direction") or []
                    if next_by_dir:
                        origin_l = origin.lower()
                        filtered = [(t, h) for (t, h) in next_by_dir if origin_l not in (h or "").lower()]
                        if filtered:
                            raw["next_by_direction"] = filtered
                            # rebuild answer text using filtered options if we have keys
                            if all(k in raw for k in ("route", "stop", "date", "time")):
                                lines = [
                                    f"Next departures for route {raw['route']} from {raw['stop']} on "
                                    f"{raw['date']} after {format_time_12h(raw['time'])}:"
                                ]
                                for t, headsign in filtered:
                                    lines.append(f"- {format_time_12h(t)} ({headsign})")
                                answer_text = "\n".join(lines)

                # If multiple headsigns remain and no destination hint, ask to clarify.
                if isinstance(raw, dict):
                    next_by_dir = raw.get("next_by_direction") or []
                    headsigns = [h for _, h in next_by_dir if h]
                    uniq = sorted(set(headsigns))
                    origin = origin_hint or extract_origin_place(msg_ctx)
                    filtered_heads, removed = _filter_headsigns_by_origin(uniq, origin, raw.get("stop"))
                    if removed:
                        uniq = filtered_heads
                        next_by_dir = [p for p in next_by_dir if p[1] in uniq]
                        raw["next_by_direction"] = next_by_dir
                    if len(uniq) > 1 and (not destination_hint or (origin and destination_hint.lower() == origin.lower())):
                        options = "; ".join(uniq)
                        prompt = build_direction_prompt(
                            options,
                            lang,
                            {
                                "route": raw.get("route"),
                                "stop": raw.get("stop"),
                                "time": raw.get("date"),
                            },
                        )
                        return _with_meta({
                            "answer": prompt,
                            "sources": [{"type": "need_direction_schedule"}],
                        })
            else:
                answer_text = str(res)
            # Normalize any 24h times in the default response
            if isinstance(res, dict):
                raw = res.get("raw") or {}
                next_by_dir = raw.get("next_by_direction") or []
                if next_by_dir and all(k in raw for k in ("route", "stop", "date", "time")):
                    lines = [
                        f"Next departures for route {raw['route']} from {raw['stop']} on "
                        f"{raw['date']} after {format_time_12h(raw['time'])}:"
                    ]
                    for t, headsign in next_by_dir:
                        lines.append(f"- {format_time_12h(t)} ({headsign})")
                    answer_text = "\n".join(lines)
            answer_text = normalize_times_in_text(answer_text)
            # If multiple headsigns are present, ask for direction/landmark
            if isinstance(res, dict):
                raw = res.get("raw") or {}
                next_by_dir = raw.get("next_by_direction") or []
                headsigns = [h for _, h in next_by_dir if h]
                uniq = sorted(set(headsigns))
                origin = origin_hint or extract_origin_place(msg_ctx)
                filtered_heads, removed = _filter_headsigns_by_origin(uniq, origin, raw.get("stop"))
                if removed:
                    uniq = filtered_heads
                    next_by_dir = [p for p in next_by_dir if p[1] in uniq]
                    raw["next_by_direction"] = next_by_dir
                if len(uniq) > 1 and not destination_hint:
                    options = "; ".join(uniq)
                    prompt = build_direction_prompt(
                        options,
                        lang,
                        {
                            "route": raw.get("route"),
                            "stop": raw.get("stop"),
                            "time": raw.get("date"),
                        },
                    )
                    return _with_meta({
                        "answer": prompt,
                        "sources": [{"type": "need_direction_schedule"}],
                    })
            # Avoid LLM paraphrasing for schedules to prevent hallucinations.
            return _with_meta({
                "answer": answer_text,
                "sources": [{"type": "backend_basics_schedule"}],
            })
        except Exception as e:
            logger.error("backend_basics_answer_error: %s", repr(e))

    # If stop_id missing (ETA flow only):
    if not prefer_schedule and not stop_id:
        if route_id:
            candidates = suggest_stops_by_route(route_id, (destination_hint + " " + msg).strip(), limit=8)

            if len(candidates) == 1:
                stop_id = candidates[0]["id"]
            elif len(candidates) > 1:
                # Show top 3 stop options as buttons
                buttons = [
                    {
                        "label": f"Stop {c['id']} - {c['name'][:40]}",
                        "action": f"ETA stop {c['id']}"
                    }
                    for c in candidates[:3]
                ]
                return _with_meta({
                    "answer": tmsg(
                        lang,
                        f"I found {len(candidates)} stops for Route {route_id}. Which one?",
                        f"Encontré {len(candidates)} paradas para la Ruta {route_id}. ¿Cuál?"
                    ),
                    "buttons": buttons,
                    "sources": [{"type": "stop_suggestions_bustime", "route_id": route_id}],
                })

        if not stop_id:
            return _with_meta({
                "answer": tmsg(
                    lang,
                    "To check ETA, give me the 4-digit Stop ID from the sign or say something like 'Route 5 at Rosa Parks'.",
                    "Para ver el ETA, dime el Stop ID de 4 digitos del letrero o algo como 'Ruta 5 en Rosa Parks'."
                ),
                "sources": [{"type": "need_stop_or_route"}],
            })

    # Schedule questions (Backend Basics required)
    if prefer_schedule:
        return _with_meta({
            "answer": humanize_answer(tmsg(
                lang,
                "Schedule data is unavailable right now. Please retry in a minute or ask for live ETA with a Stop ID.",
                "La base de datos de horarios no esta disponible ahora. Intenta de nuevo en un minuto o pide el ETA en vivo con un Stop ID."
            ), lang),
            "sources": [{"type": "backend_basics_unavailable"}],
        })

    # Real-time predictions (Bustime)
    predictions = []
    try:
        if route_id and stop_id and not route_serves_stop(route_id, stop_id):
            return _with_meta({
                "answer": tmsg(
                    lang,
                    f"Route {route_id} does not serve Stop {stop_id}. Please choose a different stop or route.",
                    f"La ruta {route_id} no pasa por la parada {stop_id}. Elige otra parada o ruta."
                ),
                "sources": [{"type": "route_not_serving_stop"}],
            })
        data = get_predictions_cached(stop_id)
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
        logger.error("predictions_error: %s\n%s", repr(e), traceback.format_exc())

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
        return _with_meta({
            "answer": humanize_answer(format_realtime_answer(lang, usable), lang),
            "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
        })

    # If no real-time ETAs, fall back to schedule when possible
    if ensure_backend_basics() and BB_ANSWER_FN and route_id and (destination_hint or "from" in msg_ctx.lower() or "leaving" in msg_ctx.lower()):
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
            # Strip any unexpected Stop ID tokens from schedule text
            answer_text = re.sub(r"\s*\\(Stop ID:\\s*\\d+\\)", "", answer_text)
            answer_text = humanize_answer(answer_text, lang)
            return _with_meta({
                "answer": answer_text,
                "sources": [{"type": "backend_basics_schedule_fallback"}],
            })
        except Exception:
            pass

    return _with_meta({
        "answer": humanize_answer(tmsg(
            lang,
            f"No real-time ETAs (<=45 min) found for Stop {stop_id}.",
            f"No hay ETAs en tiempo real (<=45 min) para la parada {stop_id}."
        ), lang),
        "sources": [{"type": "realtime_none", "stop_id": stop_id, "route_id": route_id}],
    })

def handle_agent_message(message: str, history=None) -> dict:
    transit = try_transit_answer(message, history=history)
    if transit:
        return {
            "answer": transit.get("answer", ""),
            "sources": transit.get("sources", []),
            "buttons": transit.get("buttons"),
            "meta": transit.get("meta", {}),
        }

    # Check if asking for help
    msg_lower = message.lower()
    if any(word in msg_lower for word in ["help", "how", "ayuda", "como"]):
        buttons = [
            {"label": "Check Next Bus ETA", "action": "I need to check next bus ETA"},
            {"label": "View Schedule", "action": "I need to view a schedule"},
            {"label": "Find Stop by Route", "action": "Find stops for route"},
        ]
        return {
            "answer": "I can help you with:\n• Real-time bus ETAs\n• Schedule lookups\n• Finding stops\n\nJust tell me a route and stop (e.g., 'Route 5 at Rosa Parks') or enter a 4-digit Stop ID.",
            "buttons": buttons,
            "sources": [{"type": "help"}],
            "meta": {"intent": "help", "language": detect_language_simple(message)},
        }

    return {
        "answer": tmsg(
            detect_language_simple(message),
            "I'm here to help with RTS ETAs and schedules. Tell me a route plus stop (e.g., 'Route 5 at Rosa Parks') or share a 4-digit Stop ID.",
            "Estoy aqui para ayudarte con ETAs y horarios de RTS. Dime una ruta y parada (ej: 'Ruta 5 en Rosa Parks') o comparte un Stop ID de 4 digitos."
        ),
        "sources": [{"type": "fallback"}],
        "meta": {
            "intent": "fallback",
            "language": detect_language_simple(message),
        },
    }
