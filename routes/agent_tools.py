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
                "Examples: 'Rosa Parks', 'Santa Fe College', 'Reitz Union'. "
                "If the user's route number is already known, pass route_id too "
                "so the result is filtered to stops that route actually serves."
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
                    },
                    "route_id": {
                        "type": "string",
                        "description": (
                            "Optional route number (for example '5' or '43'). "
                            "Include this when the user already specified a route, "
                            "so ambiguous landmarks are restricted to stops served by that route."
                        ),
                    },
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
                "only have a place name. If the user asks for a specific route "
                "at this stop, pass route_id so the live predictions are filtered."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "stop_id": {
                        "type": "string",
                        "description": "4-digit zero-padded GTFS stop ID (e.g. '0001', '0520').",
                    },
                    "route_id": {
                        "type": "string",
                        "description": "Optional route number to filter live predictions (e.g. '1', '43', '75').",
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
                        "enum": ["next", "first", "last", "before"],
                        "description": (
                            "'next' = next departures after a given time (default). "
                            "'first' = first departure of the day. "
                            "'last' = last departure of the day. "
                            "'before' = last departure strictly before a given time (requires time=)."
                        ),
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "Time threshold for 'next' queries. "
                            "ALWAYS pass this when the user mentioned a specific time "
                            "(e.g. 'after 4pm', 'around noon', 'at 3:30'). "
                            "Accepts: '3pm', '4pm', '15:30', 'morning', 'afternoon', 'evening'. "
                            "Only omit if the user did not specify any time at all."
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
            "name": "suggest_destinations",
            "description": (
                "Call this when the user names an ambiguous destination type "
                "(e.g. 'health department', 'library', 'Walmart'). Returns a "
                "short list of known candidate destinations in this agency's "
                "service area so the user can pick one. Do NOT call for a "
                "specific address — call plan_trip directly in that case."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The ambiguous destination type as the user said it, lowercased.",
                    }
                },
                "required": ["query"],
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
    {
        "type": "function",
        "function": {
            "name": "get_route_stops",
            "description": (
                "List all stops on a route in order, optionally filtered by direction. "
                "Use when the user asks 'what stops does route X make?', "
                "'list the stops on route 1 outbound', or 'does route 5 stop at X?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route_id": {
                        "type": "string",
                        "description": "Route number (e.g. '1', '43').",
                    },
                    "direction": {
                        "type": "string",
                        "description": (
                            "Optional direction or headsign keyword to filter results, "
                            "e.g. 'Butler Plaza', 'outbound', 'downtown'. "
                            "Omit to return all directions."
                        ),
                    },
                },
                "required": ["route_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_route_vehicle_count",
            "description": (
                "Get the SCHEDULED number of buses simultaneously active on a route "
                "throughout the day, based on the GTFS timetable (not real-time). "
                "Use ONLY for schedule-based questions about deployment windows: "
                "'when will there be 2 buses on route X', "
                "'how many buses does route X run at peak', "
                "'what's the peak number of buses on route X', or "
                "'how many buses is route X scheduled to run'. "
                "Returns peak count and daily deployment windows. "
                "Do NOT use for live/real-time 'how many are running now' questions — "
                "use get_vehicle_location for that (it returns count AND locations)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route_id": {
                        "type": "string",
                        "description": "Route number (e.g. '15', '37', '75').",
                    },
                    "date": {
                        "type": "string",
                        "description": (
                            "Date to check. "
                            "Accepts: 'today', 'tomorrow', 'monday'–'sunday', 'YYYY-MM-DD'. "
                            "Omit to use today."
                        ),
                    },
                },
                "required": ["route_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicle_location",
            "description": (
                "Get the REAL-TIME location of all active buses on a route, plus the live count. "
                "Use this for any live question about active vehicles: "
                "'where is bus X', 'where is route X right now', "
                "'is the bus near me', 'how far is the bus', "
                "'how many buses are running on route X', 'how many route X are running', "
                "'how many buses are active on route X today', "
                "'how many route X buses are there right now', "
                "'is route X running now', or any count/location question about vehicles active NOW. "
                "Returns vehicle count plus each active vehicle's next stop and minutes until arrival. "
                "Prefer this over get_route_vehicle_count whenever the user is asking about the present moment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route_id": {
                        "type": "string",
                        "description": "Route number (e.g. '8', '75').",
                    }
                },
                "required": ["route_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_vehicles_systemwide",
            "description": (
                "Return the live count of active buses across the ENTIRE system, "
                "broken down per route. "
                "Use this whenever the user asks about all routes / the whole system at once: "
                "'how many buses are running now', 'how many buses are out', "
                "'is the system running today', 'show me all active buses', "
                "'how many routes have buses on them'. "
                "Returns total vehicle count, count of routes with at least one active bus, "
                "and a per-route breakdown. Hits the same aggregator the live map uses, "
                "with a 5-second server-side cache so concurrent calls are cheap. "
                "Use get_vehicle_location instead when the user names a specific route."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_first_last_today",
            "description": (
                "Return the first and last scheduled bus today (or on a given date) "
                "for EVERY route in service. "
                "Use this whenever the user asks about system-wide first/last service "
                "with NO specific route named: "
                "'when is the first bus today', 'when does service start', "
                "'when does the system shut down', 'when is the last bus tonight', "
                "'what time do buses stop running today', 'first bus across all routes'. "
                "Returns earliest first-bus time across the system, latest last-bus time, "
                "and per-route first/last in a sorted list. "
                "Schedule-based (GTFS), not real-time. "
                "Use get_route_overview instead when the user names a specific route."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Optional natural date ('today', 'tomorrow', 'monday', '2026-05-04'). Defaults to today.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_trip",
            "description": (
                "Plan a bus trip from one location to another. "
                "Use this when the user asks 'how do I get from X to Y', "
                "'what bus takes me to Y', 'how can I get to Y from X', "
                "or any trip involving two locations. "
                "Geocodes both addresses and returns up to 3 itineraries with routes, "
                "departure times, walk times, and transfer details. "
                "If the user did not mention an origin, ask for it before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "string",
                        "description": (
                            "Starting location as the user described it. "
                            "Examples: 'UF Reitz Union', '3006 NW 34th St', "
                            "'Hidden Creek Apartments', 'Butler Plaza'. "
                            "Do not guess — ask the user if origin is unknown."
                        ),
                    },
                    "destination": {
                        "type": "string",
                        "description": (
                            "Destination as the user described it. "
                            "Examples: 'Rosa Parks', 'Santa Fe College', 'Shands Hospital'."
                        ),
                    },
                    "depart_at": {
                        "type": "string",
                        "description": (
                            "Optional. The time the user wants to LEAVE. Accept natural forms like "
                            "'2pm', '2:00 PM', '14:00', 'now'. Use this for 'leaving at X', "
                            "'depart at X', 'after X'. Do NOT set this if the user gave an arrival "
                            "deadline — use arrive_by instead."
                        ),
                    },
                    "arrive_by": {
                        "type": "string",
                        "description": (
                            "Optional. The time the user wants to ARRIVE by. Accept natural forms "
                            "like '2pm', '2:00 PM', '14:00'. Use this for 'by X', 'arrive by X', "
                            "'I need to be there by X', 'before X'. The planner will return the "
                            "latest trip that arrives at or before this time."
                        ),
                    },
                },
                "required": ["origin", "destination"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_differences",
            "description": (
                "Compare which routes are suspended or added on a non-weekday service type "
                "versus a regular weekday. Use this when the user asks which buses are "
                "affected, suspended, not running, or changed on Reduced Service, Saturday, "
                "or Sunday. Returns lists of suspended routes (run on weekday but not on "
                "the given service type) and extra routes (run on the given service type "
                "but not on weekdays)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_type": {
                        "type": "string",
                        "description": (
                            "The service type to compare against regular weekday. "
                            "One of: 'Reduced_Service', 'Saturday', 'Sunday'."
                        ),
                    }
                },
                "required": ["service_type"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_alerts",
            "description": (
                "Return active service advisories published by the transit agency — detours, "
                "delays, route suspensions, holiday schedule changes, or any disruption. "
                "Use when the user asks about delays, disruptions, detours, service changes, "
                "holiday schedules, or 'what's happening today / this week'. "
                "Optionally filter to a specific route."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route_id": {
                        "type": "string",
                        "description": (
                            "Optional. Filter advisories to a specific route number "
                            "(e.g. '5', '15'). Omit to get all active advisories."
                        ),
                    }
                },
                "required": [],
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
# search_stops(name: str, route_id?: str) → dict
# ─────────────────────────────
#   Single match:
#     {"status": "found", "stop_id": "0001", "stop_name": "Rosa Parks Downtown Station"}
#   Multiple candidates (show user to pick):
#     {"status": "multiple", "candidates": [{"stop_id": "0520", "stop_name": "Santa Fe College Gainesville Campus"}, ...]}
#   No match:
#     {"status": "not_found", "name": "Walmart on Archer", "message": "No stops found matching that name."}
#
# get_realtime_predictions(stop_id: str, route_id?: str) → dict
# ──────────────────────────────────────────────
#   Predictions available:
#     {"status": "ok", "stop_id": "0001", "stop_name": "Rosa Parks Downtown Station",
#      "predictions": [{"route": "10", "headsign": "To Butler Plaza TS", "minutes": 3, "delayed": false},
#                      {"route": "43", "headsign": "To Santa Fe College", "minutes": 12, "delayed": false}]}
#   Specific route not currently predicted at this stop:
#     {"status": "no_route_prediction", "route": "1", "stop_id": "0001", "stop_name": "...",
#      "available_routes": ["7", "15", "26"], "message": "..."}
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

import json
import logging
import re
import time
from datetime import date as _date

import routes.schedule_service as _sched
from routes.stop_resolver import (
    _gtfs_resolve_stop_name,
    get_predictions_cached,
    resolve_stop_global,
    route_serves_stop,
    suggest_stops_by_route,
)
from routes.parsing_helpers import format_time_12h
from utils.agency_config import get_common_destinations

logger = logging.getLogger(__name__)


# Generic terms that always map to the main downtown hub (Rosa Parks, stop 0001).
# Used in search_stops to avoid ambiguous LIKE matches on "downtown".
_HUB_STOP_ALIASES: dict[str, tuple[str, str]] = {
    "downtown": ("0001", "Rosa Parks RTS Downtown Station"),
    "downtown gainesville": ("0001", "Rosa Parks RTS Downtown Station"),
    "rosa parks": ("0001", "Rosa Parks RTS Downtown Station"),
    "transit hub": ("0001", "Rosa Parks RTS Downtown Station"),
    "rts transfer center": ("0001", "Rosa Parks RTS Downtown Station"),
    "downtown station": ("0001", "Rosa Parks RTS Downtown Station"),
}

# Keywords that identify the Gainesville downtown transit hub.
# Headsigns containing any of these are "inbound" when departing from the hub.
_DOWNTOWN_HUB_KEYWORDS = frozenset([
    "rosa parks", "downtown station", "rts transfer",
    "transfer center", "downtown transfer", "rts downtown",
    "transit center",
])


def _filter_inbound_departures(departures: list, stop_name: str) -> list:
    """
    When a user is departing FROM a stop, remove headsigns that head back
    toward that same stop or its hub aliases.

    GTFS stop names are typically verbose ("Oaks Mall SW 62nd Blvd") while
    headsigns are short ("To Oaks Mall"). We have to check overlap in BOTH
    directions — a single-direction substring check misses the common case.
    """
    import re

    stop_lower = (stop_name or "").lower()
    is_hub = any(kw in stop_lower for kw in _DOWNTOWN_HUB_KEYWORDS)

    def _headsign_points_to_stop(headsign_lower: str) -> bool:
        # Cheap direct check: stop name appears inside headsign.
        if stop_lower and stop_lower in headsign_lower:
            return True

        # Reverse check: strip a leading "To "/"to"/"hacia" off the headsign and
        # see if the destination keyword appears in the stop name.
        m = re.match(r"^\s*(?:to|hacia)\s+(.+)$", headsign_lower)
        if not m:
            return False
        dest = m.group(1).strip()
        if not dest:
            return False
        if dest in stop_lower:
            return True

        # Fallback: take the first 2 significant words of the destination
        # (handles "Butler Plaza TS" vs stop name "Butler Plaza Transfer Station").
        dest_words = [w for w in dest.split() if len(w) >= 3]
        if len(dest_words) >= 2 and f"{dest_words[0]} {dest_words[1]}" in stop_lower:
            return True
        return False

    filtered = []
    for dep in departures:
        headsign_lower = (dep.get("headsign") or "").lower()
        if _headsign_points_to_stop(headsign_lower):
            continue
        # For downtown hub departures, also exclude other hub-pointing headsigns.
        if is_hub and any(kw in headsign_lower for kw in _DOWNTOWN_HUB_KEYWORDS):
            continue
        filtered.append(dep)
    return filtered or departures  # fallback: never return empty


def _normalize_route_id(route_id: str | None) -> str | None:
    """
    Strip natural-language prefixes the LLM may add.
    'Route 1' → '1', 'route 43' → '43', '  75  ' → '75'.
    GTFS route_short_name values are bare numbers (e.g. '1', '43', '75').
    """
    if not route_id:
        return route_id
    import re
    rid = re.sub(r"(?i)^\s*route\s*", "", route_id).strip()
    return rid or route_id.strip()


def _fmt_date(iso: str) -> str:
    """'2026-02-27' → 'Feb 27'"""
    try:
        return _date.fromisoformat(iso).strftime("%b %d").lstrip("0")
    except Exception:
        return iso


def _to_hhmm(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.lower() == "now":
        return None

    m_24 = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m_24:
        hour = int(m_24.group(1))
        minute = int(m_24.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return None

    m_ampm = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?\s*([AaPp][Mm])", text)
    if not m_ampm:
        return None

    hour = int(m_ampm.group(1))
    minute = int(m_ampm.group(2) or "0")
    suffix = m_ampm.group(3).lower()
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        return None
    if suffix == "pm" and hour != 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


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


def _tool_get_route_stops(route_id: str, direction: str | None = None) -> dict:
    route_id = _normalize_route_id(route_id) or route_id
    result = _sched.get_route_stops(route_id, direction_hint=direction)
    if result.get("status") != "ok":
        return result
    # Limit each direction to 50 stops max to keep response size reasonable
    for d in result.get("directions", []):
        d["stops"] = d["stops"][:50]
    return result


def dispatch_tool(name: str, arguments: dict, session_id: str | None = None) -> dict:
    """
    Called by the agent loop for each tool_call the LLM requests.
    Routes to the correct wrapper function by name.
    Returns a dict matching the schema in PART B above.
    """
    logger.info(
        "tool_call session=%s tool=%s input=%s",
        session_id or "-",
        name,
        json.dumps(arguments, default=str)[:500],
    )
    handlers = {
        "search_stops": _tool_search_stops,
        "get_realtime_predictions": _tool_get_realtime_predictions,
        "get_schedule": _tool_get_schedule,
        "search_routes": _tool_search_routes,
        "suggest_destinations": _tool_suggest_destinations,
        "get_route_overview": _tool_get_route_overview,
        "get_route_stops": _tool_get_route_stops,
        "get_service_differences": _tool_get_service_differences,
        "get_service_alerts":      _tool_get_service_alerts,
        "get_route_vehicle_count": _tool_get_route_vehicle_count,
        "get_vehicle_location": _tool_get_vehicle_location,
        "get_active_vehicles_systemwide": _tool_get_active_vehicles_systemwide,
        "get_system_first_last_today":    _tool_get_system_first_last_today,
        "plan_trip": _tool_plan_trip,
    }
    handler = handlers.get(name)
    if not handler:
        logger.error("Unknown tool called: %s", name)
        return {"status": "error", "message": f"Unknown tool: {name}"}
    started = time.perf_counter()
    try:
        result = handler(**arguments)
    except Exception as exc:
        logger.exception("Tool %s failed: %s", name, exc)
        result = {"status": "error", "message": f"Tool {name} encountered an error."}
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "tool_result session=%s tool=%s status=%s duration_ms=%d",
        session_id or "-",
        name,
        (result or {}).get("status"),
        duration_ms,
    )
    return result


# ── Tool 1: search_stops ─────────────────────────────────────────────────────

def _tool_search_stops(name: str, route_id: str | None = None) -> dict:
    """Resolve a stop name or landmark to a GTFS stop ID."""
    alias = _HUB_STOP_ALIASES.get(name.lower().strip())
    route_id = _normalize_route_id(route_id)

    if route_id:
        # Route-aware matching prevents generic landmarks like "Oaks Mall"
        # from surfacing stops that the requested route never serves.
        if alias and route_serves_stop(route_id, alias[0]):
            return {"status": "found", "stop_id": alias[0], "stop_name": alias[1], "route": route_id}

        scoped = _gtfs_resolve_stop_name(route_id, name)
        if scoped:
            if "stop_id" in scoped:
                return {
                    "status": "found",
                    "stop_id": scoped["stop_id"],
                    "stop_name": scoped["stop_name"],
                    "route": route_id,
                }
            return {
                "status": "multiple",
                "name": name,
                "route": route_id,
                "candidates": scoped.get("candidates", []),
            }

        bustime_candidates = suggest_stops_by_route(route_id, name, limit=5)
        if len(bustime_candidates) == 1:
            candidate = bustime_candidates[0]
            return {
                "status": "found",
                "stop_id": candidate["id"],
                "stop_name": candidate["name"],
                "route": route_id,
            }
        if len(bustime_candidates) > 1:
            return {
                "status": "multiple",
                "name": name,
                "route": route_id,
                "candidates": [
                    {"stop_id": candidate["id"], "stop_name": candidate["name"]}
                    for candidate in bustime_candidates
                ],
            }

        return {
            "status": "not_found",
            "name": name,
            "route": route_id,
            "message": (
                f"No stops found matching '{name}' on Route {route_id}. "
                "Try another landmark or the 4-digit Stop ID from the sign."
            ),
        }

    # Check hub alias first for global searches — avoids ambiguous LIKE matches
    # on place names like "downtown".
    if alias:
        return {"status": "found", "stop_id": alias[0], "stop_name": alias[1]}
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

def _gap_fill_with_schedule(live_predictions: list, stop_id: str) -> list:
    """Append next scheduled departure for routes that serve this stop but have no live prediction.

    BusTime only returns predictions within ~45 min. This fills the gap so riders see
    all routes that serve the stop, not just the ones with an imminent bus.
    Looks up to 14 days forward so routes with no service today (e.g. weekend-only)
    still surface with their next scheduled departure and a day label.
    """
    from datetime import datetime, timedelta
    try:
        today = datetime.now(_sched.TZ).date()
        covered = {p["route"] for p in live_predictions}
        result = list(live_predictions)

        prev_count = len(covered)
        for offset in range(15):
            target = today + timedelta(days=offset)
            text = "now" if offset == 0 else f"{target.isoformat()} midnight"
            data = _sched.get_schedule_all_routes(text, stop_id=stop_id)
            if data.get("error"):
                break
            rows = data.get("next_by_route") or []
            if not rows:
                continue

            if target == today:
                day_label = None
            elif target == today + timedelta(days=1):
                day_label = "Tomorrow"
            else:
                day_label = target.strftime("%a %b %-d")

            for rt, t, hs in rows:
                route = str(rt)
                if route not in covered:
                    entry = {
                        "route": route,
                        "headsign": hs or "",
                        "minutes": None,
                        "scheduled_time": format_time_12h(t),
                        "source": "scheduled",
                    }
                    if day_label:
                        entry["scheduled_day"] = day_label
                    result.append(entry)
                    covered.add(route)

            # Stop once a full day passes with no new routes found
            if offset > 0 and len(covered) == prev_count and covered:
                break
            prev_count = len(covered)

        return result
    except Exception as exc:
        logger.debug("Gap-fill schedule lookup failed for stop %s: %s", stop_id, exc)
        return live_predictions


def _tool_get_realtime_predictions(stop_id: str, route_id: str | None = None) -> dict:
    """Get live Bustime predictions at a stop."""
    route_id = _normalize_route_id(route_id)
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
            "source": "live",
        })

    if route_id:
        route_predictions = [p for p in formatted if p.get("route") == route_id]
        if not route_predictions:
            return {
                "status": "no_route_prediction",
                "route": route_id,
                "stop_id": stop_id,
                "stop_name": stop_name,
                "available_routes": sorted({p.get("route") for p in formatted if p.get("route")}),
                "message": (
                    f"No live Route {route_id} prediction at {stop_name} right now. "
                    "Try the schedule as a fallback."
                ),
            }
        formatted = route_predictions
    else:
        formatted = _gap_fill_with_schedule(formatted, stop_id)

    return {
        "status": "ok",
        "stop_id": stop_id,
        "stop_name": stop_name,
        "route_filter": route_id,
        "predictions": formatted,
    }


# ── Tool 3: get_schedule ─────────────────────────────────────────────────────

def _route_never_serves_stop(route_id: str, stop_id: str) -> bool:
    """Return True if the route has zero stop_times entries at the given stop."""
    try:
        conn = _sched.connect_db()
        try:
            # stop_times uses unpadded stop_id; strip leading zeros for the query
            unpadded = stop_id.lstrip("0") or "0"
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM trips t
                JOIN routes r ON r.route_id = t.route_id
                JOIN stop_times st ON st.trip_id = t.trip_id
                WHERE r.route_short_name = ? AND (st.stop_id = ? OR st.stop_id = ?)
                """,
                (route_id, stop_id, unpadded),
            ).fetchone()
            return (row["cnt"] == 0) if row else True
        finally:
            conn.close()
    except Exception:
        return False


def _tool_get_schedule(
    route_id: str | None = None,
    stop_id: str | None = None,
    stop_name: str | None = None,
    kind: str = "next",
    time: str | None = None,
    date: str | None = None,
) -> dict:
    """Get scheduled departures from GTFS. Works with or without route_id."""
    # Guardrail: kind="last" with a time param means the caller wanted "before X"
    # (kind="last" ignores time entirely). Redirect automatically.
    if kind == "last" and time:
        kind = "before"
    route_id = _normalize_route_id(route_id)
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
            "stop_id": data.get("stop_id") or stop_id,
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
            "stop_id": data.get("stop_id") or stop_id,
            "date": _fmt_date(data.get("date", "")),
            "last_departure": format_time_12h(last),
        }

    # kind == "before"
    before_rows = data.get("before_by_direction")
    if before_rows is not None:
        stop_name = data.get("stop", "")
        if not before_rows:
            return {
                "status": "no_trips",
                "route": route_id,
                "stop": stop_name,
                "date": _fmt_date(data.get("date", "")),
                "before": format_time_12h(data.get("time", "")),
                "message": f"No scheduled departures found before {format_time_12h(data.get('time', ''))}.",
            }
        departures = [{"time": format_time_12h(t), "headsign": hs} for t, hs in before_rows]
        return {
            "status": "ok_before",
            "route": route_id,
            "stop": stop_name,
            "stop_id": data.get("stop_id") or stop_id,
            "date": _fmt_date(data.get("date", "")),
            "before": format_time_12h(data.get("time", "")),
            "departures": departures,
        }

    # kind == "next"
    rows = data.get("next_by_direction") or []
    if not rows:
        # Distinguish "no more trips today" from "route never serves this stop"
        if stop_id:
            never_serves = _route_never_serves_stop(route_id, stop_id)
            if never_serves:
                return {
                    "status": "route_not_at_stop",
                    "route": route_id,
                    "stop": data.get("stop") or stop_id,
                    "stop_id": data.get("stop_id") or stop_id,
                    "message": (
                        f"Route {route_id} does not serve stop {stop_id} "
                        f"({data.get('stop') or stop_id}). "
                        "Check the stop ID or use get_route_stops to see which stops it serves."
                    ),
                }
        return {
            "status": "no_trips",
            "route": route_id,
            "stop": data.get("stop"),
            "stop_id": data.get("stop_id") or stop_id,
            "date": _fmt_date(data.get("date", "")),
            "after": format_time_12h(data.get("time", "")),
            "message": f"No scheduled departures found after {format_time_12h(data.get('time', ''))}.",
        }
    stop_name = data.get("stop", "")
    departures = [{"time": format_time_12h(t), "headsign": hs} for t, hs in rows]
    departures = _filter_inbound_departures(departures, stop_name)
    return {
        "status": "ok",
        "route": route_id,
        "stop": stop_name,
        "stop_id": data.get("stop_id") or stop_id,
        "date": _fmt_date(data.get("date", "")),
        "departures": departures,
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


def _destination_matches(query: str, candidate: str) -> bool:
    query_norm = (query or "").strip().lower()
    candidate_norm = (candidate or "").strip().lower()
    if not query_norm or not candidate_norm:
        return False
    return (
        query_norm == candidate_norm
        or query_norm in candidate_norm
        or candidate_norm in query_norm
    )


def _tool_suggest_destinations(query: str) -> dict:
    """Return configured destination candidates for ambiguous place types."""
    query_norm = (query or "").strip().lower()
    cfg = get_common_destinations()
    pois = cfg.get("pois") or {}
    landmarks = cfg.get("landmarks") or {}

    for key, entries in pois.items():
        if _destination_matches(query_norm, key):
            return {
                "status": "ok",
                "query": query_norm,
                "candidates": [
                    {"name": entry.get("name", ""), "address": entry.get("address", "")}
                    for entry in entries
                    if entry.get("name") and entry.get("address")
                ][:5],
            }

    for canonical_name, entry in landmarks.items():
        aliases = entry.get("aliases") or []
        if _destination_matches(query_norm, canonical_name) or any(
            _destination_matches(query_norm, alias) for alias in aliases
        ):
            return {
                "status": "ok_landmark",
                "query": query_norm,
                "candidates": [{
                    "name": canonical_name,
                    "stop_ids": [str(stop_id) for stop_id in (entry.get("stops") or [])],
                }],
            }

    return {"status": "not_found", "query": query_norm}


# ── Tool 5: get_route_overview ───────────────────────────────────────────────

def _tool_get_route_overview(route_id: str, date: str | None = None) -> dict:
    """Get first/last bus + frequency summary for a route on a date."""
    route_id = _normalize_route_id(route_id) or route_id
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

    # Add first/last by service type so agent can answer
    # "first bus on weekdays vs Saturday vs Sunday" without multiple calls
    service_schedule = _sched.get_route_first_last_by_service_type(route_id)

    return {
        "status": "ok",
        "route": route_id,
        "route_name": summary["route_long_name"],
        "date": _fmt_date(summary["date_iso"]),
        "day_label": summary["day_label"],
        "directions": summary["directions"],
        "schedule_by_service_type": service_schedule,
    }


# ── Tool 7: get_service_differences ──────────────────────────────────────────

def _tool_get_service_differences(service_type: str) -> dict:
    """Compare routes on a given service type vs regular weekday schedule."""
    conn = _sched.connect_db()
    if not conn:
        return {"status": "db_unavailable", "message": "Schedule database unavailable."}

    # Normalize common variations
    _alias = {
        "reduced": "Reduced_Service",
        "reduced service": "Reduced_Service",
        "reducedservice": "Reduced_Service",
        "saturday": "Saturday",
        "sunday": "Sunday",
    }
    svc = _alias.get(service_type.lower().strip(), service_type)

    try:
        def routes_for_service(service_id: str) -> set:
            rows = conn.execute(
                """
                SELECT DISTINCT r.route_short_name
                FROM trips t
                JOIN routes r ON r.route_id = t.route_id
                WHERE t.service_id = ?
                """,
                (service_id,),
            ).fetchall()
            return {row[0] for row in rows if row[0]}

        weekday_routes = routes_for_service("Weekday")
        target_routes = routes_for_service(svc)

        if not target_routes and not weekday_routes:
            return {
                "status": "not_found",
                "service_type": svc,
                "message": f"No GTFS data found for service type '{svc}'. "
                           "Valid options: Reduced_Service, Saturday, Sunday.",
            }

        suspended = sorted(weekday_routes - target_routes, key=lambda x: (len(x), x))
        extra = sorted(target_routes - weekday_routes, key=lambda x: (len(x), x))
        running = sorted(weekday_routes & target_routes, key=lambda x: (len(x), x))

        # Human-readable label
        _label = {
            "Reduced_Service": "Reduced Service",
            "Saturday": "Saturday",
            "Sunday": "Sunday",
        }
        label = _label.get(svc, svc)

        return {
            "status": "ok",
            "service_type": label,
            "suspended_routes": suspended,
            "extra_routes": extra,
            "running_routes": running,
            "summary": (
                f"On {label}, {len(suspended)} route(s) are suspended compared to a regular weekday"
                + (f": {', '.join(suspended)}" if suspended else "")
                + (f". {len(extra)} route(s) run only on {label}: {', '.join(extra)}" if extra else "")
                + f". {len(running)} route(s) run as normal."
            ),
        }
    finally:
        conn.close()


# ── Tool: get_service_alerts ─────────────────────────────────────────────────

def _tool_get_service_alerts(route_id: str | None = None) -> dict:
    """Return active service advisories from the BusTime API."""
    import rts_api
    try:
        data = rts_api.get_service_advisories(route_id or None)
    except Exception as exc:
        return {"status": "api_unavailable", "message": str(exc)}

    advisories_raw = data.get("sb", []) or []
    if not advisories_raw:
        return {"status": "no_alerts", "message": "No active service advisories at this time."}

    alerts = []
    for a in advisories_raw:
        routes = [s.get("rt") for s in (a.get("srvc") or []) if s.get("rt")]
        alerts.append({
            "subject":  a.get("sbj") or a.get("nm") or "Service Advisory",
            "detail":   a.get("dtl") or a.get("brf") or "",
            "priority": a.get("prty") or "",
            "routes":   routes,
            "starts":   a.get("beg") or "",
            "ends":     a.get("end") or "",
        })

    return {"status": "ok", "count": len(alerts), "alerts": alerts}


# ── Tool 8: get_route_vehicle_count ──────────────────────────────────────────

def _tool_get_route_vehicle_count(route_id: str, date: str | None = None) -> dict:
    """Return scheduled concurrent bus count windows for a route on a given date."""
    route_id = _normalize_route_id(route_id) or route_id

    # Resolve date → service_id
    date_str = None
    if date:
        parsed = _sched.parse_date(date)
        date_str = parsed.isoformat() if parsed else None

    from datetime import date as _dateobj, datetime
    from zoneinfo import ZoneInfo
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "utils"))
    from agency_config import get_timezone as _get_tz
    _TZ = ZoneInfo(_get_tz())
    target_date = _dateobj.fromisoformat(date_str) if date_str else datetime.now(_TZ).date()
    day_name = target_date.strftime("%A")

    # Determine service_id from active label
    label = _sched.get_active_service_label(target_date)
    service_id = label.replace(" ", "_") if label else "Weekday"

    conn = _sched.connect_db()
    if not conn:
        return {"status": "db_unavailable", "message": "Schedule database unavailable."}

    try:
        rows = conn.execute("""
            SELECT MIN(st.departure_time) AS first_dep,
                   MAX(st.arrival_time)   AS last_arr
            FROM trips t
            JOIN routes r ON r.route_id = t.route_id
            JOIN stop_times st ON st.trip_id = t.trip_id
            WHERE r.route_short_name = ? AND t.service_id = ?
            GROUP BY t.trip_id
            ORDER BY first_dep
        """, (route_id, service_id)).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "status": "no_service",
            "route": route_id,
            "date": target_date.strftime("%b %d"),
            "day": day_name,
            "message": f"Route {route_id} has no scheduled trips on {day_name}.",
        }

    def to_min(t):
        h, m, s = (int(x) for x in t.split(":"))
        return h * 60 + m

    def fmt_min(m):
        h, mn = divmod(m, 60)
        sx = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return "%d:%02d %s" % (h12, mn, sx)

    # Build +1/-1 events
    events = []
    for r in rows:
        events.append((to_min(r[0]), +1))
        events.append((to_min(r[1]), -1))
    events.sort(key=lambda x: (x[0], x[1]))

    # Walk events → build windows of stable count
    active = 0
    windows = []
    window_start = None
    for minute, delta in events:
        if active > 0 and window_start is not None:
            windows.append({"from": fmt_min(window_start), "to": fmt_min(minute), "buses": active})
        active += delta
        if active > 0:
            window_start = minute

    # Merge consecutive windows with same bus count and gap ≤ 5 min (turnaround)
    merged = []
    for w in windows:
        if (merged and merged[-1]["buses"] == w["buses"]
                and to_min(w["from"]) - to_min(merged[-1]["to"]) <= 5):
            merged[-1]["to"] = w["to"]
        else:
            merged.append(dict(w))

    peak = max(w["buses"] for w in merged) if merged else 0

    # Current count based on now (Eastern time)
    now_et = datetime.now(_TZ)
    now_min = now_et.hour * 60 + now_et.minute
    current = 0
    if not date_str:  # only meaningful for today
        for w in merged:
            if to_min(w["from"]) <= now_min < to_min(w["to"]):
                current = w["buses"]
                break

    return {
        "status": "ok",
        "route": route_id,
        "date": target_date.strftime("%b %d"),
        "day": day_name,
        "service_type": service_id.replace("_", " "),
        "current_count": current,
        "peak_count": peak,
        "total_trips": len(rows),
        "windows": merged,
    }


# ── Tool 9: get_vehicle_location ──────────────────────────────────────────────

def _tool_get_vehicle_location(route_id: str) -> dict:
    """Return all active buses on a route with next-stop name and minutes away."""
    route_id = _normalize_route_id(route_id) or route_id
    try:
        import rts_api
        data = rts_api.get_vehicles(route_id)
    except Exception as exc:
        logger.warning("get_vehicles failed for route %s: %s", route_id, exc)
        return {"status": "api_unavailable", "message": "Unable to reach real-time vehicle data."}

    vehicles_raw = data.get("vehicle", []) or data.get("vehicles", []) or []
    if not vehicles_raw:
        return {
            "status": "no_vehicles",
            "route": route_id,
            "message": f"No active buses found on Route {route_id} right now.",
        }

    results = []
    for v in vehicles_raw:
        vid = str(v.get("vid", "")).strip()
        destination = str(v.get("des", "")).strip()
        delayed = bool(v.get("dly", False))

        # Get next-stop prediction for this vehicle using vid= param
        next_stop_name = None
        next_stop_id = None
        minutes = None
        try:
            import rts_api as _rts
            pred_data = _rts.call_bustime("getpredictions", {"vid": vid, "top": 1})
            preds = pred_data.get("prd", []) or []
            if preds:
                p = preds[0]
                next_stop_name = str(p.get("stpnm", "")).strip() or None
                next_stop_id = str(p.get("stpid", "")).strip() or None
                ctdn = str(p.get("prdctdn", "")).strip()
                try:
                    minutes = int(ctdn)
                except ValueError:
                    minutes = ctdn  # e.g. "DUE"
        except Exception:
            pass

        results.append({
            "vehicle_id": vid,
            "destination": destination,
            "next_stop_id": next_stop_id,
            "next_stop_name": next_stop_name,
            "minutes_to_next_stop": minutes,
            "delayed": delayed,
        })

    # Sort: numeric minutes first (ascending), then non-numeric, then unknowns
    def _sort_key(v):
        m = v["minutes_to_next_stop"]
        if isinstance(m, int):
            return (0, m)
        if m is not None:
            return (1, 0)
        return (2, 0)

    results.sort(key=_sort_key)
    return {
        "status": "ok",
        "route": route_id,
        "vehicle_count": len(results),
        "vehicles": results[:10],
    }


# ── Tool 10: get_active_vehicles_systemwide ──────────────────────────────────

def _tool_get_active_vehicles_systemwide() -> dict:
    """
    Return a system-wide summary of active buses across every route.
    Reuses the same aggregator that powers the live map (`/api/map/vehicles`)
    so the chat agent and the map are guaranteed to agree on counts.
    """
    try:
        from routes.map_api import _fetch_all_vehicles
        vehicles = _fetch_all_vehicles()
    except Exception as exc:
        logger.warning("get_active_vehicles_systemwide failed: %s", exc)
        return {
            "status":  "api_unavailable",
            "message": "Unable to reach real-time vehicle data right now.",
        }

    if not vehicles:
        return {
            "status":  "no_vehicles",
            "message": "No active buses across the system right now.",
        }

    by_route: dict[str, int] = {}
    for v in vehicles:
        rt = str(v.get("route") or "?")
        by_route[rt] = by_route.get(rt, 0) + 1

    def _route_sort_key(rt: str):
        return (0, int(rt)) if rt.isdigit() else (1, rt)

    return {
        "status":             "ok",
        "total_vehicles":     len(vehicles),
        "active_route_count": len(by_route),
        "by_route": [
            {"route": rt, "count": by_route[rt]}
            for rt in sorted(by_route.keys(), key=_route_sort_key)
        ],
    }


# ── Tool 11: get_system_first_last_today ─────────────────────────────────────

def _tool_get_system_first_last_today(date: str | None = None) -> dict:
    """
    Return first/last scheduled bus across every route in service today.

    Closes the gap that surfaced 2026-05-03: agent had get_route_overview for
    a single route but no system-wide aggregator. User asked "when is the
    first bus today" without a route, agent admitted "I don't have a tool"
    and fell back to suggesting an external URL.

    Pure GTFS — no BusTime calls. Iterates the routes list (cached from the
    routes table) and uses the existing get_route_day_summary helper.
    """
    target_date = None
    if date:
        try:
            parsed = _sched.parse_date(date)
            target_date = parsed.isoformat() if parsed else None
        except Exception:
            target_date = None

    # Pull the route list once. Reusing map_api's cache keeps both surfaces
    # consistent on the route inventory.
    try:
        from routes.map_api import _load_routes
        routes_list = _load_routes()
    except Exception as exc:
        logger.warning("get_system_first_last_today: routes lookup failed: %s", exc)
        return {
            "status":  "db_unavailable",
            "message": "Schedule data is unavailable right now.",
        }

    per_route: list[dict] = []
    earliest_first = None
    latest_last = None

    for r in routes_list:
        rid = r["route_id"]
        try:
            summary = _sched.get_route_day_summary(rid, target_date)
        except Exception as exc:
            logger.debug("get_system_first_last_today: %s summary failed: %s", rid, exc)
            continue
        if not summary or not summary.get("runs_today"):
            continue

        # Each direction has its own first/last; collapse to the route's
        # overall first (earliest across directions) and last (latest).
        firsts = [d.get("first") for d in summary.get("directions", []) if d.get("first")]
        lasts  = [d.get("last")  for d in summary.get("directions", []) if d.get("last")]
        if not firsts or not lasts:
            continue

        # Times are 12-hour strings ('5:30 AM', '11:42 PM'); sort by parsed minute.
        def _to_min(s: str) -> int:
            try:
                from datetime import datetime as _dt
                t = _dt.strptime(s.strip(), "%I:%M %p")
                return t.hour * 60 + t.minute
            except Exception:
                return 24 * 60  # push unparseable to the end

        first_str = min(firsts, key=_to_min)
        last_str  = max(lasts,  key=_to_min)

        per_route.append({
            "route_id":  rid,
            "long_name": summary.get("route_long_name", ""),
            "first":     first_str,
            "last":      last_str,
        })

        f_min = _to_min(first_str)
        l_min = _to_min(last_str)
        if earliest_first is None or f_min < earliest_first[1]:
            earliest_first = (rid, f_min, first_str)
        if latest_last is None or l_min > latest_last[1]:
            latest_last = (rid, l_min, last_str)

    if not per_route:
        return {
            "status":      "no_service",
            "date":        target_date or "today",
            "day_label":   _sched.get_active_service_label(),
            "message":     "No routes are running on the selected date.",
            "by_route":    [],
        }

    per_route.sort(key=lambda r: int(r["route_id"]) if r["route_id"].isdigit() else 9999)

    return {
        "status":            "ok",
        "date":              target_date or "today",
        "day_label":         _sched.get_active_service_label(),
        "active_route_count": len(per_route),
        "earliest_first": {
            "route":    earliest_first[0],
            "time":     earliest_first[2],
        },
        "latest_last": {
            "route":    latest_last[0],
            "time":     latest_last[2],
        },
        "by_route": per_route,
    }


# ── Tool 12: plan_trip ────────────────────────────────────────────────────────

def _tool_plan_trip(
    origin: str,
    destination: str,
    depart_at: str | None = None,
    arrive_by: str | None = None,
) -> dict:
    """Geocode origin + destination and return up to 3 transit itineraries."""
    try:
        from utils.geocoding import geocode
        from utils.trip_planner import find_trips
    except Exception as exc:
        logger.error("plan_trip import error: %s", exc)
        return {"status": "error", "message": "Trip planner is temporarily unavailable."}

    origin_geo = geocode(origin)
    if not origin_geo:
        return {
            "status": "geocode_failed",
            "message": f"Could not find '{origin}'. Try a more specific address or intersection.",
        }

    dest_geo = geocode(destination)
    if not dest_geo:
        return {
            "status": "geocode_failed",
            "message": f"Could not find '{destination}'. Try a more specific address or intersection.",
        }

    try:
        depart_after_hhmm = _to_hhmm(depart_at) if depart_at else None
        arrive_by_hhmm = _to_hhmm(arrive_by) if arrive_by else None
        if depart_after_hhmm and arrive_by_hhmm:
            logger.debug(
                "plan_trip received both depart_at=%s and arrive_by=%s; ignoring depart_at",
                depart_at,
                arrive_by,
            )
            depart_after_hhmm = None
        result = find_trips(
            origin_lat=origin_geo["lat"],
            origin_lon=origin_geo["lon"],
            dest_lat=dest_geo["lat"],
            dest_lon=dest_geo["lon"],
            depart_after=depart_after_hhmm,
            arrive_by=arrive_by_hhmm,
            origin_stop_id=origin_geo.get("stop_id"),
            dest_stop_id=dest_geo.get("stop_id"),
        )
    except Exception as exc:
        logger.error("find_trips error: %s", exc)
        return {"status": "error", "message": "Trip planning encountered an error."}

    if result.get("error"):
        return {
            "status": "no_routes",
            "origin": origin_geo["formatted_address"],
            "destination": dest_geo["formatted_address"],
            "message": result["error"],
        }

    itins = result.get("itineraries", [])
    if not itins:
        return {
            "status": "no_routes",
            "origin": origin_geo["formatted_address"],
            "destination": dest_geo["formatted_address"],
            "message": "No routes found between these locations right now.",
        }

    formatted = []
    for itin in itins[:3]:
        legs_summary = []

        walk_to = itin.get("walk_to_stop")
        if walk_to and walk_to.get("walk_min", 0) > 0:
            legs_summary.append(
                f"Walk {walk_to['walk_min']} min to {walk_to['stop_name']}"
            )

        for leg in itin.get("legs", []):
            if leg["type"] == "bus":
                legs_summary.append(
                    f"Route {leg['route']} to {leg.get('headsign', '')} "
                    f"— dep {leg['depart']}, arr {leg['arrive']} ({leg['ride_min']} min ride)"
                )
            elif leg["type"] == "transfer":
                side = "same side" if leg.get("same_side") else "cross street"
                legs_summary.append(
                    f"Transfer at {leg['at_stop_name']} — {leg['wait_min']} min wait ({side})"
                )

        walk_from = itin.get("walk_from_stop")
        if walk_from and walk_from.get("walk_min", 0) > 0:
            legs_summary.append(
                f"Walk {walk_from['walk_min']} min from {walk_from['stop_name']}"
            )

        buses = [l for l in itin.get("legs", []) if l["type"] == "bus"]
        formatted.append({
            "total_min": round(itin["total_min"]),
            "realtime": itin.get("realtime", False),
            "transfers": len([l for l in itin.get("legs", []) if l["type"] == "transfer"]),
            "departs": buses[0]["depart"] if buses else None,
            "arrives": buses[-1]["arrive"] if buses else None,
            "legs": legs_summary,
        })

    return {
        "status": "ok",
        "origin": origin_geo["formatted_address"],
        "destination": dest_geo["formatted_address"],
        "service_label": result.get("service_label", ""),
        "itineraries": formatted,
    }
