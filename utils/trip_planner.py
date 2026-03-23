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
_MAX_TRANSFER_WALK_M = 300  # max walk between transfer stops (directional stop pairs, etc.)
_MAX_WAIT_MIN = 90          # max wait at a transfer — covers 80-min headways on low-frequency routes
_MAX_RESULTS = 5
_SEARCH_WINDOW_MIN = 120   # look for departures within this window
_HUB_CONNECTION_MIN = 3    # minimum connection buffer at a major hub

# Major transfer hubs — used as fallback relay points when no direct/1-transfer route exists.
# Each hub is a major station where many routes converge, making it a natural relay point.
TRANSFER_HUBS = [
    {"stop_id": 1,    "name": "Rosa Parks RTS Downtown Station"},
    {"stop_id": 1493, "name": "Butler Plaza Transfer Station"},
    {"stop_id": 1097, "name": "Oaks Mall"},
    {"stop_id": 473,  "name": "Reitz Union"},
    {"stop_id": 13,   "name": "Beaty Towers"},
]


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


# ── Transfer-walk helper ──────────────────────────────────────────────────────

def _nearby_stops_conn(conn, lat: float, lon: float,
                       radius_m: int, service_ids: list[str]) -> list[dict]:
    """Find stops near (lat, lon) using an already-open DB connection.
    Returns [{stop_id, stop_name, lat, lon, walk_min}] sorted by walk_min.
    Avoids opening extra connections inside the transfer search loops.
    """
    deg = 1 / 111_000
    dlat = radius_m * deg
    dlon = radius_m * deg / max(math.cos(math.radians(lat)), 0.001)
    s_ph = ",".join("?" * len(service_ids))
    rows = conn.execute(f"""
        SELECT DISTINCT CAST(s.stop_id AS INTEGER) AS stop_id,
               s.stop_name,
               CAST(s.stop_lat AS REAL) AS lat,
               CAST(s.stop_lon AS REAL) AS lon
        FROM   stops s
        WHERE  CAST(s.stop_lat AS REAL) BETWEEN ? AND ?
          AND  CAST(s.stop_lon AS REAL) BETWEEN ? AND ?
          AND  EXISTS (
              SELECT 1 FROM stop_times st
              JOIN trips t ON t.trip_id = st.trip_id
              WHERE CAST(st.stop_id AS INTEGER) = CAST(s.stop_id AS INTEGER)
                AND t.service_id IN ({s_ph})
              LIMIT 1
          )
    """, (lat - dlat, lat + dlat, lon - dlon, lon + dlon, *service_ids)).fetchall()

    result = []
    for r in rows:
        dist = haversine_m(lat, lon, r["lat"], r["lon"])
        if dist <= radius_m:
            result.append({
                "stop_id":  r["stop_id"],
                "stop_name": r["stop_name"],
                "lat":      r["lat"],
                "lon":      r["lon"],
                "walk_min": walk_minutes(dist),
            })
    return sorted(result, key=lambda x: x["walk_min"])


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
    """
    Find single-transfer itineraries departing after depart_min.

    Redesigned to run in 3 batched phases instead of N×M individual queries:
      Phase 1 — collect all valid leg1 trips + their transfer stop lists
      Phase 2 — for every unique transfer stop, find nearby boarding stops
                 within _MAX_TRANSFER_WALK_M (handles directional stop pairs)
      Phase 3 — one SQL query for all boarding stops → leg2 options
      Phase 4 — match in Python; apply _MAX_WAIT_MIN (90 min) wait filter
    """
    if not origin_ids or not dest_ids or not service_ids:
        return []

    o_ph = ",".join("?" * len(origin_ids))
    d_ph = ",".join("?" * len(dest_ids))
    s_ph = ",".join("?" * len(service_ids))
    limit_min   = depart_min + _SEARCH_WINDOW_MIN
    depart_gtfs = _min_to_gtfs(depart_min)
    limit_gtfs  = _min_to_gtfs(limit_min)

    # ── Phase 1: Leg-1 departures and their transfer stop lists ───────────────
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

    leg1_trips: list[tuple] = []   # (leg1_row, dep1_min, transfer_stops)
    for leg1 in leg1_rows:
        dep1 = _gtfs_to_min(leg1["depart"])
        if dep1 < depart_min or dep1 > limit_min:
            continue

        # Skip trips that go directly to destination (handled by _find_direct)
        already_direct = conn.execute(f"""
            SELECT 1 FROM stop_times
            WHERE trip_id = ?
              AND CAST(stop_id AS INTEGER) IN ({d_ph})
              AND CAST(stop_sequence AS INTEGER) > CAST(? AS INTEGER)
            LIMIT 1
        """, (leg1["trip_id"], *dest_ids, leg1["seq"])).fetchone()
        if already_direct:
            continue

        transfer_stops = conn.execute("""
            SELECT CAST(stop_id AS INTEGER) AS stop_id, arrival_time
            FROM   stop_times
            WHERE  trip_id = ?
              AND  CAST(stop_sequence AS INTEGER) > CAST(? AS INTEGER)
            ORDER  BY CAST(stop_sequence AS INTEGER)
        """, (leg1["trip_id"], leg1["seq"])).fetchall()

        if transfer_stops:
            leg1_trips.append((leg1, dep1, transfer_stops))

    if not leg1_trips:
        return []

    # ── Phase 2: Boarding-stop map for every unique transfer stop ─────────────
    # For each stop where leg1 could let us off, find all stops within
    # _MAX_TRANSFER_WALK_M we could walk to and board leg2.
    # This is what makes directional stop pairs (e.g. NB stop → nearby SB stop)
    # and short-walk transfers work correctly.
    unique_t_ids: set[int] = set()
    for _, _, tss in leg1_trips:
        for ts in tss:
            unique_t_ids.add(ts["stop_id"])

    # boarding_map[t_stop_id] = [(boarding_stop_id, walk_min), ...]
    boarding_map: dict[int, list[tuple[int, float]]] = {}
    for t_stop_id in unique_t_ids:
        t_stop = get_stop_by_id(t_stop_id)   # O(1) after cache warm
        if not t_stop:
            continue
        nearby = _nearby_stops_conn(conn, t_stop["lat"], t_stop["lon"],
                                    _MAX_TRANSFER_WALK_M, service_ids)
        opts: dict[int, float] = {t_stop_id: 0.0}   # always include self at 0
        for ns in nearby:
            sid = ns["stop_id"]
            if sid not in opts or ns["walk_min"] < opts[sid]:
                opts[sid] = ns["walk_min"]
        boarding_map[t_stop_id] = list(opts.items())

    # ── Phase 3: One batch leg-2 query for all boarding stops ─────────────────
    all_boarding_ids = {bid for opts in boarding_map.values() for bid, _ in opts}
    if not all_boarding_ids:
        return []

    b_ph = ",".join("?" * len(all_boarding_ids))
    # Upper bound: window + max wait so no valid connection is missed
    batch_limit_gtfs = _min_to_gtfs(limit_min + _MAX_WAIT_MIN)

    all_leg2_rows = conn.execute(f"""
        SELECT r.route_short_name          AS route,
               r.route_long_name           AS route_name,
               t.trip_headsign             AS headsign,
               CAST(st1.stop_id AS INTEGER) AS from_stop_id,
               CAST(st2.stop_id AS INTEGER) AS to_stop_id,
               st1.departure_time          AS depart,
               st2.arrival_time            AS arrive
        FROM   stop_times st1
        JOIN   stop_times st2 ON  st2.trip_id = st1.trip_id
                               AND CAST(st2.stop_sequence AS INTEGER) > CAST(st1.stop_sequence AS INTEGER)
        JOIN   trips  t ON t.trip_id  = st1.trip_id
        JOIN   routes r ON r.route_id = t.route_id
        WHERE  CAST(st1.stop_id AS INTEGER) IN ({b_ph})
          AND  CAST(st2.stop_id AS INTEGER) IN ({d_ph})
          AND  t.service_id IN ({s_ph})
          AND  st1.departure_time >= ?
          AND  st1.departure_time <= ?
        ORDER  BY st1.departure_time
    """, [*all_boarding_ids, *dest_ids, *service_ids, depart_gtfs, batch_limit_gtfs]).fetchall()

    # Index by boarding stop for O(1) lookup in Phase 4
    leg2_by_stop: dict[int, list] = {}
    for row in all_leg2_rows:
        leg2_by_stop.setdefault(row["from_stop_id"], []).append(row)

    # ── Phase 4: Match leg1 × transfer_stop × boarding_stop × leg2 ───────────
    results: list[dict] = []
    seen: set[tuple] = set()

    for leg1, dep1, transfer_stops in leg1_trips:
        for ts in transfer_stops:
            t_stop_id = ts["stop_id"]
            arr1_min  = _gtfs_to_min(ts["arrival_time"])
            t_stop    = get_stop_by_id(t_stop_id)   # cached
            if not t_stop or t_stop_id not in boarding_map:
                continue

            for boarding_stop_id, xfer_walk_min in boarding_map[t_stop_id]:
                ready_min = arr1_min + math.ceil(xfer_walk_min)

                for leg2 in leg2_by_stop.get(boarding_stop_id, []):
                    if leg2["route"] == leg1["route"]:
                        continue  # same-route transfer is useless

                    dep2 = _gtfs_to_min(leg2["depart"])
                    arr2 = _gtfs_to_min(leg2["arrive"])

                    wait_min = dep2 - ready_min
                    if wait_min < 0 or wait_min > _MAX_WAIT_MIN:
                        continue

                    ride1  = arr1_min - dep1
                    ride2  = arr2 - dep2 if arr2 >= dep2 else arr2 + 1440 - dep2
                    total  = ride1 + math.ceil(xfer_walk_min) + wait_min + ride2

                    # Same-side: no walk AND stops are not on opposite sides of a crossroad
                    same_side = (xfer_walk_min == 0 and
                                 same_side_penalty_sec(t_stop_id, boarding_stop_id) == 0)

                    key = (leg1["route"], leg1["from_stop_id"],
                           leg2["route"], leg2["to_stop_id"], dep1)
                    if key in seen:
                        continue
                    seen.add(key)

                    boarding_stop = get_stop_by_id(boarding_stop_id) or t_stop

                    results.append({
                        "type": "transfer",
                        "legs": [
                            {
                                "type":         "bus",
                                "route":        leg1["route"],
                                "route_name":   leg1["route_name"],
                                "headsign":     leg1["headsign"],
                                "from_stop_id": int(leg1["from_stop_id"]),
                                "to_stop_id":   t_stop_id,
                                "depart_min":   dep1,
                                "arrive_min":   arr1_min,
                                "depart":       _min_to_hhmm(dep1),
                                "arrive":       _min_to_hhmm(arr1_min),
                                "ride_min":     ride1,
                            },
                            {
                                "type":               "transfer",
                                "at_stop_id":         t_stop_id,
                                "at_stop_name":       t_stop.get("stop_name", ""),
                                "boarding_stop_id":   boarding_stop_id,
                                "boarding_stop_name": boarding_stop.get("stop_name", ""),
                                "walk_min":           round(xfer_walk_min, 1),
                                "wait_min":           wait_min,
                                "same_side":          same_side,
                                "has_shelter":        (boarding_stop.get("shelters") or 0) > 0,
                            },
                            {
                                "type":         "bus",
                                "route":        leg2["route"],
                                "route_name":   leg2["route_name"],
                                "headsign":     leg2["headsign"],
                                "from_stop_id": boarding_stop_id,
                                "to_stop_id":   int(leg2["to_stop_id"]),
                                "depart_min":   dep2,
                                "arrive_min":   arr2,
                                "depart":       _min_to_hhmm(dep2),
                                "arrive":       _min_to_hhmm(arr2),
                                "ride_min":     ride2,
                            },
                        ],
                        "total_min":  total,
                        "realtime":   False,
                        "same_side":  same_side,
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
    """Find single-transfer trips that arrive AT OR BEFORE arrive_min.

    Mirror of _find_with_transfer but searches backwards from destination.
    Uses the same feeder-map approach: leg1 can arrive at any stop within
    _MAX_TRANSFER_WALK_M of leg2's boarding stop.
    """
    if not origin_ids or not dest_ids or not service_ids:
        return []

    d_ph = ",".join("?" * len(dest_ids))
    o_ph = ",".join("?" * len(origin_ids))
    s_ph = ",".join("?" * len(service_ids))
    window_min  = arrive_min - _SEARCH_WINDOW_MIN
    arrive_gtfs = _min_to_gtfs(arrive_min)
    window_gtfs = _min_to_gtfs(window_min)

    # ── Leg 2: trips arriving at destination on time ───────────────────────────
    leg2_rows = conn.execute(f"""
        SELECT r.route_short_name          AS route,
               r.route_long_name           AS route_name,
               t.trip_headsign             AS headsign,
               t.trip_id,
               CAST(st1.stop_id AS INTEGER) AS from_stop_id,
               CAST(st2.stop_id AS INTEGER) AS to_stop_id,
               st1.departure_time          AS depart,
               st2.arrival_time            AS arrive,
               st1.stop_sequence           AS seq
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

    if not leg2_rows:
        return []

    # ── Pre-compute feeder stops for each unique leg2 boarding stop ────────────
    # leg1 can arrive at any feeder stop and walk to the leg2 boarding stop.
    unique_boarding_ids = {int(r["from_stop_id"]) for r in leg2_rows}
    feeder_map: dict[int, list[tuple[int, float]]] = {}
    for boarding_id in unique_boarding_ids:
        t_stop = get_stop_by_id(boarding_id)   # cached
        if not t_stop:
            continue
        nearby = _nearby_stops_conn(conn, t_stop["lat"], t_stop["lon"],
                                    _MAX_TRANSFER_WALK_M, service_ids)
        opts: dict[int, float] = {boarding_id: 0.0}
        for ns in nearby:
            sid = ns["stop_id"]
            if sid not in opts or ns["walk_min"] < opts[sid]:
                opts[sid] = ns["walk_min"]
        feeder_map[boarding_id] = list(opts.items())

    # ── Search leg1 for each leg2 ──────────────────────────────────────────────
    results: list[dict] = []
    seen: set[tuple] = set()

    for leg2 in leg2_rows:
        dep2 = _gtfs_to_min(leg2["depart"])
        arr2 = _gtfs_to_min(leg2["arrive"])
        if arr2 > arrive_min:
            continue

        boarding_stop_id = int(leg2["from_stop_id"])
        boarding_stop    = get_stop_by_id(boarding_stop_id)   # cached
        if not boarding_stop:
            continue

        for feeder_stop_id, xfer_walk_min in feeder_map.get(boarding_stop_id, [(boarding_stop_id, 0.0)]):
            # leg1 must arrive at feeder stop early enough to walk to boarding stop
            latest_arr1_gtfs = _min_to_gtfs(dep2 - math.ceil(xfer_walk_min))

            leg1_rows = conn.execute(f"""
                SELECT r.route_short_name          AS route,
                       r.route_long_name           AS route_name,
                       t.trip_headsign             AS headsign,
                       t.trip_id,
                       CAST(st1.stop_id AS INTEGER) AS from_stop_id,
                       st1.departure_time          AS depart,
                       st1.stop_sequence           AS seq,
                       st2.arrival_time            AS arrive
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
            """, [*origin_ids, feeder_stop_id, *service_ids, latest_arr1_gtfs]).fetchall()

            for leg1 in leg1_rows:
                dep1 = _gtfs_to_min(leg1["depart"])
                arr1 = _gtfs_to_min(leg1["arrive"])
                if arr1 < dep1:
                    continue   # bad GTFS row

                # Skip if leg1 already goes directly to destination
                already_direct = conn.execute(f"""
                    SELECT 1 FROM stop_times
                    WHERE trip_id = ?
                      AND CAST(stop_id AS INTEGER) IN ({d_ph})
                      AND CAST(stop_sequence AS INTEGER) > CAST(? AS INTEGER)
                    LIMIT 1
                """, (leg1["trip_id"], *dest_ids, leg1["seq"])).fetchone()
                if already_direct:
                    continue

                if leg1["route"] == leg2["route"]:
                    continue   # same-route transfer is useless

                # Wait = time from leg1 arrival + walk to when leg2 boards
                wait_min = dep2 - math.ceil(xfer_walk_min) - arr1
                if wait_min < 0 or wait_min > _MAX_WAIT_MIN:
                    continue

                same_side = (xfer_walk_min == 0 and
                             same_side_penalty_sec(feeder_stop_id, boarding_stop_id) == 0)

                ride1  = arr1 - dep1
                ride2  = arr2 - dep2 if arr2 >= dep2 else arr2 + 1440 - dep2
                total  = ride1 + math.ceil(xfer_walk_min) + wait_min + ride2

                key = (leg1["route"], feeder_stop_id, leg2["route"], int(leg2["to_stop_id"]), dep1)
                if key in seen:
                    continue
                seen.add(key)

                feeder_stop = get_stop_by_id(feeder_stop_id) or boarding_stop

                results.append({
                    "type": "transfer",
                    "legs": [
                        {
                            "type":         "bus",
                            "route":        leg1["route"],
                            "route_name":   leg1["route_name"],
                            "headsign":     leg1["headsign"],
                            "from_stop_id": int(leg1["from_stop_id"]),
                            "to_stop_id":   feeder_stop_id,
                            "depart_min":   dep1,
                            "arrive_min":   arr1,
                            "depart":       _min_to_hhmm(dep1),
                            "arrive":       _min_to_hhmm(arr1),
                            "ride_min":     ride1,
                        },
                        {
                            "type":               "transfer",
                            "at_stop_id":         feeder_stop_id,
                            "at_stop_name":       feeder_stop.get("stop_name", ""),
                            "boarding_stop_id":   boarding_stop_id,
                            "boarding_stop_name": boarding_stop.get("stop_name", ""),
                            "walk_min":           round(xfer_walk_min, 1),
                            "wait_min":           wait_min,
                            "same_side":          same_side,
                            "has_shelter":        (boarding_stop.get("shelters") or 0) > 0,
                        },
                        {
                            "type":         "bus",
                            "route":        leg2["route"],
                            "route_name":   leg2["route_name"],
                            "headsign":     leg2["headsign"],
                            "from_stop_id": boarding_stop_id,
                            "to_stop_id":   int(leg2["to_stop_id"]),
                            "depart_min":   dep2,
                            "arrive_min":   arr2,
                            "depart":       _min_to_hhmm(dep2),
                            "arrive":       _min_to_hhmm(arr2),
                            "ride_min":     ride2,
                        },
                    ],
                    "total_min":  total,
                    "realtime":   False,
                    "same_side":  same_side,
                })

    return results


# ── Hub-relay fallback ────────────────────────────────────────────────────────

def _itin_first_dep(itin: dict) -> int:
    bus = next((l for l in itin["legs"] if l["type"] == "bus"), None)
    return bus["depart_min"] if bus else 0

def _itin_last_arr(itin: dict) -> int:
    bus_legs = [l for l in itin["legs"] if l["type"] == "bus"]
    return bus_legs[-1]["arrive_min"] if bus_legs else 0


def _find_via_hub(conn, origin_ids: list[int], dest_ids: list[int],
                  service_ids: list[str], depart_min: int) -> list[dict]:
    """
    Fallback when no direct/single-transfer route exists.
    Tries routing origin → [major hub] → destination where each leg is
    itself a direct or single-transfer trip.  Covers the common case where
    two routes don't share any stop but both pass through a major hub.
    """
    results: list[dict] = []
    seen: set[tuple] = set()

    for hub in TRANSFER_HUBS:
        hub_id   = hub["stop_id"]
        hub_name = hub["name"]

        # Skip redundant: if hub is already in origin or dest stop set
        if hub_id in origin_ids or hub_id in dest_ids:
            continue

        # ── Leg 1: origin → hub ───────────────────────────────────────────
        leg1_opts = (
            _find_direct(conn, origin_ids, [hub_id], service_ids, depart_min) +
            _find_with_transfer(conn, origin_ids, [hub_id], service_ids, depart_min)
        )
        if not leg1_opts:
            continue

        # Score and take the top 3 departure options to the hub
        for l in leg1_opts:
            l["score"] = _score_itinerary(l)
        leg1_opts.sort(key=lambda x: (_itin_first_dep(x), x["score"]))

        for leg1 in leg1_opts[:3]:
            hub_arrive = _itin_last_arr(leg1)
            leg2_start = hub_arrive + _HUB_CONNECTION_MIN

            # ── Leg 2: hub → destination ──────────────────────────────────
            leg2_opts = (
                _find_direct(conn, [hub_id], dest_ids, service_ids, leg2_start) +
                _find_with_transfer(conn, [hub_id], dest_ids, service_ids, leg2_start)
            )
            if not leg2_opts:
                continue

            for l in leg2_opts:
                l["score"] = _score_itinerary(l)
            leg2_opts.sort(key=lambda x: x["score"])
            leg2 = leg2_opts[0]

            leg2_first_dep = _itin_first_dep(leg2)
            hub_wait = leg2_first_dep - hub_arrive
            if hub_wait < 0 or hub_wait > _MAX_WAIT_MIN:
                continue

            # Dedup: first route in leg1, hub, last route in leg2, dep bucket
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

            # Build the hub connector transfer leg
            hub_transfer_leg = {
                "type":               "transfer",
                "at_stop_id":         hub_id,
                "at_stop_name":       hub_name,
                "boarding_stop_id":   hub_id,
                "boarding_stop_name": hub_name,
                "walk_min":           0.0,
                "wait_min":           hub_wait,
                "same_side":          True,
                "has_shelter":        True,   # all major hubs have covered areas
            }

            total = _itin_last_arr(leg2) - _itin_first_dep(leg1)

            results.append({
                "type":      "hub_transfer",
                "via_hub":   hub_name,
                "legs":      leg1["legs"] + [hub_transfer_leg] + leg2["legs"],
                "total_min": total,
                "realtime":  leg1.get("realtime", False) or leg2.get("realtime", False),
                "same_side": False,
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
        bus_legs   = [l for l in itin["legs"] if l["type"] == "bus"]
        dep_bucket = (bus_legs[0].get("depart_min", 0) if bus_legs else 0) // 30
        # For hub-transfer itineraries (3+ bus legs) use full route sequence as key
        # so different hub paths are kept distinct.
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
    origin_stops = find_nearest_stops(origin_lat, origin_lon, radius_m=_MAX_WALK_M, limit=16,
                                      service_ids=service_ids)
    if not origin_stops:
        origin_stops = find_nearest_stops(origin_lat, origin_lon, radius_m=5000, limit=1,
                                          service_ids=service_ids)
    dest_stops = find_nearest_stops(dest_lat, dest_lon, radius_m=_MAX_WALK_M, limit=16,
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

        all_trips = direct + transfers

        # Fallback: no direct/1-transfer route — try routing via major hubs
        if not all_trips and mode == "depart":
            all_trips = _find_via_hub(conn, origin_ids, dest_ids, service_ids, target_min)
    finally:
        conn.close()

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
