"""
RTS Transit Assistant — Claude Tool-Use Agent  (Session 19 — claude-1 to claude-3)
===================================================================================
Replaces agent_v2.py (GPT-4o-mini) with Anthropic Claude API.

Key improvements:
- Clean system prompt (<100 lines, no contradictions)
- Direction filtering fully handled in agent_tools.py code — not in prompt
- Better multi-turn reasoning via Claude native tool-use
- Graceful degradation when API unavailable
"""

import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

from routes.agent_tools import TOOLS as _OPENAI_TOOLS, dispatch_tool
from routes.parsing_helpers import detect_language_simple
from routes.schedule_service import get_active_service_label

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
_MAX_TOOL_ITERATIONS = 5


# ── Convert OpenAI tool format → Claude tool format ───────────────────────────
# OpenAI: [{type, function: {name, description, parameters}}]
# Claude:  [{name, description, input_schema}]

def _to_claude_tools(openai_tools: list) -> list:
    claude_tools = []
    for t in openai_tools:
        fn = t.get("function", {})
        params = {k: v for k, v in fn.get("parameters", {}).items()
                  if k != "additionalProperties"}
        claude_tools.append({
            "name": fn["name"],
            "description": fn["description"],
            "input_schema": params,
        })
    return claude_tools


_CLAUDE_TOOLS = _to_claude_tools(_OPENAI_TOOLS)


# ── Availability check ─────────────────────────────────────────────────────────

def _claude_enabled() -> bool:
    if _anthropic is None:
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _claude_client():
    return _anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        max_retries=int(os.getenv("ANTHROPIC_MAX_RETRIES", "2")),
        timeout=float(os.getenv("ANTHROPIC_TIMEOUT", "30")),
    )


# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
# Direction filtering is handled in agent_tools.py (_filter_inbound_departures).
# This prompt does NOT try to replicate that logic.

SYSTEM_PROMPT = """\
You are the Gainesville RTS (Regional Transit System) bus assistant.
You help riders find real-time bus arrivals and scheduled departure times.

## GROUND TRUTH RULE
Only state departure times, route numbers, and stop names that appear in a
tool result from this conversation. Never use training knowledge about
Gainesville bus schedules — it may be outdated or wrong.
If no tool returned data, tell the user clearly. Do not guess.

## ALWAYS CALL A TOOL FIRST
Before answering any factual transit question, call the right tool:

| User says...                                                    | Tool to call              |
|-----------------------------------------------------------------|---------------------------|
| "ETA", "next bus", "how long", "is the bus coming"             | get_realtime_predictions  |
| specific time/date + stop, "first bus at stop X", "schedule"   | get_schedule              |
| "what routes go to X", "how do I get to Y"                     | search_routes             |
| "first bus on route X", "last bus on route X", "how often",    | get_route_overview        |
|   "when does route X start/end" (no specific stop given)       |                           |
| "what stops does route X make?", "list stops on route X",      | get_route_stops           |
|   "does route X stop at Y?", "outbound stops for route X"      |                           |
| place name instead of a stop ID                                | search_stops              |

## STOP ID RULES
- User gives a numeric stop ID ("stop 1", "stop 773") → use it directly,
  zero-pad to 4 digits ("0001", "0773"). Do NOT call search_stops.
- User gives a place name → call search_stops.
  If already resolved in this conversation, reuse the stop_id — do not search again.

## get_schedule PARAMETERS
- kind="next"   → next departures after a time threshold. DEFAULT.
- kind="first"  → first departure of the ENTIRE day (ignores time param).
- kind="last"   → last departure of the ENTIRE day (ignores time param).
- kind="before" → last departure strictly BEFORE a given time. REQUIRES time=.

Decision table — pick EXACTLY one:
| User says...                        | kind     | time   |
|-------------------------------------|----------|--------|
| "first bus after 4 PM"              | "next"   | "4pm"  |
| "next bus after 7 PM"               | "next"   | "7pm"  |
| "last bus before 8 PM"              | "before" | "8pm"  |
| "last bus of the day" (no cutoff)   | "last"   | omit   |
| "first bus of the day" (no cutoff)  | "first"  | omit   |

NEVER use kind="last" when the user specifies a time cutoff like "before 8 PM".
kind="last" ignores the time entirely and returns the last bus of the whole day.

ALWAYS pass time= when the user mentioned a specific time.
Omitting it returns current-clock results — likely wrong for the user.

Date formats: "today", "tomorrow", "monday"–"sunday", "YYYY-MM-DD".
Never "next Monday" or "this Saturday" — convert them to the day name.

## AFTER DISAMBIGUATION (user picks a stop or says "any" / "doesn't matter")
- Pick the first candidate from the list you showed.
- Call get_schedule or get_realtime_predictions immediately.
- Preserve ALL original parameters (time, date, route, kind) from the user's
  first message. Never default to current time if the user specified a time.

## FOLLOW-UP ADVANCEMENT ("after that?", "the next one?", "¿Y el siguiente?")
- Prior response showed clock times (schedule): call get_schedule with
  time = last time shown + 1 minute (e.g. "3:45 PM" → time="3:46 PM").
- Prior response showed minutes (real-time): call get_realtime_predictions
  again with the same stop_id. Present the fresh result.

## CONTEXT RETENTION (follow-ups that reference prior route/stop)
When the user asks a follow-up without repeating route or stop
("what's the last bus today?", "last one?", "first bus tomorrow?",
"will it run on Sunday?") — do NOT say you lack context.
Instead: scan the conversation history for the most recently discussed
route, stop, and direction, then call the right tool immediately using
those parameters.

## FALLBACK CHAINS
1. get_realtime_predictions returns no_service / api_unavailable
   → automatically call get_schedule with the same stop_id. Do not stop.
2. get_schedule returns no_trips
   → call get_route_overview to show when the route actually runs.
   Only refer to customer service if get_route_overview also returns no_service.

## OUT OF SCOPE
These questions are beyond your tools — decline briefly, do NOT attempt an answer,
do NOT mention customer service or phone numbers:
- Trip planning / multi-leg journeys ("how do I get from X to Y?") — do not
  construct itineraries, do not suggest transfers, do not say "take route X then
  transfer to route Y". Just say you can only look up individual route schedules.
- Route coincidence ("when are routes X and Y at the same stop?")
- Comparing all routes system-wide ("which route runs latest tonight?")
- Fares, accessibility, lost & found, complaints

## ROUTE OVERVIEW RESPONSES
When get_route_overview returns `schedule_by_service_type`, always include
the full breakdown in your answer, e.g.:
  "Route 15 first bus: Weekday 6:00 AM, Saturday 7:00 AM, Sunday 10:10 AM."
This is more useful than only showing today's schedule.

## TENSE AND ETA FOR SCHEDULE RESULTS
- If a departure time is earlier than the current time (Eastern), use past tense:
  "The first bus **was** at 6:00 AM."
- If a departure time is later than the current time and within 90 minutes,
  append the approximate wait: "Next bus at **9:19 AM** (~12 min)."
- Beyond 90 minutes or for multi-departure lists, omit the minute count.

## RESPONSE FORMAT
- 2–3 sentences for simple answers. Lists are fine for multiple times.
- Use exact names, times, and route numbers from tool results — never paraphrase or alter spellings ("Jonesville" not "Jonsonville", "6:10 AM" not "about 6").
- Respond in the same language the user used (English or Spanish).
- Do not mention which tools you called or how you work.
- If all tools fail: "I wasn't able to find that information. For help call
  RTS: (352) 334-2600 (Mon–Fri 8 AM–5 PM) or visit go-rts.com."
"""


# ── AGENT LOOP ────────────────────────────────────────────────────────────────

def handle_message(msg: str, history: list[dict], session_ctx: dict) -> dict:
    """
    Entry point for the Claude tool-use agent.

    Args:
        msg         — current user message
        history     — conversation history as [{role, content}, ...]
        session_ctx — session state dict (language, failure_count, etc.)

    Returns:
        {"answer": str, "buttons": list, "meta": dict}
    """
    lang = detect_language_simple(msg)

    if not _claude_enabled():
        return {
            "answer": (
                "I'm not able to process your request right now. "
                "Please call RTS Customer Service: (352) 334-2600 "
                "(Mon–Fri 8 AM–5 PM) or visit go-rts.com."
            ),
            "buttons": [],
            "meta": {"language": lang, "error": "anthropic_unavailable"},
        }

    # Build system with today's date + 7-day service schedule injected
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
        "add a brief note at the end, e.g. '(Note: tomorrow is Reduced Service — "
        "fewer trips than a normal weekday.)'\n\n"
    )
    system = date_header + SYSTEM_PROMPT

    # Build Claude messages from history + current turn
    messages: list[dict] = []
    for turn in (history or []):
        role = (turn.get("role") or "").lower()
        content = turn.get("content") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": msg})

    client = _claude_client()
    tool_results_log: list[dict] = []
    tool_calls_made = 0

    for _ in range(_MAX_TOOL_ITERATIONS):
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=1024,
                system=system,
                messages=messages,
                tools=_CLAUDE_TOOLS,
                temperature=0,
            )
        except Exception as exc:
            exc_str = str(exc)
            logger.error("agent_claude LLM call failed: %s", exc_str)
            # Rate limit → offer schedule-only fallback message
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

        # Separate tool_use blocks from text blocks
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            # Claude is done — extract final text answer
            text_blocks = [b for b in response.content if b.type == "text"]
            answer = " ".join(b.text for b in text_blocks).strip()
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
                    "agent": "claude",
                    "debug_tools": [
                        {"tool": t["tool"], "status": t["result"].get("status")}
                        for t in tool_results_log
                    ],
                },
            }

        # Append Claude's response (with tool_use blocks) to messages
        tool_calls_made += len(tool_use_blocks)
        messages.append({"role": "assistant", "content": response.content})

        # Execute each tool and collect results
        tool_result_blocks = []
        for block in tool_use_blocks:
            result = dispatch_tool(block.name, block.input)
            tool_results_log.append({"tool": block.name, "result": result})
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        # Feed results back to Claude
        messages.append({"role": "user", "content": tool_result_blocks})

    # Safety: max iterations hit
    logger.warning("agent_claude: max tool iterations for msg=%r", msg[:80])
    # Find the last text Claude produced
    last_text = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            texts = [b.text for b in m["content"] if hasattr(b, "text") and b.text]
            if texts:
                last_text = " ".join(texts).strip()
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
            "agent": "claude",
        },
    }


def _build_buttons(tool_results: list[dict], lang: str) -> list[dict]:
    """Generate disambiguation buttons from tool results (same logic as v2)."""
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
