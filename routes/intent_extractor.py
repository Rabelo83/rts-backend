"""
LLM-based intent extraction for the RTS transit assistant.
Wraps OpenAI (or any OpenAI-compatible endpoint) for:
  - llm_extract_intent        — single-shot extraction (no history)
  - llm_extract_intent_hybrid — context-aware extraction (full history)
  - humanize_answer           — optional natural-language rewrite

Task C: Structured outputs
  Uses response_format=json_schema (strict) on production OpenAI endpoints.
  Falls back to json_object when OPENAI_BASE_URL is set (local Ollama/LM Studio),
  since those may not support strict JSON schema enforcement.
"""
import os
import json
import traceback
import logging

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from routes.parsing_helpers import detect_language_simple, digits_only, normalize_stop_id

logger = logging.getLogger(__name__)

# ── Structured-output schema ─────────────────────────────────────────────────
# All required fields, strict types. Null values use anyOf to stay spec-compliant.
_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["eta", "schedule", "vehicle_location", "general", "clarification"],
        },
        "route_id":         {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "stop_id":          {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "stop_name":        {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "direction":        {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "destination_hint": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "origin_hint":      {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "timeframe":        {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "language":         {"type": "string", "enum": ["en", "es"]},
        "confidence":       {"type": "number"},
        "needs": {
            "type": "array",
            "items": {"type": "string", "enum": ["route", "stop", "direction", "time"]},
        },
    },
    "required": [
        "intent", "route_id", "stop_id", "stop_name", "direction",
        "destination_hint", "origin_hint", "timeframe", "language",
        "confidence", "needs",
    ],
    "additionalProperties": False,
}


def _make_response_format() -> dict:
    """Return the response_format dict for a chat completions call.

    Uses json_schema (structured outputs, strict=True) when talking to the
    real OpenAI API.  Falls back to json_object for local endpoints (Ollama,
    LM Studio, etc.) set via OPENAI_BASE_URL, which may not support strict
    schema enforcement.
    """
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if base_url:
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "transit_intent",
            "strict": True,
            "schema": _INTENT_SCHEMA,
        },
    }


# ── OpenAI client factory ────────────────────────────────────────────────────

def _openai_client(api_key: str):
    """Build an OpenAI-compatible client.
    - Set OPENAI_BASE_URL to point at a local LLM (Ollama, LM Studio, etc.)
    - max_retries=3: SDK auto-retries on 429 RateLimit and 5xx errors with
      exponential backoff (~1s, 2s, 4s). Prevents transient failures reaching the rider.
    - timeout=30: each request hard-capped at 30 s to avoid tying up the Gunicorn worker.
    """
    kwargs: dict = {
        "api_key": api_key,
        "max_retries": int(os.getenv("OPENAI_MAX_RETRIES", "3")),
        "timeout": float(os.getenv("OPENAI_TIMEOUT", "30")),
    }
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


# ── Shared fallback ──────────────────────────────────────────────────────────

def _fallback_intent(message: str) -> dict:
    return {
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


def _parse_intent_obj(obj: dict, message: str) -> dict:
    """Normalize raw LLM JSON into the canonical intent dict."""
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


# ── System prompt (shared) ───────────────────────────────────────────────────

_SYSTEM_SINGLE = (
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

_SYSTEM_HYBRID = (
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


# ── Public API ────────────────────────────────────────────────────────────────

def llm_extract_intent(message: str, history_summary: str | None = None) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return _fallback_intent(message)

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    user_payload = {
        "message": message,
        "history_summary": history_summary or "",
    }

    try:
        client = _openai_client(api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_SINGLE},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            response_format=_make_response_format(),
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        obj = json.loads(raw)
        return _parse_intent_obj(obj, message)

    except Exception as e:
        logger.error("llm_extract_intent_error: %s\n%s", repr(e), traceback.format_exc())
        return _fallback_intent(message)


def llm_extract_intent_hybrid(message: str, history: list = None) -> dict:
    """
    Enhanced LLM extraction that receives FULL conversation history
    for context-aware extraction. This enables follow-up questions like
    "what about after 3:30pm?" to preserve route/stop from previous turns.

    Option 3 (Hybrid): Use LLM for extraction with full context,
    then use deterministic database queries for execution.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return _fallback_intent(message)

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    history = history or []

    # Build conversation messages with history
    messages = [{"role": "system", "content": _SYSTEM_HYBRID}]

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
        client = _openai_client(api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format=_make_response_format(),
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        obj = json.loads(raw)
        return _parse_intent_obj(obj, message)

    except Exception as e:
        logger.error("llm_extract_intent_hybrid_error: %s\n%s", repr(e), traceback.format_exc())
        return _fallback_intent(message)


def humanize_answer(text: str, lang: str) -> str:
    if not text:
        return text
    if os.getenv('HUMANIZE_ENABLED', 'false').lower() == 'false':
        return text
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if OpenAI is None or not api_key:
        return text
    try:
        client = _openai_client(api_key)
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


def humanize_answer_stream(text: str, lang: str):
    """
    Yields text chunks for streaming delivery to the frontend.

    - When HUMANIZE_ENABLED=true and OpenAI is reachable: yields real LLM tokens
      token-by-token (natural-language rewrite via streaming completions).
    - Default (HUMANIZE_ENABLED=false): yields the pre-formatted answer in
      ~3-word chunks so the frontend shows a typewriter effect without any
      extra LLM latency or cost.
    """
    if not text:
        return

    # LLM streaming path (opt-in via HUMANIZE_ENABLED env var)
    if os.getenv('HUMANIZE_ENABLED', 'false').lower() not in ('false', '0', 'no', ''):
        api_key = os.getenv('OPENAI_API_KEY', '').strip()
        if OpenAI and api_key:
            try:
                client = _openai_client(api_key)
                model = os.getenv('HUMANIZE_MODEL', 'gpt-4o-mini')
                sys_msg = (
                    'You are a friendly RTS assistant. Rewrite the answer to be clear and human. '
                    'Preserve all times, stop IDs, and route numbers exactly. Do not add facts.'
                )
                with client.chat.completions.create(
                    model=model,
                    messages=[
                        {'role': 'system', 'content': sys_msg},
                        {'role': 'user', 'content': text},
                    ],
                    temperature=0.2,
                    stream=True,
                ) as stream:
                    for chunk in stream:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            yield delta
                return
            except Exception:
                pass  # fall through to word-chunk mode

    # Default: word-chunked typewriter effect (no extra LLM call)
    words = text.split(' ')
    chunk_size = 3
    for i in range(0, len(words), chunk_size):
        chunk = ' '.join(words[i:i + chunk_size])
        if i + chunk_size < len(words):
            chunk += ' '
        yield chunk
