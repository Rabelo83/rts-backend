"""
utils/trip_planner.py
Transit routing — thin wrapper over GTFSEngine.

All GTFS graph traversal is done in-memory by GTFSEngine (zero SQL during routing).
This module handles:
  - Service ID resolution
  - Stop lookup (delegated to engine)
  - Hub-relay fallback for 2-transfer gaps
  - Real-time enrichment (BusTime predictions on first leg)
  - Walk legs (origin → first stop, last stop → destination)
  - Scoring, deduplication, ranking
"""
import math
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from agency_config import get_timezone, get_transfer_hubs

_TZ = ZoneInfo(get_timezone())

from routes.schedule_service import get_active_service_label
from utils.gtfs_engine import (
    get_engine, haversine_m, walk_min as _walk_min,
    _min_to_hhmm, _gtfs_to_min,
)
from utils.stop_finder import (
    find_nearest_stops, get_stop_by_id,
    same_side_penalty_sec,
)

_MAX_WALK_M           = 1000   # max walk to/from a stop (~0.6 mi)
_MAX_FINAL_WALK_MIN   = 12     # max walk on the final (destination) leg — no one
                               # wants a surprise 17-minute walk after the bus ride.
                               # The stop-finder fallback can expand to 5 km when
                               # nothing is close; this cap drops those results.
_MAX_WAIT_MIN         = 90     # max wait — covers 80-min headways
_MAX_RESULTS          = 5
_SEARCH_WIN_MIN       = 120    # departure search window
_HUB_CONNECT_MIN      = 3      # min buffer after arriving at a hub

# Major transfer hubs used as fallback relay points — read from agency_config.yaml
TRANSFER_HUBS = [
    {"stop_id": h["stop_id"], "name": h["display"]}
    for h in get_transfer_hubs()
]


# ── Time helpers ───────────────────────────────────────────────────────────────

def _now_min() -> int:
    now = datetime.now(_TZ)
    return now.hour * 60 + now.minute


# ── Real-time enrichment ───────────────────────────────────────────────────────

def _enrich_realtime(itineraries: list[dict],
                     origin_stop_ids: list[int]) -> list[dict]:
    """Replace first-leg departure with BusTime real-time prediction if available."""
    try:
        import rts_api
        data  = rts_api.get_predictions(",".join(str(s) for s in origin_stop_ids))
        preds = data.get("prd") or []
        if not preds:
            return itineraries

        rt_map: dict[str, int] = {}
        now = _now_min()
        for p in preds:
            route    = str(p.get("rt", ""))
            prd_time = p.get("prdctdn", "")
            if prd_time == "DUE":
                eta = 0
            else:
                try:
                    eta = int(prd_time)
                except Exception:
                    continue
            actual = now + eta
            if route not in rt_map or actual < rt_map[route]:
                rt_map[route] = actual

        for itin in itineraries:
            first_bus = next((l for l in itin["legs"] if l["type"] == "bus"), None)
            if not first_bus or first_bus["route"] not in rt_map:
                continue
            rt_dep = rt_map[first_bus["route"]]
            diff   = rt_dep - first_bus["depart_min"]
            first_bus["depart_min"] = rt_dep
            first_bus["depart"]     = _min_to_hhmm(rt_dep)
            first_bus["realtime"]   = True
            for leg in itin["legs"][1:]:
                if leg["type"] == "bus":
                    leg["depart_min"] += diff
                    leg["arrive_min"] += diff
                    leg["depart"]      = _min_to_hhmm(leg["depart_min"])
                    leg["arrive"]      = _min_to_hhmm(leg["arrive_min"])
                elif leg["type"] == "transfer":
                    leg["wait_min"] = max(0, leg.get("wait_min", 0) - diff)
            itin["realtime"] = True
    except Exception:
        pass
    return itineraries


# ── Walk legs ──────────────────────────────────────────────────────────────────

def _add_walk_legs(itineraries: list[dict],
                   origin_lat: float, origin_lon: float,
                   dest_lat: float, dest_lon: float) -> list[dict]:
    """Prepend/append walk info and recalculate total_min."""
    for itin in itineraries:
        bus_legs = [l for l in itin["legs"] if l["type"] == "bus"]
        if not bus_legs:
            continue

        first_stop = get_stop_by_id(bus_legs[0]["from_stop_id"])
        last_stop  = get_stop_by_id(bus_legs[-1]["to_stop_id"])

        walk_to = walk_from = 0.0

        if first_stop:
            d = haversine_m(origin_lat, origin_lon,
                            first_stop["lat"], first_stop["lon"])
            walk_to = _walk_min(d)
            itin["walk_to_stop"] = {
                "stop_name":  first_stop["stop_name"],
                "distance_m": round(d),
                "walk_min":   walk_to,
            }

        if last_stop:
            d = haversine_m(dest_lat, dest_lon,
                            last_stop["lat"], last_stop["lon"])
            walk_from = _walk_min(d)
            itin["walk_from_stop"] = {
                "stop_name":  last_stop["stop_name"],
                "distance_m": round(d),
                "walk_min":   walk_from,
            }

        itin["total_min"] = round(walk_to + itin["total_min"] + walk_from, 1)
    return itineraries


# ── Hub-relay fallback ─────────────────────────────────────────────────────────

def _itin_first_dep(itin: dict) -> int:
    bus = next((l for l in itin["legs"] if l["type"] == "bus"), None)
    return bus["depart_min"] if bus else 0


def _itin_last_arr(itin: dict) -> int:
    bus_legs = [l for l in itin["legs"] if l["type"] == "bus"]
    return bus_legs[-1]["arrive_min"] if bus_legs else 0


def _find_via_hub(engine, origin_ids: list[int], dest_ids: list[int],
                  service_ids: tuple, depart_min: int) -> list[dict]:
    """
    Fallback when no direct/1-transfer route exists.
    Tries origin → [major hub] → destination where each leg uses engine.route_depart().
    Bounded: 5 hubs × 3 leg1 options × best leg2 = max 15 RAPTOR calls.
    """
    results: list[dict] = []
    seen: set[tuple] = set()

    for hub in TRANSFER_HUBS:
        hub_id   = hub["stop_id"]
        hub_name = hub["name"]

        if hub_id in origin_ids or hub_id in dest_ids:
            continue

        leg1_opts = engine.route_depart(
            origin_ids, [hub_id], service_ids, depart_min,
            window_min=_SEARCH_WIN_MIN, max_transfers=1
        )
        if not leg1_opts:
            continue

        for l in leg1_opts:
            l["score"] = _score_itinerary(l)
        leg1_opts.sort(key=lambda x: (_itin_first_dep(x), x["score"]))

        for leg1 in leg1_opts[:3]:
            hub_arrive  = _itin_last_arr(leg1)
            leg2_start  = hub_arrive + _HUB_CONNECT_MIN

            leg2_opts = engine.route_depart(
                [hub_id], dest_ids, service_ids, leg2_start,
                window_min=_SEARCH_WIN_MIN, max_transfers=1
            )
            if not leg2_opts:
                continue

            for l in leg2_opts:
                l["score"] = _score_itinerary(l)
            leg2_opts.sort(key=lambda x: x["score"])
            leg2 = leg2_opts[0]

            hub_wait = _itin_first_dep(leg2) - hub_arrive
            if hub_wait < 0 or hub_wait > _MAX_WAIT_MIN:
                continue

            leg1_buses = [l for l in leg1["legs"] if l["type"] == "bus"]
            leg2_buses = [l for l in leg2["legs"] if l["type"] == "bus"]
            key = (
                leg1_buses[0]["route"] if leg1_buses else "",
                hub_id,
                leg2_buses[-1]["route"] if leg2_buses else "",
                _itin_first_dep(leg1) // 30,
            )
            if key in seen:
                continue
            seen.add(key)

            hub_xfer = {
                "type":               "transfer",
                "at_stop_id":         hub_id,
                "at_stop_name":       hub_name,
                "boarding_stop_id":   hub_id,
                "boarding_stop_name": hub_name,
                "walk_min":           0.0,
                "wait_min":           hub_wait,
                "same_side":          True,
                "has_shelter":        True,
            }
            total = _itin_last_arr(leg2) - _itin_first_dep(leg1)
            results.append({
                "type":      "hub_transfer",
                "via_hub":   hub_name,
                "legs":      leg1["legs"] + [hub_xfer] + leg2["legs"],
                "total_min": total,
                "realtime":  (leg1.get("realtime", False) or
                              leg2.get("realtime", False)),
                "same_side": False,
            })

    return results


# ── Scoring & deduplication ────────────────────────────────────────────────────

def _score_itinerary(itin: dict) -> float:
    walk_to   = itin.get("walk_to_stop",   {}).get("walk_min", 0)
    walk_from = itin.get("walk_from_stop", {}).get("walk_min", 0)
    transfers = sum(1 for l in itin["legs"] if l["type"] == "transfer")
    same_side = -2 if itin.get("same_side") else 0
    rt_bonus  = -1 if itin.get("realtime") else 0
    return (itin["total_min"]
            + walk_to + walk_from
            + transfers * 5
            + same_side + rt_bonus)


def _dedup_and_rank(itineraries: list[dict]) -> list[dict]:
    # Drop itineraries with an excessive final-leg walk before anything else.
    # The stop-finder's 5 km fallback can produce stops that are far from the
    # destination; those itineraries technically "work" but suggesting a 17-min
    # walk at the end of a 15-min bus ride makes the planner look broken.
    itineraries = [
        itin for itin in itineraries
        if (itin.get("walk_from_stop") or {}).get("walk_min", 0) <= _MAX_FINAL_WALK_MIN
    ]

    for itin in itineraries:
        itin["score"] = _score_itinerary(itin)

    seen: dict[tuple, dict] = {}
    for itin in itineraries:
        bus_legs   = [l for l in itin["legs"] if l["type"] == "bus"]
        dep_bucket = (bus_legs[0].get("depart_min", 0) if bus_legs else 0) // 30
        if len(bus_legs) > 2:
            key = tuple(l["route"] for l in bus_legs) + (dep_bucket,)
        else:
            r1  = bus_legs[0]["route"] if bus_legs else ""
            r2  = bus_legs[1]["route"] if len(bus_legs) > 1 else ""
            key = (r1, r2, dep_bucket)
        if key not in seen or itin["score"] < seen[key]["score"]:
            seen[key] = itin

    def _first_dep(itin):
        bus = next((l for l in itin["legs"] if l["type"] == "bus"), None)
        return bus.get("depart_min", 0) if bus else 0

    ranked = sorted(seen.values(), key=lambda x: (_first_dep(x), x["score"]))
    return ranked[:_MAX_RESULTS]


# ── Main entry point ───────────────────────────────────────────────────────────

def find_trips(
    origin_lat:   float,
    origin_lon:   float,
    dest_lat:     float,
    dest_lon:     float,
    depart_after: Optional[str] = None,
    arrive_by:    Optional[str] = None,
    target_date:  Optional[date] = None,
) -> dict:
    """
    Find transit options from (origin_lat, origin_lon) to (dest_lat, dest_lon).

    depart_after: "HH:MM" 24h — departing at or after this time
    arrive_by:    "HH:MM" 24h — arriving at or before this time (reverse routing)
    target_date:  date object (defaults to today)

    Returns:
      {
        "itineraries":   [...],
        "origin_stops":  [...],
        "dest_stops":    [...],
        "service_label": str,
        "mode":          "depart" | "arrive",
        "error":         str | None
      }
    """
    if target_date is None:
        target_date = datetime.now(_TZ).date()

    # Determine mode and target minute
    mode = "depart"
    if arrive_by:
        mode = "arrive"
        try:
            h, m    = (int(x) for x in arrive_by.split(":")[:2])
            target_min = h * 60 + m
        except Exception:
            target_min = _now_min()
    elif depart_after:
        try:
            h, m    = (int(x) for x in depart_after.split(":")[:2])
            target_min = h * 60 + m
        except Exception:
            target_min = _now_min()
    else:
        target_min = _now_min()

    engine      = get_engine()
    date_str    = target_date.isoformat()
    service_ids = engine.service_ids_for_date(date_str)

    if not service_ids:
        return {
            "itineraries": [], "origin_stops": [], "dest_stops": [],
            "service_label": None, "mode": mode,
            "error": "No service available on that date.",
        }

    try:
        service_label = get_active_service_label(target_date)
    except Exception:
        service_label = None

    # Find nearby stops (engine spatial index, service-aware)
    origin_stops = engine.find_stops_near(
        origin_lat, origin_lon, _MAX_WALK_M, service_ids
    )[:16]
    if not origin_stops:
        origin_stops = engine.find_stops_near(
            origin_lat, origin_lon, 5000, service_ids
        )[:1]

    dest_stops = engine.find_stops_near(
        dest_lat, dest_lon, _MAX_WALK_M, service_ids
    )[:16]
    if not dest_stops:
        dest_stops = engine.find_stops_near(
            dest_lat, dest_lon, 5000, service_ids
        )[:1]

    if not origin_stops:
        return {
            "itineraries": [], "origin_stops": [], "dest_stops": dest_stops or [],
            "service_label": service_label, "mode": mode,
            "error": "No bus stops with service found near your starting point.",
        }
    if not dest_stops:
        return {
            "itineraries": [], "origin_stops": origin_stops,
            "dest_stops": [],
            "service_label": service_label, "mode": mode,
            "error": "No bus stops with service found near your destination.",
        }

    origin_ids = [s["stop_id"] for s in origin_stops]
    dest_ids   = [s["stop_id"] for s in dest_stops]

    # Route using in-memory RAPTOR (max 2 transfers = 3 bus legs for clean UX)
    if mode == "arrive":
        all_trips = engine.route_arrive(
            origin_ids, dest_ids, service_ids, target_min,
            window_min=_SEARCH_WIN_MIN, max_transfers=2
        )
    else:
        all_trips = engine.route_depart(
            origin_ids, dest_ids, service_ids, target_min,
            window_min=_SEARCH_WIN_MIN, max_transfers=2
        )

    # Hub-relay fallback for 2-transfer gaps (e.g. SE 13th → SW 8th via Downtown)
    if not all_trips and mode == "depart":
        all_trips = _find_via_hub(
            engine, origin_ids, dest_ids, service_ids, target_min
        )

    if not all_trips:
        return {
            "itineraries": [], "origin_stops": origin_stops,
            "dest_stops": dest_stops,
            "service_label": service_label, "mode": mode,
            "error": "No routes found between these locations at that time.",
            "_debug": {
                "target_min":  target_min,
                "target_time": _min_to_hhmm(target_min),
                "target_date": str(target_date),
                "service_ids": list(service_ids),
                "origin_stop_ids": origin_ids,
                "dest_stop_ids":   dest_ids,
            },
        }

    # Drop any malformed itineraries (empty dicts from edge-case RAPTOR paths)
    all_trips = [t for t in all_trips if t.get("legs")]

    all_trips = _enrich_realtime(all_trips, origin_ids)
    all_trips = _add_walk_legs(
        all_trips, origin_lat, origin_lon, dest_lat, dest_lon
    )
    top = _dedup_and_rank(all_trips)

    return {
        "itineraries":   top,
        "origin_stops":  origin_stops[:2],
        "dest_stops":    dest_stops[:2],
        "service_label": service_label,
        "mode":          mode,
        "error":         None,
    }
