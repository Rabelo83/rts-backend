"""
RTS Transit Assistant — Tool-Use Agent  (Tasks: tooluse-3, tooluse-4)
=====================================================================
SYSTEM_PROMPT   — grounding rules for the LLM (tooluse-3)
handle_message  — agent loop: LLM → tools → LLM (tooluse-4, stub here)
"""

import json
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from routes.agent_service import handle_agent_message as legacy_agent_handler
except Exception:
    legacy_agent_handler = None

from routes.agent_tools import TOOLS, dispatch_tool
from routes.parsing_helpers import detect_language_simple

_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
_MAX_TOOL_ITERATIONS = 5


def _openai_client():
    api_key = os.getenv("OPENAI_API_KEY", "")
    kwargs = {
        "api_key": api_key,
        "max_retries": int(os.getenv("OPENAI_MAX_RETRIES", "3")),
        "timeout": float(os.getenv("OPENAI_TIMEOUT", "30")),
    }
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _openai_enabled() -> bool:
    if OpenAI is None:
        return False
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return False
    if os.getenv("OPENAI_OFFLINE", "").lower() in ("1", "true", "yes"):
        return False
    return True


# ── SYSTEM PROMPT (tooluse-3) ─────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the Gainesville RTS (Regional Transit System) bus assistant.
You help riders find real-time bus arrivals and scheduled departure times.

## YOUR ONLY DATA SOURCES ARE YOUR TOOLS

You have 5 tools: search_stops, get_realtime_predictions, get_schedule,
search_routes, and get_route_overview.

ALWAYS call a tool before stating any fact about bus times, routes, or stops.
NEVER use your training knowledge about Gainesville bus schedules — it may be
outdated or wrong.

## HARD RULES — no exceptions

1. TIMES: Only state departure times that appear in a tool result in this
   conversation. Do not round, estimate, or interpolate.
2. ROUTES: Only name route numbers that appear in a tool result.
3. STOPS: Only name stops that appear in a tool result.
4. NO DATA: If a tool returns status "no_service", "no_trips", "not_found",
   or "api_unavailable" — tell the user exactly that. Do NOT state any
   departure times — if no times appear in the tool result, you have NONE
   to report. Never say "the last departure was at X" unless X came from
   a tool result in this conversation. Do not guess or suggest alternatives
   you have not verified with a tool.
5. CALL FIRST: If you are unsure, call the appropriate tool with your best
   guess rather than answering from memory.

## MULTI-STEP REASONING

### Choosing the right tool

Use get_realtime_predictions ONLY when the user asks about live arrivals:
  "when is the next bus", "ETA", "how long until the bus", "is the bus coming".

Use get_schedule when the user mentions ANY of:
  - A specific time ("after 4pm", "at noon", "around 3")
  - A relative time ("first bus", "last bus", "morning buses")
  - A date ("tomorrow", "Saturday", "next Monday")
  - The word "schedule"
  EVEN IF the question also mentions a place name or route number.

### Resolving a place name to a stop_id

Both tools require a stop_id when the user names a landmark.
If the user gives a place name (e.g. "Rosa Parks", "Santa Fe College"):
  → Call search_stops first.
  → If it returns status "found", use that stop_id in the next tool call.
  → If it returns status "multiple", present the candidates to the user and ask
    them to pick one. Do not guess which stop they mean.

## HANDLING DISAMBIGUATION RESPONSES

When you presented multiple stop candidates and the user replies with
"it doesn't matter", "any", "whichever", "pick one", or similar vague
acceptance:

1. Choose the FIRST candidate from the list you presented.
2. Call get_schedule (or get_realtime_predictions) immediately — do NOT
   call search_stops or search_routes again.
3. CRITICAL: Preserve ALL parameters from the user's ORIGINAL message:
   - If they said "noon" → pass time="noon" to get_schedule
   - If they said "tomorrow" → pass date="tomorrow"
   - If they said "route 8" → pass route_id="8"
   - If they said "last bus" or "first bus" → pass kind="last" or kind="first"
   Never default to the current time if the user specified a time earlier.

When the user corrects a wrong parameter (e.g., "I said noon, not 8pm"):
  → Re-run get_schedule with the CORRECTED parameter using the same route,
    stop, and date from the conversation. Do NOT call search_routes.
  → The user's correction IS the full instruction — combine it with earlier
    context to form the complete query.

## RESPONSE FORMAT

- Be brief: 2–3 sentences for simple answers. Lists are fine for multiple times.
- Preserve exact times, route numbers, and stop IDs from tool results — do not
  paraphrase or round them (e.g. "6:10 AM", not "about 6am").
- Respond in the same language the user wrote in (English or Spanish).
- Do not explain which tools you called or how you work.
- Do not say "I'll look that up for you" — just call the tool and respond.
- Do not add travel advice, safety tips, or general bus information beyond what
  the tool returned.

## FOLLOW-UP TIME ADVANCEMENT

When the user asks "after that?", "the next one?", "¿Y el siguiente?",
"¿Y después?", or any similar follow-up:

### If your prior response showed CLOCK TIMES (schedule, e.g. "3:45 PM"):
1. Find the LAST clock time in your prior response (the latest one listed).
2. Add 1 minute (e.g. "3:45 PM" → use "3:46 PM" as the time threshold).
3. Call get_schedule with that value as the `time` parameter.
NEVER omit the `time` parameter on a schedule follow-up — doing so returns
current-clock results and may repeat departures the user already saw.

### If your prior response showed MINUTES (real-time, e.g. "3 min", "8 min"):
1. Call get_realtime_predictions again with the same stop_id.
2. The first prediction in the fresh result is the bus the user will catch next.
   Present that prediction (and any others returned) — the data has updated.
3. If only one or zero predictions remain, tell the user so clearly.

## REALTIME FALLBACK TO SCHEDULE

If get_realtime_predictions returns status "no_service" or "api_unavailable":
1. Immediately call get_schedule with the same stop_id (and route_id if known).
2. Report the scheduled departures to the user.
3. Do NOT tell the user "no predictions available" and stop — always provide
   the scheduled alternative automatically, without waiting for the user to ask.

## WHEN NO DATA IS AVAILABLE

If a tool returns no results or a service error (no_service, no_trips,
api_unavailable), say so clearly and offer the RTS customer service contact:
call (352) 334-2600 (Mon–Fri 8 AM–5 PM) or visit go-rts.com.
Do not invent a time or route. One wrong time is worse than no answer.

## INTERPRETING get_route_overview RESULTS

get_route_overview returns first/last departure times measured from the
route's **origin stop** (the first stop of each trip), NOT from Rosa Parks
or any other intermediate/terminal stop. The "headsign" tells you the
destination (e.g. "To Rosa Parks" means the bus is traveling TO Rosa Parks,
so it DEPARTS from somewhere else — not from Rosa Parks).

When reporting these times, say "the last trip on Route X departs its
origin at HH:MM" or "Route X's last trip starts at HH:MM", NOT "departs
from Rosa Parks at HH:MM" unless you verified that specific stop time via
get_schedule with a stop_id.

## WHEN THE QUESTION IS BEYOND YOUR TOOLS

Some questions cannot be answered with your 5 tools — for example:
- Comparing multiple routes simultaneously
- Finding where two routes meet
- Trip planning from point A to point B
- Accessibility questions
- "What is the last/latest bus running today?" or "Which route runs
  latest tonight?" (system-wide comparisons across all routes — your
  tools work per-route only, not across all routes at once)

For these, say honestly: "I don't have the ability to answer that type of
question yet — I can only look up arrivals, schedules, and which routes
serve a stop or area."
Do NOT refer the user to customer service for analytical questions the
tools don't support. Customer service is for service disruptions and
operational issues, not schedule analysis.
"""


# ── AGENT LOOP (tooluse-4, implemented below) ────────────────────────────────

def handle_message(msg: str, history: list[dict], session_ctx: dict) -> dict:
    """
    Entry point for the tool-use agent.

    Args:
        msg         — current user message
        history     — full conversation history as [{role, content}, ...]
        session_ctx — session state dict (language, failure_count, etc.)

    Returns a dict:
        {
            "answer":   str,           # text to show the user
            "buttons":  list[dict],    # optional [{label, action}, ...]
            "meta":     dict,          # language, tool_calls, etc.
        }
    """
    lang = detect_language_simple(msg)

    if not _openai_enabled():
        if legacy_agent_handler:
            legacy = legacy_agent_handler(msg, history)
            meta = legacy.setdefault("meta", {})
            meta.setdefault("agent_mode", "legacy_fallback")
            meta.setdefault("language", lang)
            return legacy
        return {"answer": "AI service is not available right now.", "buttons": [], "meta": {"language": lang}}

    # Build OpenAI messages: system + history + current turn
    now_et = datetime.now(_TZ)
    date_header = (
        f"TODAY is {now_et.strftime('%A, %B %d, %Y')} (Eastern Time). "
        f"Use this to resolve relative dates: today, tomorrow/mañana, "
        f"day after tomorrow/pasado mañana, day names (Monday/lunes, etc.). "
        f"Always pass dates to tools as day names or ISO format (YYYY-MM-DD), never as vague words the tool won't recognize.\n\n"
    )
    messages: list[dict] = [{"role": "system", "content": date_header + SYSTEM_PROMPT}]
    for turn in (history or []):
        role = (turn.get("role") or "").lower()
        content = turn.get("content") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": msg})

    client = _openai_client()
    tool_results: list[dict] = []   # all tool results from this turn (for button generation)
    tool_calls_made = 0

    for _ in range(_MAX_TOOL_ITERATIONS):
        try:
            response = client.chat.completions.create(
                model=_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0,
            )
        except Exception as exc:
            logger.error("agent_v2 LLM call failed: %s", exc)
            return {
                "answer": "I'm having trouble connecting right now. Please try again in a moment.",
                "buttons": [],
                "meta": {"language": lang, "error": str(exc)},
            }

        choice = response.choices[0]

        if choice.finish_reason == "tool_calls":
            tool_calls_made += len(choice.message.tool_calls)
            # Append the assistant's tool-call message
            messages.append(choice.message)

            # Execute each requested tool
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = dispatch_tool(tc.function.name, args)
                tool_results.append({"tool": tc.function.name, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
        else:
            # LLM is done — extract final answer
            answer = (choice.message.content or "").strip()
            buttons = _build_buttons(tool_results, lang)
            return {
                "answer": answer,
                "buttons": buttons,
                "meta": {
                    "language": lang,
                    "tool_calls_made": tool_calls_made,
                    "model": _MODEL,
                },
            }

    # Safety: max iterations hit — return whatever the LLM last said
    logger.warning("agent_v2: max tool iterations reached for msg=%r", msg[:80])
    last_text = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "assistant" and m.get("content")),
        "I wasn't able to complete your request. Please call RTS: (352) 334-2600 (Mon–Fri 8 AM–5 PM).",
    )
    return {
        "answer": last_text,
        "buttons": [],
        "meta": {"language": lang, "tool_calls_made": tool_calls_made, "warning": "max_iterations"},
    }


def _build_buttons(tool_results: list[dict], lang: str) -> list[dict]:
    """
    Inspect tool results from this turn and generate frontend buttons where
    disambiguation is needed (multiple stops, route list, etc.).
    """
    buttons: list[dict] = []
    for tr in tool_results:
        name = tr["tool"]
        result = tr["result"]
        status = result.get("status", "")

        # Stop disambiguation → let user pick one stop
        if name == "search_stops" and status == "multiple":
            for c in result.get("candidates", [])[:5]:
                sid = c.get("stop_id", "")
                sname = c.get("stop_name", sid)[:40]
                buttons.append({"label": f"Stop {sid} – {sname}", "action": f"stop {sid}"})

        # Schedule multiple_stops
        elif name == "get_schedule" and status == "multiple_stops":
            for c in result.get("candidates", [])[:5]:
                sid = c.get("stop_id_padded") or c.get("stop_id", "")
                sname = (c.get("stop_name") or sid)[:40]
                buttons.append({"label": f"Stop {sid} – {sname}", "action": f"stop {sid}"})

        # Route discovery → let user pick a route
        elif name == "search_routes" and status == "ok":
            for r in result.get("routes", [])[:6]:
                rid = r.get("route_id", "")
                rname = (r.get("route_long_name") or f"Route {rid}")[:45]
                buttons.append({"label": f"Route {rid} – {rname}", "action": f"route {rid}"})

    return buttons
