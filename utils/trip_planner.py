"""
utils/trip_planner.py
Transit routing engine: direct routes + single transfer, with real-time hybrid.

Algorithm:
  1. Find stops near origin and destination (stop_finder.py)
  2. Direct: GTFS stop_times query for trips covering origin→dest on same trip
  3. Transfer: enumerate routes from origin, find common stops, match routes to dest
  4. Real-time first: replace first leg departure with BusTime prediction when available
  5. Score & rank: composite penalty (walk×2, transfers+5min, same-side bonus)
  6. Deduplicate: key by (route1, transfer_stop, route2)
  7. Arrive-by: reverse routing from destination arrival time
"""
import math
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/New_York")

from routes.schedule_service import connect_db, get_active_service_label
from utils.stop_finder import (
    find_nearest_stops, get_stop_by_id,
    haversine_m, walk_minutes, same_side_penalty_sec,
)

_WALK_SPEED_MPS = 1.2
_MAX_WALK_M = 1000         # max walk to/from a stop (~0.6 mi, needed for suburban stops)
_MAX_TRANSFER_WALK_M = 300  # max walk between transfer stops
_MAX_RESULTS = 5
_SEARCH_WINDOW_MIN = 120   # look for departures within this window


# ── GTFS time helpers ─────────────────────────────────────────────────────────

def _gtfs_to_min(t: str) -> int:
    """'HH:MM:SS' (possibly >24h) → minutes since midnight."""
    h, m, s = (int(x) for x in t.split(":"))
    return h * 60 + m


def _min_to_hhmm(minutes: int) -> str:
    h = (minutes % (24 * 60)) // 60
    m = minutes % 60
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def _now_min() -> int:
    now = datetime.now(_TZ)
    return now.hour * 60 + now.minute


# ── Service ID for a date ─────────────────────────────────────────────────────

def _service_ids_for_date(target_date: date) -> list[str]:
    """Return active service_ids for target_date using GTFS calendar."""
    conn = connect_db()
    try:
        dow = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][target_date.weekday()]
        date_str = target_date.strftime("%Y%m%d")
        rows = conn.execute(f"""
            SELECT service_id FROM calendar
            WHERE {dow} = 1 AND start_date <= ? AND end_date >= ?
        """, (date_str, date_str)).fetchall()
        base = {r["service_id"] for r in rows}

        ex = conn.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?", (date_str,)
        ).fetchall()
        for r in ex:
            if r["exception_type"] == 1:
                base.add(r["service_id"])
            else:
                base.discard(r["service_id"])
        return list(base)
    finally:
        conn.close()


# ── Direct route search ───────────────────────────────────────────────────────

def _min_to_gtfs(minutes: int) -> str:
    """Convert minutes-since-midnight to GTFS 'HH:MM:00' string (supports >24h)."""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}:00"


def _find_direct(conn, origin_ids: list[int], dest_ids: list[int],
                 service_ids: list[str], depart_min: int) -> list[dict]:
    if not origin_ids or not dest_ids or not service_ids:
        return []

    o_ph = ",".join("?" * len(origin_ids))
    d_ph = ",".join("?" * len(dest_ids))
    s_ph = ",".join("?" * len(service_ids))
    limit_min = depart_min + _SEARCH_WINDOW_MIN
    depart_gtfs = _min_to_gtfs(depart_min)
    limit_gtfs = _min_to_gtfs(limit_min)

    rows = conn.execute(f"""
        SELECT r.route_short_name  AS route,
               r.route_long_name   AS route_name,
               t.trip_headsign     AS headsign,
               st1.stop_id         AS from_stop_id,
               st2.stop_id         AS to_stop_id,
               st1.departure_time  AS depart,
               st2.arrival_time    AS arrive
        FROM   stop_times st1
        JOIN   stop_times st2 ON  st2.trip_id = st1.trip_id
                               AND CAST(st2.stop_sequence AS INTEGER) > CAST(st1.stop_sequence AS INTEGER)
        JOIN   trips  t ON t.trip_id   = st1.trip_id
        JOIN   routes r ON r.route_id  = t.route_id
        WHERE  CAST(st1.stop_id AS INTEGER) IN ({o_ph})
          AND  CAST(st2.stop_id AS INTEGER) IN ({d_ph})
          AND  t.service_id IN ({s_ph})
          AND  st1.departure_time >= ?
          AND  st1.departure_time <= ?
        ORDER  BY st1.departure_time
        LIMIT  30
    """, [*origin_ids, *dest_ids, *service_ids, depart_gtfs, limit_gtfs]).fetchall()

    results = []
    for r in rows:
        dep = _gtfs_to_min(r["depart"])
        arr = _gtfs_to_min(r["arrive"])
        if dep < depart_min or dep > limit_min:
            continue
        ride_min = arr - dep if arr >= dep else arr + 1440 - dep
        results.append({
            "type": "direct",
            "legs": [{
                "type": "bus",
                "route": r["route"],
                "route_name": r["route_name"],
                "headsign": r["headsign"],
                "from_stop_id": int(r["from_stop_id"]),
                "to_stop_id": int(r["to_stop_id"]),
                "depart_min": dep,
                "arrive_min": arr,
                "depart": _min_to_hhmm(dep),
                "arrive": _min_to_hhmm(arr),
                "ride_min": ride_min,
            }],
            "total_min": ride_min,
            "realtime": False,
            "same_side": True,
        })
    return results


# ── Single-transfer search ────────────────────────────────────────────────────

def _find_with_transfer(conn, origin_ids: list[int], dest_ids: list[int],
                        service_ids: list[str], depart_min: int) -> list[dict]:
    if not origin_ids or not dest_ids or not service_ids:
        return []

    o_ph = ",".join("?" * len(origin_ids))
    d_ph = ",".join("?" * len(dest_ids))
    s_ph = ",".join("?" * len(service_ids))
    limit_min = depart_min + _SEARCH_WINDOW_MIN

    depart_gtfs = _min_to_gtfs(depart_min)
    limit_gtfs = _min_to_gtfs(limit_min)

    # Leg 1: all departures from origin stops within window
    leg1_rows = conn.execute(f"""
        SELECT r.route_short_name AS route,
               r.route_long_name  AS route_name,
               t.trip_headsign    AS headsign,
               t.trip_id,
               st1.stop_id        AS from_stop_id,
               st1.departure_time AS depart,
               st1.stop_sequence  AS seq
        FROM   stop_times st1
        JOIN   trips  t ON t.trip_id  = st1.trip_id
        JOIN   routes r ON r.route_id = t.route_id
        WHERE  CAST(st1.stop_id AS INTEGER) IN ({o_ph})
          AND  t.service_id IN ({s_ph})
          AND  st1.departure_time >= ?
          AND  st1.departure_time <= ?
        ORDER  BY st1.departure_time
        LIMIT  50
    """, [*origin_ids, *service_ids, depart_gtfs, limit_gtfs]).fetchall()

    results = []
    seen = set()

    for leg1 in leg1_rows:
        dep1 = _gtfs_to_min(leg1["depart"])
        if dep1 < depart_min or dep1 > limit_min:
            continue

        # Skip if leg1's trip already reaches the destination directly —
        # in that case _find_direct handles it and no transfer is needed.
        d_ph_inner = ",".join("?" * len(dest_ids))
        already_direct = conn.execute(f"""
            SELECT 1 FROM stop_times
            WHERE trip_id = ?
              AND CAST(stop_id AS INTEGER) IN ({d_ph_inner})
              AND CAST(stop_sequence AS INTEGER) > CAST(? AS INTEGER)
            LIMIT 1
        """, (leg1["trip_id"], *dest_ids, leg1["seq"])).fetchone()
        if already_direct:
            continue

        # All subsequent stops on this trip (potential transfer points)
        transfer_stops = conn.execute("""
            SELECT CAST(stop_id AS INTEGER) AS stop_id, arrival_time, stop_sequence
            FROM   stop_times
            WHERE  trip_id = ? AND CAST(stop_sequence AS INTEGER) > CAST(? AS INTEGER)
            ORDER  BY CAST(stop_sequence AS INTEGER)
        """, (leg1["trip_id"], leg1["seq"])).fetchall()

        for ts in transfer_stops:
            t_stop_id = ts["stop_id"]
            arr1_min = _gtfs_to_min(ts["arrival_time"])

            # Check walking distance to a nearby dest stop from this transfer stop
            t_stop = get_stop_by_id(t_stop_id)
            if not t_stop:
                continue

            # Leg 2: routes from transfer stop to destination
            leg2_rows = conn.execute(f"""
                SELECT r.route_short_name AS route,
                       r.route_long_name  AS route_name,
                       t.trip_headsign    AS headsign,
                       CAST(st2.stop_id AS INTEGER)  AS to_stop_id,
                       st1.departure_time AS depart,
                       st2.arrival_time   AS arrive
                FROM   stop_times st1
                JOIN   stop_times st2 ON  st2.trip_id = st1.trip_id
                                       AND CAST(st2.stop_sequence AS INTEGER) > CAST(st1.stop_sequence AS INTEGER)
                JOIN   trips  t ON t.trip_id  = st1.trip_id
                JOIN   routes r ON r.route_id = t.route_id
                WHERE  CAST(st1.stop_id AS INTEGER) = ?
                  AND  CAST(st2.stop_id AS INTEGER) IN ({d_ph})
                  AND  t.service_id IN ({s_ph})
                  AND  st1.departure_time >= ?
                ORDER  BY st1.departure_time
                LIMIT  5
            """, [t_stop_id, *dest_ids, *service_ids,
                  ts["arrival_time"]]).fetchall()

            for leg2 in leg2_rows:
                # Skip same-route transfers (e.g. Route 37 → Route 37 via Butler Plaza)
                if leg2["route"] == leg1["route"]:
                    continue

                dep2 = _gtfs_to_min(leg2["depart"])
                arr2 = _gtfs_to_min(leg2["arrive"])
                wait_min = dep2 - arr1_min
                if wait_min < 0:
                    continue  # missed connection
                if wait_min > 30:
                    continue  # too long a wait

                # Same-side penalty
                crossing_sec = same_side_penalty_sec(t_stop_id, t_stop_id)  # same stop
                ride1 = arr1_min - dep1
                ride2 = arr2 - dep2 if arr2 >= dep2 else arr2 + 1440 - dep2
                total = ride1 + wait_min + ride2

                key = (leg1["route"], leg1["from_stop_id"], leg2["route"], leg2["to_stop_id"], dep1)
                if key in seen:
                    continue
                seen.add(key)

                same_side = crossing_sec == 0
                results.append({
                    "type": "transfer",
                    "legs": [
                        {
                            "type": "bus",
                            "route": leg1["route"],
                            "route_name": leg1["route_name"],
                            "headsign": leg1["headsign"],
                            "from_stop_id": int(leg1["from_stop_id"]),
                            "to_stop_id": t_stop_id,
                            "depart_min": dep1,
                            "arrive_min": arr1_min,
                            "depart": _min_to_hhmm(dep1),
                            "arrive": _min_to_hhmm(arr1_min),
                            "ride_min": ride1,
                        },
                        {
                            "type": "transfer",
                            "at_stop_id": t_stop_id,
                            "at_stop_name": t_stop.get("stop_name", ""),
                            "wait_min": wait_min,
                            "same_side": same_side,
                            "has_shelter": (t_stop.get("shelters") or 0) > 0,
                        },
                        {
                            "type": "bus",
                            "route": leg2["route"],
                            "route_name": leg2["route_name"],
                            "headsign": leg2["headsign"],
                            "from_stop_id": t_stop_id,
                            "to_stop_id": int(leg2["to_stop_id"]),
                            "depart_min": dep2,
                            "arrive_min": arr2,
                            "depart": _min_to_hhmm(dep2),
                            "arrive": _min_to_hhmm(arr2),
                            "ride_min": ride2,
                        },
                    ],
                    "total_min": total,
                    "realtime": False,
                    "same_side": same_side,
                })

    return results


# ── Real-time enrichment ──────────────────────────────────────────────────────

def _enrich_realtime(itineraries: list[dict], origin_stop_ids: list[int]) -> list[dict]:
    """Replace first-leg departure with BusTime real-time prediction if available."""
    try:
        import rts_api
        # Correct call: positional stop_id arg, comma-separated for multi-stop
        data = rts_api.get_predictions(",".join(str(s) for s in origin_stop_ids))
        preds = data.get("prd") or []
        if not preds:
            return itineraries

        # Build lookup: route → earliest predicted departure in minutes-since-midnight
        rt_map: dict[str, int] = {}
        now_min = _now_min()
        for p in preds:
            route = str(p.get("rt", ""))
            prd_time = p.get("prdctdn", "")
            if prd_time == "DUE":
                eta = 0
            else:
                try:
                    eta = int(prd_time)
                except Exception:
                    continue
            actual_min = now_min + eta
            if route not in rt_map or actual_min < rt_map[route]:
                rt_map[route] = actual_min

        for itin in itineraries:
            first_bus = next((l for l in itin["legs"] if l["type"] == "bus"), None)
            if not first_bus or first_bus["route"] not in rt_map:
                continue
            rt_dep = rt_map[first_bus["route"]]
            diff = rt_dep - first_bus["depart_min"]
            first_bus["depart_min"] = rt_dep
            first_bus["depart"] = _min_to_hhmm(rt_dep)
            first_bus["realtime"] = True
            # Shift all downstream bus times and recalculate transfer wait_min
            for leg in itin["legs"][1:]:
                if leg["type"] == "bus":
                    leg["depart_min"] = leg.get("depart_min", 0) + diff
                    leg["arrive_min"] = leg.get("arrive_min", 0) + diff
                    leg["depart"] = _min_to_hhmm(leg["depart_min"])
                    leg["arrive"] = _min_to_hhmm(leg["arrive_min"])
                elif leg["type"] == "transfer":
                    # wait_min = leg2_depart - leg1_arrive; both shifted by diff → wait unchanged
                    # but if diff is large enough that we miss the connection, flag it
                    leg["wait_min"] = max(0, leg.get("wait_min", 0) - diff)
            itin["realtime"] = True
    except Exception:
        pass
    return itineraries


# ── Walk legs ─────────────────────────────────────────────────────────────────

def _add_walk_legs(itineraries: list[dict],
                   origin_lat: float, origin_lon: float,
                   dest_lat: float, dest_lon: float) -> list[dict]:
    """Prepend/append walk legs and recalculate total_min."""
    for itin in itineraries:
        legs = itin["legs"]
        bus_legs = [l for l in legs if l["type"] == "bus"]
        if not bus_legs:
            continue

        first_stop = get_stop_by_id(bus_legs[0]["from_stop_id"])
        last_stop = get_stop_by_id(bus_legs[-1]["to_stop_id"])

        walk_to = 0.0
        walk_from = 0.0

        if first_stop:
            d = haversine_m(origin_lat, origin_lon, first_stop["lat"], first_stop["lon"])
            walk_to = walk_minutes(d)
            itin["walk_to_stop"] = {
                "stop_name": first_stop["stop_name"],
                "distance_m": round(d),
                "walk_min": walk_to,
            }

        if last_stop:
            d = haversine_m(dest_lat, dest_lon, last_stop["lat"], last_stop["lon"])
            walk_from = walk_minutes(d)
            itin["walk_from_stop"] = {
                "stop_name": last_stop["stop_name"],
                "distance_m": round(d),
                "walk_min": walk_from,
            }

        itin["total_min"] = round(walk_to + itin["total_min"] + walk_from, 1)
    return itineraries


# ── Arrive-by reverse routing ──────────────────────────────────────────────────

def _find_direct_arrive_by(conn, origin_ids: list[int], dest_ids: list[int],
                            service_ids: list[str], arrive_min: int) -> list[dict]:
    """Find direct trips that arrive at destination AT OR BEFORE arrive_min."""
    if not origin_ids or not dest_ids or not service_ids:
        return []

    o_ph = ",".join("?" * len(origin_ids))
    d_ph = ",".join("?" * len(dest_ids))
    s_ph = ",".join("?" * len(service_ids))
    window_min = arrive_min - _SEARCH_WINDOW_MIN
    arrive_gtfs = _min_to_gtfs(arrive_min)
    window_gtfs = _min_to_gtfs(window_min)

    rows = conn.execute(f"""
        SELECT r.route_short_name  AS route,
               r.route_long_name   AS route_name,
               t.trip_headsign     AS headsign,
               st1.stop_id         AS from_stop_id,
               st2.stop_id         AS to_stop_id,
               st1.departure_time  AS depart,
               st2.arrival_time    AS arrive
        FROM   stop_times st1
        JOIN   stop_times st2 ON  st2.trip_id = st1.trip_id
                               AND CAST(st2.stop_sequence AS INTEGER) > CAST(st1.stop_sequence AS INTEGER)
        JOIN   trips  t ON t.trip_id   = st1.trip_id
        JOIN   routes r ON r.route_id  = t.route_id
        WHERE  CAST(st1.stop_id AS INTEGER) IN ({o_ph})
          AND  CAST(st2.stop_id AS INTEGER) IN ({d_ph})
          AND  t.service_id IN ({s_ph})
          AND  st2.arrival_time <= ?
          AND  st2.arrival_time >= ?
          AND  st2.arrival_time >= st1.departure_time
        ORDER  BY st2.arrival_time DESC
        LIMIT  10
    """, [*origin_ids, *dest_ids, *service_ids, arrive_gtfs, window_gtfs]).fetchall()

    results = []
    for r in rows:
        dep = _gtfs_to_min(r["depart"])
        arr = _gtfs_to_min(r["arrive"])
        if arr > arrive_min or arr < dep:
            continue
        ride_min = arr - dep
        results.append({
            "type": "direct",
            "legs": [{
                "type": "bus",
                "route": r["route"],
                "route_name": r["route_name"],
                "headsign": r["headsign"],
                "from_stop_id": int(r["from_stop_id"]),
                "to_stop_id": int(r["to_stop_id"]),
                "depart_min": dep,
                "arrive_min": arr,
                "depart": _min_to_hhmm(dep),
                "arrive": _min_to_hhmm(arr),
                "ride_min": ride_min,
            }],
            "total_min": ride_min,
            "realtime": False,
            "same_side": True,
        })
    return results


def _find_with_transfer_arrive_by(conn, origin_ids: list[int], dest_ids: list[int],
                                   service_ids: list[str], arrive_min: int) -> list[dict]:
    """Find single-transfer trips that arrive at destination AT OR BEFORE arrive_min."""
    if not origin_ids or not dest_ids or not service_ids:
        return []

    d_ph = ",".join("?" * len(dest_ids))
    o_ph = ",".join("?" * len(origin_ids))
    s_ph = ",".join("?" * len(service_ids))
    window_min = arrive_min - _SEARCH_WINDOW_MIN
    arrive_gtfs = _min_to_gtfs(arrive_min)
    window_gtfs = _min_to_gtfs(window_min)

    # Leg 2: trips arriving at dest on time
    leg2_rows = conn.execute(f"""
        SELECT r.route_short_name AS route,
               r.route_long_name  AS route_name,
               t.trip_headsign    AS headsign,
               t.trip_id,
               CAST(st1.stop_id AS INTEGER) AS from_stop_id,
               CAST(st2.stop_id AS INTEGER) AS to_stop_id,
               st1.departure_time AS depart,
               st2.arrival_time   AS arrive,
               st1.stop_sequence  AS seq
        FROM   stop_times st1
        JOIN   stop_times st2 ON  st2.trip_id = st1.trip_id
                               AND CAST(st2.stop_sequence AS INTEGER) > CAST(st1.stop_sequence AS INTEGER)
        JOIN   trips  t ON t.trip_id  = st1.trip_id
        JOIN   routes r ON r.route_id = t.route_id
        WHERE  CAST(st2.stop_id AS INTEGER) IN ({d_ph})
          AND  t.service_id IN ({s_ph})
          AND  st2.arrival_time <= ?
          AND  st2.arrival_time >= ?
          AND  st2.arrival_time >= st1.departure_time
        ORDER  BY st2.arrival_time DESC
        LIMIT  50
    """, [*dest_ids, *service_ids, arrive_gtfs, window_gtfs]).fetchall()

    results = []
    seen = set()

    for leg2 in leg2_rows:
        dep2 = _gtfs_to_min(leg2["depart"])
        arr2 = _gtfs_to_min(leg2["arrive"])
        if arr2 > arrive_min:
            continue

        transfer_stop_id = leg2["from_stop_id"]
        t_stop = get_stop_by_id(transfer_stop_id)
        if not t_stop:
            continue

        # Leg 1: trips from origin arriving at transfer stop before leg2 departs
        leg1_rows = conn.execute(f"""
            SELECT r.route_short_name AS route,
                   r.route_long_name  AS route_name,
                   t.trip_headsign    AS headsign,
                   t.trip_id,
                   CAST(st1.stop_id AS INTEGER) AS from_stop_id,
                   st1.departure_time AS depart,
                   st1.stop_sequence  AS seq,
                   st2.arrival_time   AS arrive
            FROM   stop_times st1
            JOIN   stop_times st2 ON  st2.trip_id = st1.trip_id
                                   AND CAST(st2.stop_sequence AS INTEGER) > CAST(st1.stop_sequence AS INTEGER)
            JOIN   trips  t ON t.trip_id  = st1.trip_id
            JOIN   routes r ON r.route_id = t.route_id
            WHERE  CAST(st1.stop_id AS INTEGER) IN ({o_ph})
              AND  CAST(st2.stop_id AS INTEGER) = ?
              AND  t.service_id IN ({s_ph})
              AND  st2.arrival_time <= ?
            ORDER  BY st2.arrival_time DESC
            LIMIT  3
        """, [*origin_ids, transfer_stop_id, *service_ids, leg2["depart"]]).fetchall()

        for leg1 in leg1_rows:
            dep1 = _gtfs_to_min(leg1["depart"])
            arr1 = _gtfs_to_min(leg1["arrive"])
            if arr1 < dep1:
                continue   # bad GTFS row

            # Skip if leg1's trip already reaches the destination directly
            already_direct = conn.execute(f"""
                SELECT 1 FROM stop_times
                WHERE trip_id = ?
                  AND CAST(stop_id AS INTEGER) IN ({d_ph})
                  AND CAST(stop_sequence AS INTEGER) > CAST(? AS INTEGER)
                LIMIT 1
            """, (leg1["trip_id"], *dest_ids, leg1["seq"])).fetchone()
            if already_direct:
                continue

            # Skip same-route transfers
            if leg1["route"] == leg2["route"]:
                continue

            wait_min = dep2 - arr1
            if wait_min < 0 or wait_min > 30:
                continue

            crossing_sec = same_side_penalty_sec(transfer_stop_id, transfer_stop_id)
            ride1 = arr1 - dep1
            ride2 = arr2 - dep2 if arr2 >= dep2 else arr2 + 1440 - dep2
            total = ride1 + wait_min + ride2

            key = (leg1["route"], transfer_stop_id, leg2["route"], int(leg2["to_stop_id"]), dep1)
            if key in seen:
                continue
            seen.add(key)

            same_side = crossing_sec == 0
            results.append({
                "type": "transfer",
                "legs": [
                    {
                        "type": "bus",
                        "route": leg1["route"],
                        "route_name": leg1["route_name"],
                        "headsign": leg1["headsign"],
                        "from_stop_id": int(leg1["from_stop_id"]),
                        "to_stop_id": transfer_stop_id,
                        "depart_min": dep1,
                        "arrive_min": arr1,
                        "depart": _min_to_hhmm(dep1),
                        "arrive": _min_to_hhmm(arr1),
                        "ride_min": ride1,
                    },
                    {
                        "type": "transfer",
                        "at_stop_id": transfer_stop_id,
                        "at_stop_name": t_stop.get("stop_name", ""),
                        "wait_min": wait_min,
                        "same_side": same_side,
                        "has_shelter": (t_stop.get("shelters") or 0) > 0,
                    },
                    {
                        "type": "bus",
                        "route": leg2["route"],
                        "route_name": leg2["route_name"],
                        "headsign": leg2["headsign"],
                        "from_stop_id": transfer_stop_id,
                        "to_stop_id": int(leg2["to_stop_id"]),
                        "depart_min": dep2,
                        "arrive_min": arr2,
                        "depart": _min_to_hhmm(dep2),
                        "arrive": _min_to_hhmm(arr2),
                        "ride_min": ride2,
                    },
                ],
                "total_min": total,
                "realtime": False,
                "same_side": same_side,
            })

    return results


# ── Scoring & deduplication ────────────────────────────────────────────────────

def _score_itinerary(itin: dict) -> float:
    """
    Composite penalty score — lower is better.
    Walking counts double its time contribution.
    Each transfer adds a flat 5-min penalty.
    Same-side transfer saves 2 min. Real-time data saves 1 min.
    """
    walk_to_min  = itin.get("walk_to_stop",   {}).get("walk_min", 0)
    walk_from_min = itin.get("walk_from_stop", {}).get("walk_min", 0)
    transfers = sum(1 for l in itin["legs"] if l["type"] == "transfer")
    same_side_bonus = -2 if itin.get("same_side") else 0
    rt_bonus        = -1 if itin.get("realtime") else 0

    return (itin["total_min"]
            + walk_to_min + walk_from_min   # walk counts double (already in total_min once)
            + transfers * 5
            + same_side_bonus
            + rt_bonus)


def _dedup_and_rank(itineraries: list[dict]) -> list[dict]:
    """Score, deduplicate, sort, return top _MAX_RESULTS.

    Dedup key: (route1, xfer_stop, route2, dep_30min_bucket)
    Trips with the same route combo but different departure windows (30-min buckets)
    are kept as separate options — matches what the official RTS planner shows.
    Within the same bucket, keep only the lowest-score variant.
    """
    for itin in itineraries:
        itin["score"] = _score_itinerary(itin)

    seen: dict[tuple, dict] = {}
    for itin in itineraries:
        bus_legs  = [l for l in itin["legs"] if l["type"] == "bus"]
        xfer_legs = [l for l in itin["legs"] if l["type"] == "transfer"]
        r1   = bus_legs[0]["route"] if bus_legs else ""
        r2   = bus_legs[1]["route"] if len(bus_legs) > 1 else ""
        xfer = xfer_legs[0].get("at_stop_name", "") if xfer_legs else ""
        dep_bucket = (bus_legs[0].get("depart_min", 0) if bus_legs else 0) // 30
        key = (r1, xfer, r2, dep_bucket)
        if key not in seen or itin["score"] < seen[key]["score"]:
            seen[key] = itin

    def _first_dep(itin):
        bus = next((l for l in itin["legs"] if l["type"] == "bus"), None)
        return bus.get("depart_min", 0) if bus else 0

    ranked = sorted(seen.values(), key=lambda x: (_first_dep(x), x["score"]))
    return ranked[:_MAX_RESULTS]


# ── Main entry point ──────────────────────────────────────────────────────────

def find_trips(origin_lat: float, origin_lon: float,
               dest_lat: float, dest_lon: float,
               depart_after: Optional[str] = None,
               arrive_by: Optional[str] = None,
               target_date: Optional[date] = None) -> dict:
    """
    Find transit options from (origin_lat, origin_lon) to (dest_lat, dest_lon).

    depart_after: "HH:MM" 24h — find trips departing at or after this time
    arrive_by:    "HH:MM" 24h — find trips arriving at or before this time (reverse routing)
    target_date:  date object, defaults to today

    Returns:
      {
        "itineraries": [...],     # up to 3, scored and ranked
        "origin_stops": [...],
        "dest_stops": [...],
        "service_label": str,     # "Weekday" / "Reduced Service" / etc.
        "mode": "depart"|"arrive",
        "error": str | None
      }
    """
    if target_date is None:
        target_date = datetime.now(_TZ).date()

    # Determine mode and target minute
    mode = "depart"
    if arrive_by:
        mode = "arrive"
        try:
            h, m = (int(x) for x in arrive_by.split(":")[:2])
            target_min = h * 60 + m
        except Exception:
            target_min = _now_min()
    elif depart_after:
        try:
            h, m = (int(x) for x in depart_after.split(":")[:2])
            target_min = h * 60 + m
        except Exception:
            target_min = _now_min()
    else:
        target_min = _now_min()

    service_ids = _service_ids_for_date(target_date)
    if not service_ids:
        return {"itineraries": [], "origin_stops": [], "dest_stops": [],
                "service_label": None, "mode": mode,
                "error": "No service available on that date."}

    # Get service label for the target date (not always today)
    try:
        from routes.schedule_service import get_active_service_label
        service_label = get_active_service_label(target_date)
    except Exception:
        service_label = None

    # Find stops served by today's active service_ids (avoids returning stops
    # that only exist on Weekday when today is Reduced_Service, or vice versa)
    origin_stops = find_nearest_stops(origin_lat, origin_lon, radius_m=_MAX_WALK_M, limit=4,
                                      service_ids=service_ids)
    if not origin_stops:
        origin_stops = find_nearest_stops(origin_lat, origin_lon, radius_m=5000, limit=1,
                                          service_ids=service_ids)
    dest_stops = find_nearest_stops(dest_lat, dest_lon, radius_m=_MAX_WALK_M, limit=4,
                                    service_ids=service_ids)
    if not dest_stops:
        dest_stops = find_nearest_stops(dest_lat, dest_lon, radius_m=5000, limit=1,
                                        service_ids=service_ids)

    if not origin_stops:
        return {"itineraries": [], "origin_stops": [], "dest_stops": dest_stops or [],
                "service_label": service_label, "mode": mode,
                "error": "No bus stops with service found near your starting point."}
    if not dest_stops:
        return {"itineraries": [], "origin_stops": origin_stops, "dest_stops": [],
                "service_label": service_label, "mode": mode,
                "error": "No bus stops with service found near your destination."}

    origin_ids = [s["stop_id"] for s in origin_stops]
    dest_ids   = [s["stop_id"] for s in dest_stops]

    conn = connect_db()
    try:
        if mode == "arrive":
            direct    = _find_direct_arrive_by(conn, origin_ids, dest_ids, service_ids, target_min)
            transfers = _find_with_transfer_arrive_by(conn, origin_ids, dest_ids, service_ids, target_min)
        else:
            direct    = _find_direct(conn, origin_ids, dest_ids, service_ids, target_min)
            transfers = _find_with_transfer(conn, origin_ids, dest_ids, service_ids, target_min)
    finally:
        conn.close()

    all_trips = direct + transfers
    if not all_trips:
        return {"itineraries": [], "origin_stops": origin_stops, "dest_stops": dest_stops,
                "service_label": service_label, "mode": mode,
                "error": "No routes found between these locations at that time.",
                "_debug": {
                    "target_min": target_min,
                    "target_time": _min_to_hhmm(target_min),
                    "target_date": str(target_date),
                    "service_ids": service_ids,
                    "origin_stop_ids": origin_ids,
                    "dest_stop_ids": dest_ids,
                    "window_end_min": target_min + _SEARCH_WINDOW_MIN,
                    "window_end_time": _min_to_hhmm(target_min + _SEARCH_WINDOW_MIN),
                }}

    all_trips = _enrich_realtime(all_trips, origin_ids)
    all_trips = _add_walk_legs(all_trips, origin_lat, origin_lon, dest_lat, dest_lon)
    top = _dedup_and_rank(all_trips)

    return {
        "itineraries": top,
        "origin_stops": origin_stops[:2],
        "dest_stops": dest_stops[:2],
        "service_label": service_label,
        "mode": mode,
        "error": None,
    }
