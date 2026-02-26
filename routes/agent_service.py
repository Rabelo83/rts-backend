"""
RTS Transit Agent — orchestration module.

Imports pure helpers from:
  routes.parsing_helpers  — text/regex utilities
  routes.intent_extractor — LLM intent extraction (structured outputs)
  routes.stop_resolver    — GTFS/Bustime stop resolution
  routes.response_builder — response formatting

This module keeps only:
  - Cache wrapper for schedule queries
  - Backend Basics schedule engine
  - Conversation history helpers
  - try_transit_answer   (main orchestrator)
  - handle_agent_message (public entry point)
"""
import os
import re
import traceback
import sys
from datetime import datetime, timedelta
from pathlib import Path
import logging

# Add utils to path
utils_path = str(Path(__file__).resolve().parents[1] / "utils")
if utils_path not in sys.path:
    sys.path.insert(0, utils_path)

from cache import schedule_cache

# Deterministic schedule lookup (GTFS DB)
try:
    from routes import schedule_service
except Exception:
    schedule_service = None

logger = logging.getLogger(__name__)

# ── New module imports ────────────────────────────────────────────────────────
from routes.parsing_helpers import (
    TZ,
    normalize_stop_id,
    digits_only,
    extract_any_stop_candidate,
    extract_route_id_regex,
    extract_stop_id_regex,
    wants_schedule,
    wants_realtime,
    has_explicit_timeframe,
    is_transit_keywords,
    guess_destination_hint,
    extract_origin_place,
    tmsg,
    detect_language_simple,
    _normalize_place,
    _filter_headsigns_by_origin,
    _explicit_date_or_weekday,
    _is_next_request,
    _is_followup_after,
    _extract_last_departure_time,
    _advance_time_one_minute,
    _has_next_intent,
    _normalize_time_tokens,
    _has_strong_context,
    parse_when_dt_from_message,
    format_time_12h,
    normalize_times_in_text,
)

from routes.intent_extractor import (
    llm_extract_intent_hybrid,
    humanize_answer,
    _openai_client,
)

from routes.stop_resolver import (
    get_predictions_cached,
    infer_routes_from_predictions,
    suggest_stops_by_route,
    _gtfs_resolve_stop_name,
    route_serves_stop,
)

from routes.response_builder import (
    fmt_stop_list,
    format_realtime_answer,
    build_direction_prompt,
    build_exception_note,
)

# ── Cache configuration ───────────────────────────────────────────────────────
SCHEDULE_CACHE_TTL = int(os.getenv("SCHEDULE_CACHE_TTL", "60"))

# ── Optional Backend Basics schedule engine ──────────────────────────────────
BACKEND_BASICS_AVAILABLE = False
BB_ANSWER_FN = None
try:
    backend_basics_db = Path(__file__).resolve().parents[1] / "Backend Basics" / "db"
    if backend_basics_db.exists():
        sys.path.insert(0, str(backend_basics_db))
        import answering_layer as _bb_answering_layer

        BB_ANSWER_FN = _bb_answering_layer.answer_question
        BACKEND_BASICS_AVAILABLE = True
except Exception as e:
    logger.error("backend_basics_import_error: %s", repr(e))


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


def get_schedule_cached(route, text, stop_id=None, stop_name=None, kind="next", debug=False):
    """Get schedule data with LRU caching."""
    if not schedule_service:
        return {"error": "db_unavailable"}
    key = f"schedule:{route}:{text}:{stop_id}:{stop_name}:{kind}:{debug}"
    cached = schedule_cache.get(key)
    if cached is not None:
        return cached
    data = schedule_service.get_schedule(route, text, stop_id=stop_id, stop_name=stop_name, kind=kind, debug=debug)
    schedule_cache.set(key, data, ttl=SCHEDULE_CACHE_TTL)
    return data


# ── Conversation history helpers ──────────────────────────────────────────────

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
    if not api_key:
        return fallback
    try:
        client = _openai_client(api_key)
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


# ── Core agent logic ──────────────────────────────────────────────────────────

_GREETING_WORDS = frozenset([
    "hi", "hello", "hey", "hola", "yo", "sup", "howdy",
    "good morning", "good afternoon", "good evening",
    "buenos dias", "buenas tardes", "buenas noches",
])


def try_transit_answer(message: str, history=None) -> dict | None:
    msg_raw = (message or "").strip()
    msg = _normalize_time_tokens(msg_raw)

    # Pure greeting — never merge with prior transit history.
    # Return None so handle_agent_message can respond conversationally.
    if msg.lower().strip().rstrip("!?., ") in _GREETING_WORDS:
        return None

    ctx = _history_text(history)
    msg_has_strong_context = _has_strong_context(msg)
    last_assistant = _last_assistant_message(history)
    direction_followup = _assistant_asked_direction(last_assistant) and not msg_has_strong_context
    # If user provides a stop ID without a route, don't carry prior route context,
    # BUT do carry date/time context from history (e.g., "tomorrow morning" from a prior turn).
    if extract_stop_id_regex(msg) and not extract_route_id_regex(msg):
        prev = _last_user_with_context(history)
        if prev and has_explicit_timeframe(prev):
            msg_ctx = f"{msg} {prev}".strip()
        else:
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
            lacks_route = not extract_route_id_regex(msg)
            lacks_stop = not extract_stop_id_regex(msg)
            lacks_place = not guess_destination_hint(msg)
            if lacks_route and lacks_stop and lacks_place and ctx:
                # Pure follow-up (e.g. "after 7am?"): merge with full user history
                # to carry forward route/stop/place context from earlier turns
                msg_ctx = f"{msg} {ctx}".strip()
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

    # "The one after that?" / "after that?" — advance past the last shown departure time
    if _is_followup_after(normalized_msg):
        last_assistant = _last_assistant_message(history)
        last_time = _extract_last_departure_time(last_assistant)
        if last_time:
            # Use full ctx (all prior user messages) stripped of old "after TIME" patterns.
            # This preserves route AND stop name from ALL earlier turns so neither is lost
            # when the user types vague follow-ups like "the one after that?" or "one after?".
            # Advance threshold by +1 min so the GTFS >= query does NOT re-show the same bus.
            next_threshold = _advance_time_one_minute(last_time)
            ctx_clean = re.sub(
                r"\bafter\s+\d{1,2}(:\d{2})?\s*(am|pm)\b", "", ctx, flags=re.IGNORECASE
            ).strip()
            msg_ctx = f"{ctx_clean} after {next_threshold}".strip() if ctx_clean else f"after {next_threshold}"
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

    # Pass through if: transit keyword present OR message contains a route/stop number
    # This catches natural-language queries like "when does the next 43 leave santa fe?"
    if not is_transit_keywords(msg_ctx) and not extract_route_id_regex(msg_ctx) and not extract_stop_id_regex(msg_ctx):
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
    if route_id and stop_id and route_id == stop_id and 'route' not in msg_ctx.lower():
        route_id = None
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

    # If user asks for "next" with a route + any place hint, prefer schedule (no stop_id yet)
    # Catches: "when the next 43 will be at santa fe?" where stop_name_hint/origin_hint is "santa fe"
    _has_place = bool(destination_hint or stop_name_hint or origin_hint)
    if not prefer_schedule and route_id and _has_place and not stop_id and _has_next_intent(msg_ctx) and not wants_realtime(msg_ctx):
        prefer_schedule = True

    # "first after 5pm" means "next after 5pm", NOT "first bus of the day".
    # Suppress wants_first/wants_last when an explicit am/pm time is present in msg_ctx.
    _has_explicit_ampm = bool(re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", msg_ctx.lower()))
    wants_first = "first" in msg_ctx.lower() and not _has_explicit_ampm
    wants_last = "last" in msg_ctx.lower() and not _has_explicit_ampm

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
        exception_note = build_exception_note(lang, data.get("date"), data.get("exception"))
        lines = [
            f"Next scheduled departures from {data.get('stop')} on {data.get('date')} after {format_time_12h(data.get('time'))}:"
        ]
        for rt, t, headsign in next_by_route[:10]:
            if headsign:
                lines.append(f"- Route {rt} to {headsign}: {format_time_12h(t)}")
            else:
                lines.append(f"- Route {rt}: {format_time_12h(t)}")
        if exception_note:
            lines.append(exception_note)
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
        # Show route day summary first — give the rider useful context before asking for more
        _parsed_date = schedule_service.parse_date(msg_ctx) if schedule_service else None
        _date_str = _parsed_date.isoformat() if _parsed_date else None
        _summary = schedule_service.get_route_day_summary(route_id, _date_str) if schedule_service else None
        if _summary and _summary.get("runs_today"):
            _dirs = _summary["directions"]
            _dir_lines = []
            for _d in _dirs:
                _freq = f" ({_d['frequency']})" if _d.get("frequency") else ""
                _dir_lines.append(f"  • {_d['headsign']}: {_d['first']} – {_d['last']}{_freq}")
            _overview = "\n".join(_dir_lines)
            _buttons = [
                {"label": _d["headsign"], "action": f"schedule route {route_id} {_d['headsign']}"}
                for _d in _dirs[:4]
            ]
            return _with_meta({
                "answer": tmsg(
                    lang,
                    f"Route {route_id} ({_summary['route_long_name']}) runs on {_summary['day_label']}:\n{_overview}\n\nWant the full schedule? Tell me your stop, direction, or a time frame.",
                    f"La ruta {route_id} ({_summary['route_long_name']}) opera el {_summary['day_label']}:\n{_overview}\n\n¿Quieres el horario completo? Dime tu parada, dirección o franja horaria."
                ),
                "buttons": _buttons,
                "sources": [{"type": "route_day_summary", "route": route_id, "date": _summary["date_iso"]}],
            })
        elif _summary and not _summary.get("runs_today"):
            return _with_meta({
                "answer": tmsg(
                    lang,
                    f"Route {route_id} does not run on {_summary['day_label']}.",
                    f"La ruta {route_id} no opera el {_summary['day_label']}."
                ),
                "sources": [{"type": "route_day_summary", "route": route_id}],
            })
        # Fallback: no summary available — ask for stop
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

    # Track whether the user explicitly mentioned a time so we can omit "after X PM" when not specified.
    _explicit_time = has_explicit_timeframe(msg_ctx)

    # Schedule questions (deterministic, direct DB)
    if prefer_schedule and schedule_service and route_id:
        kind = "first" if wants_first else ("last" if wants_last else "next")
        stop_name = None if direction_followup else (stop_name_hint or destination_hint or origin_hint or None)
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

        exception_note = build_exception_note(lang, data.get("date"), data.get("exception"))

        # Format deterministic schedule output
        if kind == "first" and data.get("first_departure"):
            answer = f"First departure for route {data['route']} from {data['stop']} on {data['date']}: {format_time_12h(data['first_departure'])}"
            if exception_note:
                answer = f"{answer}\n{exception_note}"
            return _with_meta({
                "answer": answer,
                "sources": [{"type": "schedule_first"}],
            })
        if kind == "last" and data.get("last_departure"):
            answer = f"Last departure for route {data['route']} from {data['stop']} on {data['date']}: {format_time_12h(data['last_departure'])}"
            if exception_note:
                answer = f"{answer}\n{exception_note}"
            return _with_meta({
                "answer": answer,
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
            if not next_by_dir:
                return _with_meta({
                    "answer": tmsg(
                        lang,
                        "No scheduled trips found after that time for this stop. Try another time.",
                        "No encontre viajes programados despues de esa hora para esta parada. Intenta otra hora.",
                    ) + (f"\n{exception_note}" if exception_note else ""),
                    "sources": [{"type": "schedule_no_time"}],
                })
            _time_suffix = f" after {format_time_12h(data['time'])}" if _explicit_time and data.get("time") else ""
            lines = [
                f"Next departures for route {data['route']} from {data['stop']} on {data['date']}{_time_suffix}:"
            ]
            for t, headsign in next_by_dir:
                lines.append(f"- {format_time_12h(t)} ({headsign})")
            if exception_note:
                lines.append(exception_note)
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
                                _time_suffix_bb = f" after {format_time_12h(raw['time'])}" if _explicit_time else ""
                                lines = [
                                    f"Next departures for route {raw['route']} from {raw['stop']} on "
                                    f"{raw['date']}{_time_suffix_bb}:"
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
                    _time_suffix_bb = f" after {format_time_12h(raw['time'])}" if _explicit_time else ""
                    lines = [
                        f"Next departures for route {raw['route']} from {raw['stop']} on "
                        f"{raw['date']}{_time_suffix_bb}:"
                    ]
                    for t, headsign in next_by_dir:
                        lines.append(f"- {format_time_12h(t)} ({headsign})")
                    answer_text = "\n".join(lines)
            answer_text = normalize_times_in_text(answer_text)
            # Strip injected "after H:MM AM/PM" from response when user didn't specify a time.
            if not _explicit_time and answer_text:
                answer_text = re.sub(r",?\s*after \d{1,2}:\d{2}\s*[AP]M\b", "", answer_text, flags=re.IGNORECASE)
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
        # Try GTFS lookup first using the stop name hint from LLM or origin/destination hints
        _stop_name_for_gtfs = stop_name_hint or origin_hint or destination_hint
        if route_id and _stop_name_for_gtfs:
            _gtfs = _gtfs_resolve_stop_name(route_id, _stop_name_for_gtfs)
            if _gtfs and "stop_id" in _gtfs:
                stop_id = _gtfs["stop_id"]
            elif _gtfs and "candidates" in _gtfs:
                buttons = [
                    {"label": f"Stop {c['stop_id']} - {c['stop_name'][:40]}", "action": f"ETA stop {c['stop_id']}"}
                    for c in _gtfs["candidates"][:3]
                ]
                return _with_meta({
                    "answer": tmsg(
                        lang,
                        f"Multiple stops match '{_stop_name_for_gtfs}' on Route {route_id}. Which one?",
                        f"Varias paradas coinciden con '{_stop_name_for_gtfs}' en la Ruta {route_id}. ¿Cuál?",
                    ),
                    "buttons": buttons,
                    "sources": [{"type": "stop_suggestions_gtfs", "route_id": route_id}],
                })

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
            # Route discovery: "what routes go to UF?" / "which buses serve Shands?"
            _is_route_discovery = bool(
                re.search(r"\b(which|what)\b.+\b(routes?|bus(es)?|lines?)\b", msg_ctx.lower())
            ) or bool(
                re.search(r"\b(routes?|bus(es)?)\b.+\b(go to|serve|stop at|near|to)\b", msg_ctx.lower())
            )
            if _is_route_discovery and destination_hint and schedule_service:
                # Prefer authoritative area lookup; fall back to stop-name LIKE search
                _disc_routes = schedule_service.routes_serving_area(destination_hint)
                if not _disc_routes:
                    _disc_routes = schedule_service.routes_serving_destination(destination_hint)
                if _disc_routes:
                    _route_labels = ", ".join(
                        f"Route {r['route_id']}" for r in _disc_routes[:8]
                    )
                    _buttons = [
                        {"label": f"Route {r['route_id']}", "action": f"schedule route {r['route_id']}"}
                        for r in _disc_routes[:8]
                    ]
                    return _with_meta({
                        "answer": tmsg(
                            lang,
                            f"These routes serve {destination_hint}: {_route_labels}. Which one do you need?",
                            f"Estas rutas sirven a {destination_hint}: {_route_labels}. ¿Cuál necesitas?"
                        ),
                        "buttons": _buttons,
                        "sources": [{"type": "route_discovery", "destination": destination_hint}],
                    })

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


def stream_agent_message(message: str, history=None):
    """
    Generator that yields SSE event dicts for the /api/agent/stream endpoint.

    Event shapes:
      {"type": "status", "text": "..."}   — update the typing indicator label
      {"type": "token",  "text": "..."}   — append chunk to the bot bubble
      {"type": "done",   "answer": "...", "buttons": ..., "sources": ..., "meta": ...}
      {"type": "error",  "text": "..."}   — terminal error
    """
    from routes.intent_extractor import humanize_answer_stream

    yield {"type": "status", "text": "Thinking…"}

    try:
        result = handle_agent_message(message, history=history)
    except Exception as e:
        logger.error("stream_agent_message_error: %s", repr(e))
        yield {"type": "error", "text": "Something went wrong. Please try again."}
        return

    answer_text = result.get("answer", "")
    buttons = result.get("buttons")
    sources = result.get("sources", [])
    meta = result.get("meta", {})

    # Stream the answer in word chunks (typewriter effect).
    # When HUMANIZE_ENABLED=true the LLM itself streams token-by-token.
    full_streamed = ""
    for chunk in humanize_answer_stream(answer_text, meta.get("language", "en")):
        full_streamed += chunk
        yield {"type": "token", "text": chunk}

    yield {
        "type": "done",
        "answer": full_streamed or answer_text,
        "buttons": buttons,
        "sources": sources,
        "meta": meta,
    }


def handle_agent_message(message: str, history=None) -> dict:
    transit = try_transit_answer(message, history=history)
    if transit:
        return {
            "answer": transit.get("answer", ""),
            "sources": transit.get("sources", []),
            "buttons": transit.get("buttons"),
            "meta": transit.get("meta", {}),
        }

    lang = detect_language_simple(message)
    msg_check = message.lower().strip().rstrip("!?., ")

    # Greeting — warm, conversational response
    if msg_check in _GREETING_WORDS:
        buttons = [
            {"label": "Check Next Bus ETA", "action": "I need to check next bus ETA"},
            {"label": "View Schedule", "action": "I need to view a schedule"},
        ]
        return {
            "answer": tmsg(
                lang,
                "Hi! I can look up real-time bus ETAs and schedules for Gainesville RTS. Which route or stop are you asking about?",
                "¡Hola! Puedo buscar ETAs en tiempo real y horarios de RTS Gainesville. ¿Qué ruta o parada necesitas?"
            ),
            "buttons": buttons,
            "sources": [{"type": "greeting"}],
            "meta": {"intent": "greeting", "language": lang},
        }

    # Help request
    msg_lower = message.lower()
    if any(word in msg_lower for word in ["help", "how", "ayuda", "como"]):
        buttons = [
            {"label": "Check Next Bus ETA", "action": "I need to check next bus ETA"},
            {"label": "View Schedule", "action": "I need to view a schedule"},
            {"label": "Find Stop by Route", "action": "Find stops for route"},
        ]
        return {
            "answer": tmsg(
                lang,
                "I can help you with:\n• Real-time bus ETAs — tell me a route and stop or a 4-digit Stop ID\n• Schedules — ask about tomorrow, a specific time, first/last bus\n• Stop lookup — ask for stops on any route\n\nExample: 'Route 43 from Shands after 5pm' or 'Stop 0473'",
                "Puedo ayudarte con:\n• ETAs en tiempo real — dime una ruta y parada o el Stop ID\n• Horarios — pregunta por mañana, una hora, primer/último bus\n• Búsqueda de paradas — pide las paradas de cualquier ruta\n\nEjemplo: 'Ruta 43 desde Shands después de las 5pm' o 'Parada 0473'"
            ),
            "buttons": buttons,
            "sources": [{"type": "help"}],
            "meta": {"intent": "help", "language": lang},
        }

    # Unknown — guide conversationally instead of throwing instructions at the user
    return {
        "answer": tmsg(
            lang,
            "I didn't quite catch that. I can look up schedules and real-time ETAs — just tell me a route number and where you're going, like 'Route 5 from downtown' or 'Stop 0473'.",
            "No entendí bien. Puedo buscar horarios y ETAs — dime una ruta y destino, como 'Ruta 5 desde el centro' o 'Parada 0473'."
        ),
        "sources": [{"type": "fallback"}],
        "meta": {
            "intent": "fallback",
            "language": lang,
        },
    }
