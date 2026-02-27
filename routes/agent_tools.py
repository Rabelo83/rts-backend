"""
RTS Transit Assistant — Tool-Use Agent Schema  (Task: tooluse-1)
================================================================
This file defines the contract between the LLM agent loop (agent_v2.py) and
the underlying transit services. It has two parts:

  PART A — TOOLS
      The list of OpenAI function-call definitions passed to the API.
      These are the ONLY actions the LLM can take to get transit data.

  PART B — RETURN SCHEMAS
      Documented contracts for what each tool wrapper must return.
      Implementations live in this same file (added in tooluse-2).

Design rules:
  - Every return dict MUST include a "status" key. Never return a bare empty
    list or None — the LLM has no way to reason about silence.
  - All status strings are lowercase snake_case constants.
  - Times in return values are human-readable strings ("3:15 PM"), not raw
    GTFS "HH:MM:SS" strings — the LLM should not need to format them.
  - Stop IDs are always 4-digit zero-padded strings ("0001", "0520").
"""

# ── PART A: OpenAI function-call definitions ─────────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_stops",
            "description": (
                "Resolve a stop name or landmark to a GTFS stop ID. "
                "Call this before get_realtime_predictions or get_schedule "
                "whenever the user mentions a place name instead of a stop ID. "
                "Examples: 'Rosa Parks', 'Santa Fe College', 'Reitz Union'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "The stop name or landmark exactly as the user described it. "
                            "Do not paraphrase or abbreviate."
                        ),
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_realtime_predictions",
            "description": (
                "Get live real-time bus arrival predictions at a stop. "
                "Use this when the user asks 'when is the next bus', 'ETA', "
                "'how long until the bus', or any question about live arrivals. "
                "Requires a 4-digit stop ID — call search_stops first if you "
                "only have a place name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "stop_id": {
                        "type": "string",
                        "description": "4-digit zero-padded GTFS stop ID (e.g. '0001', '0520').",
                    }
                },
                "required": ["stop_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_schedule",
            "description": (
                "Get scheduled departure times from the static GTFS timetable. "
                "Use this when the user asks about a specific time of day, "
                "a specific date, 'first bus', 'last bus', or 'schedule'. "
                "If route_id is omitted, returns departures for ALL routes at "
                "that stop. Requires stop_id OR stop_name (not both needed)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route_id": {
                        "type": "string",
                        "description": (
                            "Route number (e.g. '43', '75'). "
                            "Omit to get all routes serving the stop."
                        ),
                    },
                    "stop_id": {
                        "type": "string",
                        "description": "4-digit stop ID if already resolved.",
                    },
                    "stop_name": {
                        "type": "string",
                        "description": (
                            "Stop name or landmark if stop_id is unknown. "
                            "The tool will resolve it internally."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["next", "first", "last"],
                        "description": (
                            "'next' = next departures after a given time (default). "
                            "'first' = first departure of the day. "
                            "'last' = last departure of the day."
                        ),
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "Time threshold for 'next' queries. "
                            "Accepts: '3pm', '15:30', 'morning', 'afternoon', 'evening', 'now'. "
                            "Omit to use current time."
                        ),
                    },
                    "date": {
                        "type": "string",
                        "description": (
                            "Date of travel. "
                            "Accepts: 'today', 'tomorrow', 'monday' … 'sunday', 'YYYY-MM-DD'. "
                            "Omit to use today."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_routes",
            "description": (
                "Find which RTS routes serve a destination or area. "
                "Use this when the user asks 'what buses go to X', "
                "'which routes serve Y', or 'how do I get to Z'. "
                "Returns route numbers and names, not departure times."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": (
                            "The destination or area the user wants to reach. "
                            "Examples: 'UF', 'downtown', 'Santa Fe College', "
                            "'Butler Plaza', 'Shands'."
                        ),
                    }
                },
                "required": ["destination"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_route_overview",
            "description": (
                "Get a high-level schedule summary for a route on a given date: "
                "first bus, last bus, and average frequency per direction. "
                "Use this when the user asks 'when does route X start/end', "
                "'how often does the 43 run', or asks for a route's schedule "
                "without specifying a stop yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route_id": {
                        "type": "string",
                        "description": "Route number (e.g. '43', '75').",
                    },
                    "date": {
                        "type": "string",
                        "description": (
                            "Date to check. "
                            "Accepts: 'today', 'tomorrow', 'monday' … 'sunday', 'YYYY-MM-DD'. "
                            "Omit to use today."
                        ),
                    },
                },
                "required": ["route_id"],
                "additionalProperties": False,
            },
        },
    },
]


# ── PART B: Return schema contracts ──────────────────────────────────────────
#
# Each tool wrapper (implemented in tooluse-2) MUST return one of the dicts
# described below. The LLM will read these values directly.
#
# search_stops(name: str) → dict
# ─────────────────────────────
#   Single match:
#     {"status": "found", "stop_id": "0001", "stop_name": "Rosa Parks Downtown Station"}
#   Multiple candidates (show user to pick):
#     {"status": "multiple", "candidates": [{"stop_id": "0520", "stop_name": "Santa Fe College Gainesville Campus"}, ...]}
#   No match:
#     {"status": "not_found", "name": "Walmart on Archer", "message": "No stops found matching that name."}
#
# get_realtime_predictions(stop_id: str) → dict
# ──────────────────────────────────────────────
#   Predictions available:
#     {"status": "ok", "stop_id": "0001", "stop_name": "Rosa Parks Downtown Station",
#      "predictions": [{"route": "10", "headsign": "To Butler Plaza TS", "minutes": 3, "delayed": false},
#                      {"route": "43", "headsign": "To Santa Fe College", "minutes": 12, "delayed": false}]}
#   No buses coming:
#     {"status": "no_service", "stop_id": "0001", "stop_name": "...",
#      "message": "No active bus predictions at this stop right now."}
#   API unreachable:
#     {"status": "api_unavailable", "message": "Bustime real-time API is not responding. Try the schedule instead."}
#
# get_schedule(route_id?, stop_id?, stop_name?, kind?, time?, date?) → dict
# ─────────────────────────────────────────────────────────────────────────
#   Next departures (single route):
#     {"status": "ok", "route": "43", "stop": "Santa Fe College", "date": "Feb 27",
#      "departures": [{"time": "3:15 PM", "headsign": "To Rosa Parks"}, ...]}
#   Next departures (all routes at stop, no route_id given):
#     {"status": "ok", "stop": "Rosa Parks Downtown Station", "date": "Feb 27",
#      "departures": [{"route": "10", "time": "3:18 PM", "headsign": "To Butler"}, ...]}
#   First bus of the day:
#     {"status": "ok_first", "route": "43", "stop": "...", "date": "Feb 27", "first_departure": "6:10 AM"}
#   Last bus of the day:
#     {"status": "ok_last", "route": "43", "stop": "...", "date": "Feb 27", "last_departure": "9:45 PM"}
#   No trips in that window:
#     {"status": "no_trips", "route": "43", "stop": "...", "date": "Feb 27", "after": "10:00 PM",
#      "message": "No scheduled departures found after 10:00 PM. Last bus today was at 9:45 PM."}
#   Stop not found:
#     {"status": "stop_not_found", "route": "43", "query": "downtown",
#      "message": "No stop matching 'downtown' found on Route 43."}
#   Ambiguous stop (multiple matches):
#     {"status": "multiple_stops", "route": "43",
#      "candidates": [{"stop_id": "0520", "stop_name": "Santa Fe College Gainesville Campus"}, ...]}
#   Route not in GTFS:
#     {"status": "route_not_found", "route": "99", "message": "Route 99 not found in the schedule database."}
#   DB unavailable:
#     {"status": "db_unavailable", "message": "Schedule database unavailable. Try the live tracker instead."}
#
# search_routes(destination: str) → dict
# ───────────────────────────────────────
#   Routes found:
#     {"status": "ok", "destination": "UF",
#      "routes": [{"route_id": "5", "route_long_name": "University Ave to Butler Plaza TS"}, ...]}
#   No routes found:
#     {"status": "not_found", "destination": "Walmart on Archer",
#      "message": "No RTS routes found serving that destination."}
#
# get_route_overview(route_id: str, date?: str) → dict
# ──────────────────────────────────────────────────────
#   Route runs today:
#     {"status": "ok", "route": "43", "route_name": "Santa Fe College to Rosa Parks TS",
#      "date": "Feb 27", "day_label": "Thursday (weekday)",
#      "directions": [{"headsign": "To Santa Fe College", "first": "6:00 AM", "last": "9:45 PM",
#                      "frequency": "every ~30 min"},
#                     {"headsign": "To Rosa Parks", "first": "6:15 AM", "last": "10:00 PM",
#                      "frequency": "every ~30 min"}]}
#   Route does not run on this date:
#     {"status": "no_service", "route": "43", "date": "Feb 28", "day_label": "Sunday",
#      "message": "Route 43 does not run on Sunday."}
#   Route not in GTFS:
#     {"status": "route_not_found", "route": "99", "message": "Route 99 not found in the schedule database."}
#   DB unavailable:
#     {"status": "db_unavailable", "message": "Schedule database unavailable."}


# ── PART C: Tool dispatcher + implementations ────────────────────────────────

import logging
from datetime import date as _date

import routes.schedule_service as _sched
from routes.stop_resolver import resolve_stop_global, get_predictions_cached
from routes.parsing_helpers import format_time_12h

logger = logging.getLogger(__name__)


def _fmt_date(iso: str) -> str:
    """'2026-02-27' → 'Feb 27'"""
    try:
        return _date.fromisoformat(iso).strftime("%b %d").lstrip("0")
    except Exception:
        return iso


def _stop_name_from_gtfs(stop_id: str) -> str:
    """Look up stop name from GTFS by stop_id_padded. Returns stop_id on failure."""
    conn = _sched.connect_db()
    if not conn:
        return stop_id
    try:
        row = conn.execute(
            "SELECT stop_name FROM stops WHERE stop_id_padded = ?", (stop_id,)
        ).fetchone()
        return row["stop_name"] if row else stop_id
    except Exception:
        return stop_id
    finally:
        conn.close()


def dispatch_tool(name: str, arguments: dict) -> dict:
    """
    Called by the agent loop for each tool_call the LLM requests.
    Routes to the correct wrapper function by name.
    Returns a dict matching the schema in PART B above.
    """
    handlers = {
        "search_stops": _tool_search_stops,
        "get_realtime_predictions": _tool_get_realtime_predictions,
        "get_schedule": _tool_get_schedule,
        "search_routes": _tool_search_routes,
        "get_route_overview": _tool_get_route_overview,
    }
    handler = handlers.get(name)
    if not handler:
        logger.error("Unknown tool called: %s", name)
        return {"status": "error", "message": f"Unknown tool: {name}"}
    try:
        return handler(**arguments)
    except Exception as exc:
        logger.exception("Tool %s failed: %s", name, exc)
        return {"status": "error", "message": f"Tool {name} encountered an error."}


# ── Tool 1: search_stops ─────────────────────────────────────────────────────

def _tool_search_stops(name: str) -> dict:
    """Resolve a stop name or landmark to a GTFS stop ID."""
    result = resolve_stop_global(name)
    if not result:
        return {
            "status": "not_found",
            "name": name,
            "message": f"No stops found matching '{name}'. Try the 4-digit Stop ID from the sign.",
        }
    if "stop_id" in result:
        return {
            "status": "found",
            "stop_id": result["stop_id"],
            "stop_name": result["stop_name"],
        }
    # candidates
    return {
        "status": "multiple",
        "name": name,
        "candidates": result["candidates"],
    }


# ── Tool 2: get_realtime_predictions ────────────────────────────────────────

def _tool_get_realtime_predictions(stop_id: str) -> dict:
    """Get live Bustime predictions at a stop."""
    try:
        data = get_predictions_cached(stop_id) or {}
    except Exception as exc:
        logger.warning("Bustime predictions failed for %s: %s", stop_id, exc)
        return {
            "status": "api_unavailable",
            "message": "Bustime real-time API is not responding. Try the schedule instead.",
        }

    preds = data.get("prd") or []
    if not preds:
        stop_name = _stop_name_from_gtfs(stop_id)
        return {
            "status": "no_service",
            "stop_id": stop_id,
            "stop_name": stop_name,
            "message": f"No active bus predictions at {stop_name} right now.",
        }

    # Use stop name from first prediction (Bustime returns stpnm per prediction)
    stop_name = (preds[0].get("stpnm") or "").strip() or _stop_name_from_gtfs(stop_id)

    formatted = []
    for p in preds:
        rt = str(p.get("rt") or "").strip()
        headsign = str(p.get("des") or "").strip()
        ctdn = str(p.get("prdctdn") or "").strip()
        delayed = bool(p.get("dly"))
        try:
            minutes = int(ctdn)
        except ValueError:
            minutes = ctdn  # "DUE" or other string
        formatted.append({
            "route": rt,
            "headsign": headsign,
            "minutes": minutes,
            "delayed": delayed,
        })

    return {
        "status": "ok",
        "stop_id": stop_id,
        "stop_name": stop_name,
        "predictions": formatted,
    }


# ── Tool 3: get_schedule ─────────────────────────────────────────────────────

def _tool_get_schedule(
    route_id: str | None = None,
    stop_id: str | None = None,
    stop_name: str | None = None,
    kind: str = "next",
    time: str | None = None,
    date: str | None = None,
) -> dict:
    """Get scheduled departures from GTFS. Works with or without route_id."""
    # Build a natural-language text string the existing parsers can handle
    text_parts = []
    if time:
        text_parts.append(time)
    if date:
        text_parts.append(date)
    text_query = " ".join(text_parts) or "now"

    # ── No route: return next departures for ALL routes at the stop ──────────
    if not route_id:
        # Resolve stop_id if only name given
        if not stop_id and stop_name:
            resolved = resolve_stop_global(stop_name)
            if not resolved:
                return {
                    "status": "stop_not_found",
                    "query": stop_name,
                    "message": f"No stop found matching '{stop_name}'.",
                }
            if "candidates" in resolved:
                return {"status": "multiple_stops", "candidates": resolved["candidates"]}
            stop_id = resolved["stop_id"]

        if not stop_id:
            return {
                "status": "stop_not_found",
                "message": "Provide a stop_id or stop_name to get the schedule.",
            }

        data = _sched.get_schedule_all_routes(text_query, stop_id=stop_id)
        if data.get("error") == "db_unavailable":
            return {"status": "db_unavailable", "message": "Schedule database unavailable. Try the live tracker instead."}
        if data.get("error") == "stop_not_found":
            return {"status": "stop_not_found", "query": stop_id, "message": f"Stop {stop_id} not found in schedule database."}

        rows = data.get("next_by_route") or []
        if not rows:
            return {
                "status": "no_trips",
                "stop": data.get("stop", stop_id),
                "date": _fmt_date(data.get("date", "")),
                "message": "No scheduled departures found at this stop right now.",
            }
        return {
            "status": "ok",
            "stop": data.get("stop"),
            "date": _fmt_date(data.get("date", "")),
            "departures": [
                {"route": rt, "time": format_time_12h(t), "headsign": hs}
                for rt, t, hs in rows
            ],
        }

    # ── Route specified: use route-scoped schedule query ─────────────────────
    data = _sched.get_schedule(
        route_id,
        text_query,
        stop_id=stop_id,
        stop_name=stop_name,
        kind=kind,
    )

    err = data.get("error")
    if err == "db_unavailable":
        return {"status": "db_unavailable", "message": "Schedule database unavailable. Try the live tracker instead."}
    if err == "stop_not_found":
        return {
            "status": "stop_not_found",
            "route": route_id,
            "query": stop_name or stop_id or "",
            "message": f"No stop matching '{stop_name or stop_id}' found on Route {route_id}.",
        }
    if err == "multiple_stops":
        return {"status": "multiple_stops", "route": route_id, "candidates": data.get("candidates", [])}

    if kind == "first":
        first = data.get("first_departure")
        if not first:
            return {
                "status": "no_trips",
                "route": route_id,
                "stop": data.get("stop"),
                "date": _fmt_date(data.get("date", "")),
                "message": f"No service found for Route {route_id} on this date.",
            }
        return {
            "status": "ok_first",
            "route": route_id,
            "stop": data.get("stop"),
            "date": _fmt_date(data.get("date", "")),
            "first_departure": format_time_12h(first),
        }

    if kind == "last":
        last = data.get("last_departure")
        if not last:
            return {
                "status": "no_trips",
                "route": route_id,
                "stop": data.get("stop"),
                "date": _fmt_date(data.get("date", "")),
                "message": f"No service found for Route {route_id} on this date.",
            }
        return {
            "status": "ok_last",
            "route": route_id,
            "stop": data.get("stop"),
            "date": _fmt_date(data.get("date", "")),
            "last_departure": format_time_12h(last),
        }

    # kind == "next"
    rows = data.get("next_by_direction") or []
    if not rows:
        return {
            "status": "no_trips",
            "route": route_id,
            "stop": data.get("stop"),
            "date": _fmt_date(data.get("date", "")),
            "after": format_time_12h(data.get("time", "")),
            "message": f"No scheduled departures found after {format_time_12h(data.get('time', ''))}.",
        }
    return {
        "status": "ok",
        "route": route_id,
        "stop": data.get("stop"),
        "date": _fmt_date(data.get("date", "")),
        "departures": [
            {"time": format_time_12h(t), "headsign": hs}
            for t, hs in rows
        ],
    }


# ── Tool 4: search_routes ────────────────────────────────────────────────────

def _tool_search_routes(destination: str) -> dict:
    """Find which RTS routes serve a destination or area."""
    routes = _sched.routes_serving_area(destination)
    if not routes:
        routes = _sched.routes_serving_destination(destination)
    if not routes:
        return {
            "status": "not_found",
            "destination": destination,
            "message": f"No RTS routes found serving '{destination}'.",
        }
    return {
        "status": "ok",
        "destination": destination,
        "routes": routes,
    }


# ── Tool 5: get_route_overview ───────────────────────────────────────────────

def _tool_get_route_overview(route_id: str, date: str | None = None) -> dict:
    """Get first/last bus + frequency summary for a route on a date."""
    date_str = None
    if date:
        # Reuse parse_date to turn "tomorrow", "monday", etc. into a date object
        parsed = _sched.parse_date(date)
        date_str = parsed.isoformat() if parsed else None

    summary = _sched.get_route_day_summary(route_id, date_str)

    if summary is None:
        conn = _sched.connect_db()
        if not conn:
            return {"status": "db_unavailable", "message": "Schedule database unavailable."}
        conn.close()
        return {
            "status": "route_not_found",
            "route": route_id,
            "message": f"Route {route_id} not found in the schedule database.",
        }

    if not summary.get("runs_today"):
        return {
            "status": "no_service",
            "route": route_id,
            "date": _fmt_date(summary["date_iso"]),
            "day_label": summary["day_label"],
            "message": f"Route {route_id} does not run on {summary['day_label']}.",
        }

    return {
        "status": "ok",
        "route": route_id,
        "route_name": summary["route_long_name"],
        "date": _fmt_date(summary["date_iso"]),
        "day_label": summary["day_label"],
        "directions": summary["directions"],
    }
