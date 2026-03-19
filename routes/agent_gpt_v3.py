"""
RTS Transit Assistant — GPT-4o-mini Agent with Clean Prompt  (v4)
==================================================================
Same clean system prompt and tool logic as agent_claude.py (v3),
but uses the OpenAI SDK (GPT-4o-mini) for ~5x lower API cost.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

try:
    from openai import OpenAI as _OpenAI
except ImportError:
    _OpenAI = None

from routes.agent_tools import TOOLS as _OPENAI_TOOLS, dispatch_tool
from routes.parsing_helpers import detect_language_simple

_MODEL = os.getenv("OPENAI_MODEL_V4", "gpt-4o-mini")
# Reuse the same OPENAI_API_KEY already used by v2
_API_KEY_ENV = "OPENAI_API_KEY_V4" if os.getenv("OPENAI_API_KEY_V4") else "OPENAI_API_KEY"
_MAX_TOOL_ITERATIONS = 5

# ── Shared system prompt (identical to agent_claude.py) ────────────────────────
from routes.agent_claude import SYSTEM_PROMPT
from routes.schedule_service import get_active_service_label

# ── Availability check ─────────────────────────────────────────────────────────

def _gpt_enabled() -> bool:
    if _OpenAI is None:
        return False
    return bool(os.getenv(_API_KEY_ENV, "").strip())


def _gpt_client():
    return _OpenAI(
        api_key=os.getenv(_API_KEY_ENV, ""),
        timeout=float(os.getenv("OPENAI_TIMEOUT", "30")),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
    )


# ── AGENT LOOP ────────────────────────────────────────────────────────────────

def handle_message(msg: str, history: list[dict], session_ctx: dict) -> dict:
    lang = detect_language_simple(msg)

    if not _gpt_enabled():
        return {
            "answer": (
                "I'm not able to process your request right now. "
                "Please call RTS Customer Service: (352) 334-2600 "
                "(Mon–Fri 8 AM–5 PM) or visit go-rts.com."
            ),
            "buttons": [],
            "meta": {"language": lang, "error": "openai_unavailable"},
        }

    # Inject today's date + 7-day service schedule
    now_et = datetime.now(_TZ)
    today = now_et.date()
    service_lines = []
    for i in range(7):
        d = today + timedelta(days=i)
        label = get_active_service_label(d)
        day_name = "Today" if i == 0 else ("Tomorrow" if i == 1 else d.strftime("%A"))
        service_lines.append(f"  {day_name} ({d.strftime('%b %d')}): {label}")
    service_block = "\n".join(service_lines)
    date_header = (
        f"TODAY is {now_et.strftime('%A, %B %d, %Y')} (Eastern Time). "
        "Use this to resolve relative dates like today, tomorrow, and day names.\n"
        f"RTS service schedule for the next 7 days:\n{service_block}\n"
        "Answer questions about service type (reduced, normal, Saturday, etc.) "
        "directly from this table — do not call a tool.\n"
        "When returning a schedule for a date that is NOT 'Regular Weekday', "
        "add a brief note at the end mentioning the service type.\n"
        "IMPORTANT: Never say a specific route 'has fewer trips' or 'is affected' by "
        "reduced service unless get_route_overview or get_schedule confirms it.\n"
        "When the user asks which buses/routes are affected, suspended, or not running on "
        "Reduced Service, Saturday, or Sunday — call get_service_differences with the "
        "appropriate service_type. Do not guess or refuse.\n\n"
    )
    system = date_header + SYSTEM_PROMPT

    # Build message list
    messages: list[dict] = [{"role": "system", "content": system}]
    for turn in (history or []):
        role = (turn.get("role") or "").lower()
        content = turn.get("content") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": msg})

    client = _gpt_client()
    tool_results_log: list[dict] = []
    tool_calls_made = 0

    for _ in range(_MAX_TOOL_ITERATIONS):
        try:
            response = client.chat.completions.create(
                model=_MODEL,
                messages=messages,
                tools=_OPENAI_TOOLS,
                tool_choice="auto",
                temperature=0,
                max_tokens=1024,
            )
        except Exception as exc:
            exc_str = str(exc)
            logger.error("agent_gpt_v3 LLM call failed: %s", exc_str)
            if "rate_limit" in exc_str.lower() or "429" in exc_str:
                return {
                    "answer": (
                        "I'm receiving too many requests right now. "
                        "You can check schedules at go-rts.com or call "
                        "RTS: (352) 334-2600 (Mon–Fri 8 AM–5 PM)."
                    ),
                    "buttons": [],
                    "meta": {"language": lang, "error": "rate_limit"},
                }
            return {
                "answer": (
                    "I'm having trouble connecting right now. "
                    "Please try again in a moment or call RTS: "
                    "(352) 334-2600 (Mon–Fri 8 AM–5 PM)."
                ),
                "buttons": [],
                "meta": {"language": lang, "error": exc_str},
            }

        choice = response.choices[0]
        finish_reason = choice.finish_reason
        tool_calls = choice.message.tool_calls or []

        if finish_reason == "stop" or not tool_calls:
            answer = (choice.message.content or "").strip()
            if not answer:
                answer = (
                    "I wasn't able to find that information. "
                    "For help call RTS: (352) 334-2600 (Mon–Fri 8 AM–5 PM) "
                    "or visit go-rts.com."
                )
            return {
                "answer": answer,
                "buttons": _build_buttons(tool_results_log, lang),
                "meta": {
                    "language": lang,
                    "tool_calls_made": tool_calls_made,
                    "model": _MODEL,
                    "agent": "gpt-v4",
                    "debug_tools": [
                        {"tool": t["tool"], "status": t["result"].get("status")}
                        for t in tool_results_log
                    ],
                },
            }

        # Append assistant message with tool calls
        tool_calls_made += len(tool_calls)
        messages.append(choice.message)

        # Execute tools and feed results back
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result = dispatch_tool(tc.function.name, args)
            tool_results_log.append({"tool": tc.function.name, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    # Max iterations hit
    logger.warning("agent_gpt_v3: max tool iterations for msg=%r", msg[:80])
    last_text = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "assistant":
            last_text = (m.get("content") or "").strip()
            if last_text:
                break
    return {
        "answer": last_text or (
            "I wasn't able to complete your request. "
            "Please call RTS: (352) 334-2600 (Mon–Fri 8 AM–5 PM)."
        ),
        "buttons": [],
        "meta": {
            "language": lang,
            "tool_calls_made": tool_calls_made,
            "warning": "max_iterations",
            "agent": "gpt-v4",
        },
    }


def _build_buttons(tool_results: list[dict], lang: str) -> list[dict]:
    buttons = []
    for tr in tool_results:
        name = tr["tool"]
        result = tr["result"]
        status = result.get("status", "")

        if name == "search_stops" and status == "multiple":
            for c in result.get("candidates", [])[:5]:
                sid = c.get("stop_id", "")
                sname = c.get("stop_name", sid)[:40]
                buttons.append({"label": f"Stop {sid} – {sname}", "action": f"stop {sid}"})

        elif name == "get_schedule" and status == "multiple_stops":
            for c in result.get("candidates", [])[:5]:
                sid = c.get("stop_id_padded") or c.get("stop_id", "")
                sname = (c.get("stop_name") or sid)[:40]
                buttons.append({"label": f"Stop {sid} – {sname}", "action": f"stop {sid}"})

        elif name == "search_routes" and status == "ok":
            for r in result.get("routes", [])[:6]:
                rid = r.get("route_id", "")
                rname = (r.get("route_long_name") or f"Route {rid}")[:45]
                buttons.append({"label": f"Route {rid} – {rname}", "action": f"route {rid}"})

    return buttons
