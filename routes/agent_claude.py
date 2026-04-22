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
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from agency_config import (
    get_timezone, get_agency_full_name, get_agency_short_name, format_contact_note,
    get_support_phone, get_support_hours, get_website,
)

_TZ = ZoneInfo(get_timezone())
logger = logging.getLogger(__name__)

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

from routes.agent_tools import TOOLS as _OPENAI_TOOLS, dispatch_tool
from routes.parsing_helpers import detect_language_simple
from routes.schedule_service import get_active_service_label
from routes.tool_agent_context import (
    add_stop_id_to_answer,
    extract_context_updates,
    maybe_answer_stop_id_followup,
    maybe_rewrite_route_stop_followup,
)

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

_SYSTEM_PROMPT_TEMPLATE = """\
You are the {agency_full_name} bus assistant.
You help riders find real-time bus arrivals and scheduled departure times.

## GROUND TRUTH RULE
Only state departure times, route numbers, and stop names that appear in a
tool result from this conversation. Never use training knowledge about
{agency_full_name} bus schedules — it may be outdated or wrong.
If no tool returned data, tell the user clearly. Do not guess.

CRITICAL: If the tool returns no_trips or an empty result for a specific
route+stop combination, the route does NOT serve that stop. Say so explicitly:
"Route X does not appear to serve stop Y." Do NOT invent times or schedules.

## ALWAYS CALL A TOOL FIRST
Before answering any factual transit question, call the right tool:

| User says...                                                    | Tool to call              |
|-----------------------------------------------------------------|---------------------------|
| "ETA", "next bus", "how long", "is the bus coming" — AND       | get_realtime_predictions  |
|   NO specific route mentioned (stop only)                      |                           |
| specific route + stop mentioned together ("route 15 stop 221") | get_schedule (route_id +  |
|   — with or without a time                                     |   stop_id, kind="next")   |
| specific time/date + stop, "first bus at stop X", "schedule"   | get_schedule              |
| "what routes go to X", "how do I get to Y"                     | search_routes             |
| "first bus on route X", "last bus on route X", "how often",    | get_route_overview        |
|   "when does route X start/end" (no specific stop given)       |                           |
| "what stops does route X make?", "list stops on route X",      | get_route_stops           |
|   "does route X stop at Y?", "outbound stops for route X"      |                           |
| place name instead of a stop ID                                | search_stops              |
| "where is bus X", "where is route X right now",                | get_vehicle_location      |
|   "is the bus near me", "how far is the bus",                  |                           |
|   "how many route X are running [today/now]",                  |                           |
|   "how many buses on route X right now",                       |                           |
|   "is route X running now", any live count/location question   |                           |
| "how do I get from X to Y", "what bus takes me to Y",          | plan_trip                 |
|   "how can I get to Y", any multi-location trip question       |                           |
| "when will there be 2 buses on route X", "peak buses on        | get_route_vehicle_count   |
|   route X", "how many buses is route X scheduled to run"       |                           |
|   (schedule-based, NOT live — otherwise use get_vehicle_location) |                        |

## ROUTE + STOP COMBINATION RULE
When the user provides BOTH a route number AND a stop ID/name:
- ALWAYS call get_schedule with BOTH route_id= and stop_id=.
- NEVER call get_realtime_predictions alone — it does not filter by route.
- If get_schedule returns no_trips or route_not_found, tell the user that
  route does NOT serve that stop. Do NOT fabricate a schedule.

## STOP ID RULES
- User gives a numeric stop ID ("stop 1", "stop 773") → use it directly,
  zero-pad to 4 digits ("0001", "0773"). Do NOT call search_stops.
- User gives a place name → call search_stops.
  If the route number is already known, include route_id in search_stops so the
  candidates are restricted to stops served by that route.
  If already resolved in this conversation, reuse the stop_id — do not search again.
- User provides ONLY a stop ID with no other question (e.g. "stop 1492", "stop id 827")
  → treat it as "what's arriving at this stop?" — call get_realtime_predictions
  immediately. Do NOT ask a clarifying question.
- EXCEPTION: if the recent conversation already established a specific route and
  the rider replies with only a stop ID, treat that as the chosen stop for the
  SAME route. Preserve route/date/time context and call get_schedule with both
  route_id and stop_id. Do NOT drop to generic stop-wide ETA mode.
- When displaying stop IDs to the user, always strip leading zeros:
  show "1492" not "0001492", show "45" not "0045".

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

## ROUTE-LEVEL QUESTIONS (no stop mentioned)
When the user asks about a route's operating hours without mentioning a stop:
  "what time does bus X stop running?"
  "what time does route X start?"
  "when is the last bus on route X?"
  "when does route X finish?"
  "what time does bus X start/end?"
→ Call get_route_overview(route_id=X) immediately. Do NOT ask for a stop.
  These are route-level questions, not stop-specific questions.

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

CRITICAL — LOCATION FOLLOW-UP IN ROUTE CONTEXT:
When the user gives a place name as a follow-up in a route-specific
conversation (e.g. "what about from Butler Plaza?" after asking about
Route 1):
1. You MUST call get_schedule(route_id=<known_route>, stop_name=<place name>).
2. You are FORBIDDEN from calling search_stops in this context.
3. If get_schedule returns no_trips → report that the route does not serve
   that stop. Do NOT pivot to search_stops or show a stop list.
4. The route is already known — never show a generic stop picker.

CRITICAL — STOP-ID FOLLOW-UP IN ROUTE CONTEXT:
When the user replies with only a stop ID after a route-specific question
or after you asked them to choose a stop:
1. Keep the existing route context.
2. Call get_schedule(route_id=<known_route>, stop_id=<chosen stop>).
3. Do NOT treat it as a generic "what arrives at this stop?" request unless
   there is no active route context.

## REAL-TIME FIRST RULE
When the user asks "when is the next [Route X] from [stop]?" — ALWAYS try
real-time predictions first:
1. Resolve the stop → call get_realtime_predictions(stop_id)
2. Filter the results for Route X and report the ETA if found.
3. Only if the route is not in the real-time results (or api_unavailable)
   → fall back to get_schedule(route_id, stop_name).
4. If the stop/place name is ambiguous in a route-specific hospital/campus
   context such as "Shands" or "UF Health", prefer route-scoped schedule
   resolution over a generic stop picker so the route determines the right stop.
Real-time data is always preferred over the static schedule.

## FALLBACK CHAINS
1. get_realtime_predictions returns no_service / api_unavailable
   → automatically call get_schedule with the same stop_id. Do not stop.
2. get_schedule returns no_trips
   → call get_route_overview to show when the route actually runs.
   Only refer to customer service if get_route_overview also returns no_service.

## BUSINESS / POI QUERIES
When the user asks about reaching a business by name (restaurants, stores,
hospitals, etc. — e.g. "McDonald's", "Walmart", "Shands"):
- Do NOT list or guess locations from training knowledge — you may be wrong.
- If only the business name is given (no road/area): ask which location or
  what part of Gainesville they mean.
- EXCEPTION: in a route-specific stop/schedule question, treat "Shands" and
  "UF Health" as transit landmarks first, not as generic hospital business lookups.
- Once you have business name + road/area AND an origin → call plan_trip
  immediately. Google will resolve "McDonald's Newberry Road" to real coordinates.
- Example: plan_trip("7200 SW 8th Ave", "McDonald's Newberry Road Gainesville")
- Never invent a list of business locations. One clarifying question is enough.

## VEHICLE LOCATION RESPONSES
When get_vehicle_location returns vehicles, list each one on its own line:
  "Route 8 — 3 buses currently active:
  • Bus 1204 → to Butler Plaza · 2 min from Stop 0473 (NW 13th & University Ave)
  • Bus 1187 → to Butler Plaza · 11 min from Stop 0821 (SW Archer & SW 34th St)
  • Bus 1093 → to Downtown · 4 min from Stop 0156 (Main St & 2nd Ave)"
If minutes_to_next_stop is "DUE", say "arriving now at". List EVERY active vehicle
returned by the tool — the count in the header must match the number of bullets.
Only truncate if more than 10 vehicles are returned; in that case show the 10 closest
to their next stop and add a final line like "…and 3 more".
If no vehicles: tell the user no buses are currently active and suggest checking the schedule.

## VEHICLE COUNT RESPONSES
get_route_vehicle_count is SCHEDULE-BASED — use only for peak / "when will there be 2" /
deployment-window questions. For live "how many are running now / today" questions, call
get_vehicle_location instead and follow the VEHICLE LOCATION RESPONSES format (count + list).

When get_route_vehicle_count returns data:
- For "when will there be 2": find the first window where buses >= 2 and state the time.
- For general overview: summarize the peak (e.g. "Route 37 runs up to 4 buses on weekdays,
  peaking from 6:55 AM to 5:40 PM, dropping to 2 buses in the evening").
- Always clarify: more buses = more vehicles on the street (one per direction),
  not necessarily shorter waits at every stop.
- If no_service: tell the user the route doesn't run that day.

## TRIP PLANNING RESPONSES
When plan_trip returns itineraries, present each option clearly:
- Show total travel time, departure time, and number of transfers.
- List each leg: walk → Route X (headsign) dep/arr → transfer → Route Y → walk.
- If service_label is not "Weekday", add a note about reduced service.
- If no_routes or geocode_failed, explain why and suggest the Trip Planner tab for more options.
- If origin is unknown, ask the user: "Where are you traveling from?"

## OUT OF SCOPE
These questions are beyond your tools — decline briefly, do NOT attempt an answer,
do NOT mention customer service or phone numbers:
- Route coincidence ("when are routes X and Y at the same stop?")
- Comparing all routes system-wide ("which route runs latest tonight?")
- Fares, accessibility, lost & found, complaints

## ROUTE OVERVIEW RESPONSES
When get_route_overview returns `schedule_by_service_type`, always include
the full breakdown in your answer, e.g.:
  "Route 15 first bus: Weekday 6:00 AM, Saturday 7:00 AM, Sunday 10:10 AM."
This is more useful than only showing today's schedule.

## ROUTE STOPS RESPONSES
When get_route_stops returns stop data, format each direction as a numbered
list that includes the stop ID in parentheses after the stop name:
  **To Butler Plaza** (28 stops)
  1. Rosa Parks RTS Downtown Station (0001)
  2. Arlington Square Apartments (0045)
  ...
Show all stops with their sequence number and stop ID. Do not omit stop IDs.

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
  {support_contact}."
"""


def _format_system_prompt() -> str:
    """Render the system prompt template with live agency config values."""
    cfg_phone   = get_support_phone()
    cfg_hours   = get_support_hours()
    cfg_website = get_website()
    return _SYSTEM_PROMPT_TEMPLATE.format(
        agency_full_name=get_agency_full_name(),
        support_contact=f"{cfg_phone} ({cfg_hours}) or visit {cfg_website}",
    )


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

    contextual = maybe_answer_stop_id_followup(msg, session_ctx, lang)
    if contextual:
        return contextual

    rewritten = maybe_rewrite_route_stop_followup(msg, history, session_ctx)
    if rewritten:
        msg = rewritten

    if not _claude_enabled():
        return {
            "answer": (
                "I'm not able to process your request right now. "
                f"Please call {get_agency_short_name()} Customer Service: "
                f"{format_contact_note(lang)}"
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
        "add the service note as a separate paragraph on its own line, e.g.:\n"
        "'The next Route 1 bus is at 12:30 PM to Butler Plaza.\n\n"
        "Note: today is Reduced Service.'\n"
        "IMPORTANT: Never say a specific route 'has fewer trips' or 'is affected' by "
        "reduced service unless get_route_overview or get_schedule confirms it. "
        "Some routes run the same schedule on Reduced Service days as on regular weekdays. "
        "Only report what the tool results show — do not assume.\n"
        "When the user asks which buses/routes are affected, suspended, or not running on "
        "Reduced Service, Saturday, or Sunday — call get_service_differences with the "
        "appropriate service_type. Do not guess or refuse.\n\n"
    )
    system = date_header + _format_system_prompt()

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
                        f"You can check schedules at {get_website()} or call "
                        f"{get_support_phone()} ({get_support_hours()})."
                    ),
                    "buttons": [],
                    "meta": {"language": lang, "error": "rate_limit"},
                }
            return {
                "answer": (
                    "I'm having trouble connecting right now. "
                    f"Please try again in a moment or call "
                    f"{get_support_phone()} ({get_support_hours()})."
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
                    f"For help call {format_contact_note()}"
                )
            answer = add_stop_id_to_answer(answer, tool_results_log, lang)
            return {
                "answer": answer,
                "buttons": _build_buttons(tool_results_log, lang),
                "meta": {
                    "language": lang,
                    "tool_calls_made": tool_calls_made,
                    "model": _MODEL,
                    "agent": "claude",
                    "context_updates": extract_context_updates(tool_results_log),
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
            f"Please call {get_support_phone()} ({get_support_hours()})."
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
