"""
utils/gtfs_engine.py
In-memory GTFS graph with RAPTOR-inspired multi-transfer routing.

Loads all GTFS data from rts_gtfs.sqlite at startup (~2-3s, ~10 MB RAM).
Zero SQL during routing — all lookups are O(1) or O(log n) in memory.

Architecture:
    stops          dict[int, tuple]     stop_id → (lat, lon, name)
    trip_info      dict[str, tuple]     trip_id → (service_id, route_short, route_long, headsign)
    trip_stops     dict[str, list]      trip_id → [(seq, stop_id, dep_min, arr_min)] sorted by seq
    stop_departs   dict[int, list]      stop_id → [(dep_min, trip_id, pos)] sorted by dep_min
    transfers      dict[int, list]      stop_id → [(to_stop_id, walk_min)] sorted by walk_min
    _spatial       dict[tuple, list]    (lat_cell, lon_cell) → [stop_ids]

RAPTOR rounds:
    Round 1: scan all trips from origin stops  → direct journeys
    Round 2: scan trips from round-1 endpoints → 1-transfer journeys
    ...up to max_transfers + 1 rounds total
"""
import bisect
import functools
import math
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from routes.schedule_service import DB_PATH

# ── Constants ─────────────────────────────────────────────────────────────────

_WALK_SPEED_MPS   = 1.2
_TRANSFER_RADIUS_M = 300       # max walk between transfer stops
_SPATIAL_CELL     = 0.005      # ~550 m at Gainesville latitude
_INF              = 10 ** 7    # sentinel "unreachable" arrival time

# ── Helpers ───────────────────────────────────────────────────────────────────

def _gtfs_to_min(t: str) -> int:
    """'HH:MM:SS' (supports >24 h) → minutes since midnight."""
    h, m, _ = t.split(":", 2)
    return int(h) * 60 + int(m)


def _min_to_hhmm(minutes: int) -> str:
    minutes = minutes % (24 * 60)
    h, m   = divmod(minutes, 60)
    suffix = "AM" if h < 12 else "PM"
    h12    = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R    = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    a    = (math.sin((phi2 - phi1) / 2) ** 2
            + math.cos(phi1) * math.cos(phi2)
            * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def walk_min(dist_m: float) -> float:
    return round(dist_m / (_WALK_SPEED_MPS * 60), 1)


# ── GTFSEngine ────────────────────────────────────────────────────────────────

class GTFSEngine:
    """
    In-memory GTFS graph.  Initialise once at server startup; thread-safe for reads.
    """

    def __init__(self) -> None:
        import time
        t0 = time.perf_counter()
        self._load()
        self._build_spatial_index()
        self._build_transfer_index()
        elapsed = time.perf_counter() - t0
        print(f"[GTFSEngine] Loaded {len(self.stops):,} stops, "
              f"{len(self.trip_stops):,} trips in {elapsed:.1f}s")

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load(self) -> None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # stops: stop_id → (lat, lon, name)
        self.stops: dict[int, tuple] = {}
        for r in conn.execute(
            "SELECT CAST(stop_id AS INTEGER) AS sid, stop_name, "
            "CAST(stop_lat AS REAL) AS lat, CAST(stop_lon AS REAL) AS lon "
            "FROM stops"
        ):
            self.stops[r["sid"]] = (r["lat"], r["lon"], r["stop_name"] or "")

        # routes: route_id → (short_name, long_name)
        _routes: dict[str, tuple] = {}
        for r in conn.execute(
            "SELECT route_id, route_short_name, route_long_name FROM routes"
        ):
            _routes[r["route_id"]] = (
                r["route_short_name"] or "", r["route_long_name"] or ""
            )

        # trips: trip_id → (service_id, route_short, route_long, headsign)
        self.trip_info: dict[str, tuple] = {}
        for r in conn.execute(
            "SELECT trip_id, service_id, route_id, trip_headsign FROM trips"
        ):
            rshort, rlong = _routes.get(r["route_id"], ("?", ""))
            self.trip_info[r["trip_id"]] = (
                r["service_id"], rshort, rlong, r["trip_headsign"] or ""
            )

        # stop_times → trip_stops + stop_departs
        # trip_stops[trip_id]  = [(seq, stop_id, dep_min, arr_min)]  sorted by seq
        # stop_departs[stop_id] = [(dep_min, trip_id, pos)]           sorted by dep_min
        self.trip_stops: dict[str, list]  = {}
        _raw_departs:    dict[int, list]  = {}

        for r in conn.execute("""
            SELECT trip_id,
                   CAST(stop_sequence AS INTEGER) AS seq,
                   CAST(stop_id AS INTEGER)       AS stop_id,
                   departure_time, arrival_time
            FROM   stop_times
            ORDER  BY trip_id, CAST(stop_sequence AS INTEGER)
        """):
            tid = r["trip_id"]
            sid = r["stop_id"]
            dep = _gtfs_to_min(r["departure_time"])
            arr = _gtfs_to_min(r["arrival_time"])

            if tid not in self.trip_stops:
                self.trip_stops[tid] = []
            pos = len(self.trip_stops[tid])
            self.trip_stops[tid].append((r["seq"], sid, dep, arr))

            _raw_departs.setdefault(sid, []).append((dep, tid, pos))

        # Sort stop_departs by departure time for fast binary search
        self.stop_departs: dict[int, list] = {
            sid: sorted(deps, key=lambda x: x[0])
            for sid, deps in _raw_departs.items()
        }

        # Store load timestamp + counts for /api/gtfs-info
        import datetime
        self.loaded_at   = datetime.datetime.now().isoformat(timespec="seconds")
        self.stop_count  = len(self.stops)
        self.trip_count  = len(self.trip_stops)

        conn.close()

    # ── Spatial index ─────────────────────────────────────────────────────────

    def _build_spatial_index(self) -> None:
        self._spatial: dict[tuple, list] = {}
        for sid, (lat, lon, _) in self.stops.items():
            cell = (int(lat / _SPATIAL_CELL), int(lon / _SPATIAL_CELL))
            self._spatial.setdefault(cell, []).append(sid)

    def _stops_in_radius(self, lat: float, lon: float, radius_m: float,
                         active_stops: Optional[frozenset] = None):
        """Yield (stop_id, dist_m) for stops within radius_m."""
        dlat = radius_m / 111_000
        dlon = radius_m / (111_000 * max(math.cos(math.radians(lat)), 0.001))
        la0  = int((lat - dlat) / _SPATIAL_CELL)
        la1  = int((lat + dlat) / _SPATIAL_CELL)
        lo0  = int((lon - dlon) / _SPATIAL_CELL)
        lo1  = int((lon + dlon) / _SPATIAL_CELL)

        seen: set[int] = set()
        for la in range(la0, la1 + 1):
            for lo in range(lo0, lo1 + 1):
                for sid in self._spatial.get((la, lo), []):
                    if sid in seen:
                        continue
                    seen.add(sid)
                    if active_stops is not None and sid not in active_stops:
                        continue
                    slat, slon, _ = self.stops[sid]
                    dist = haversine_m(lat, lon, slat, slon)
                    if dist <= radius_m:
                        yield sid, dist

    # ── Transfer index ────────────────────────────────────────────────────────

    def _build_transfer_index(self) -> None:
        """Pre-compute walk connections between stops within _TRANSFER_RADIUS_M."""
        self.transfers: dict[int, list] = {}
        for sid, (lat, lon, _) in self.stops.items():
            nearby = [
                (to_sid, walk_min(dist))
                for to_sid, dist in self._stops_in_radius(lat, lon, _TRANSFER_RADIUS_M)
                if to_sid != sid
            ]
            self.transfers[sid] = sorted(nearby, key=lambda x: x[1])

    # ── Service IDs ───────────────────────────────────────────────────────────

    @functools.lru_cache(maxsize=128)
    def service_ids_for_date(self, date_str: str) -> tuple:
        """
        Return active service_ids for the given date ('YYYY-MM-DD') as a tuple.
        Result is cached so repeated calls (same date) hit no DB at all.
        """
        d   = date.fromisoformat(date_str)
        dow = ["monday", "tuesday", "wednesday", "thursday",
               "friday", "saturday", "sunday"][d.weekday()]
        ds  = d.strftime("%Y%m%d")
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute(
                f"SELECT service_id FROM calendar "
                f"WHERE {dow}=1 AND start_date<=? AND end_date>=?",
                (ds, ds)
            ).fetchall()
            sids = {r[0] for r in rows}
            for r in conn.execute(
                "SELECT service_id, exception_type FROM calendar_dates WHERE date=?",
                (ds,)
            ).fetchall():
                if r[1] == 1:
                    sids.add(r[0])
                else:
                    sids.discard(r[0])
            return tuple(sids)
        finally:
            conn.close()

    # ── Active stops cache ────────────────────────────────────────────────────

    @functools.lru_cache(maxsize=32)
    def _active_stops_for_service(self, service_ids_tuple: tuple) -> frozenset:
        """Return frozenset of stop_ids that have at least one trip for these service_ids."""
        svc_set = set(service_ids_tuple)
        result: set[int] = set()
        for trip_id, (svc_id, *_) in self.trip_info.items():
            if svc_id in svc_set:
                for _, sid, _, _ in self.trip_stops.get(trip_id, []):
                    result.add(sid)
        return frozenset(result)

    # ── Stop finder ───────────────────────────────────────────────────────────

    def find_stops_near(self, lat: float, lon: float, radius_m: int,
                        service_ids: Optional[tuple] = None) -> list[dict]:
        """
        Return stop dicts sorted by walking distance within radius_m.
        If service_ids is provided, only stops served by those service_ids are returned.
        """
        active = (self._active_stops_for_service(service_ids)
                  if service_ids else None)
        results = []
        for sid, dist in self._stops_in_radius(lat, lon, radius_m, active):
            slat, slon, sname = self.stops[sid]
            results.append({
                "stop_id":    sid,
                "stop_name":  sname,
                "lat":        slat,
                "lon":        slon,
                "distance_m": round(dist),
                "walk_min":   walk_min(dist),
            })
        return sorted(results, key=lambda x: x["distance_m"])

    # ── Trip departure lookup ─────────────────────────────────────────────────

    def _trips_from(self, stop_id: int, from_min: int, to_min: int,
                    service_set: set) -> list[tuple]:
        """
        Return [(dep_min, trip_id, pos_in_trip)] for trips departing from stop_id
        between from_min and to_min for the given service_ids.
        Binary search on pre-sorted stop_departs.
        """
        deps = self.stop_departs.get(stop_id)
        if not deps:
            return []

        # Binary search: first index where dep_min >= from_min
        keys = [d[0] for d in deps]
        lo   = bisect.bisect_left(keys, from_min)

        result = []
        for i in range(lo, len(deps)):
            dep_min, trip_id, pos = deps[i]
            if dep_min > to_min:
                break
            svc_id = self.trip_info[trip_id][0]
            if svc_id in service_set:
                result.append((dep_min, trip_id, pos))
        return result

    # ── RAPTOR forward routing (depart-after) ─────────────────────────────────

    def route_depart(
        self,
        origin_ids:   list[int],
        dest_ids:     list[int],
        service_ids:  tuple,
        depart_min:   int,
        window_min:   int = 120,
        max_transfers: int = 3,
    ) -> list[dict]:
        """
        Multi-round RAPTOR-inspired forward routing.

        Round k scans trips from stops newly reached in round k-1.
        Returns a list of itinerary dicts (same schema as trip_planner.py).

        Pruning:
        - Within a round: only update a stop if the new arrival improves tau_star
        - Between rounds: only expand from stops improved in the previous round
        - This guarantees correctness: if we reach dest in 40 min via 1 transfer,
          we'll still find the 35 min 2-transfer option in round 3.
        """
        service_set = set(service_ids)
        dest_set    = set(dest_ids)
        # Add a generous extension so 90-min waits don't get cut off
        limit_min   = depart_min + window_min + max_transfers * 90

        # tau_star[stop_id] = overall best arrival (across all rounds)
        tau_star: dict[int, int] = {}
        for sid in origin_ids:
            if sid in self.stops:
                tau_star[sid] = depart_min

        # journey_star[stop_id] = best leg list to reach that stop
        journey_star: dict[int, list] = {
            sid: [] for sid in origin_ids if sid in self.stops
        }

        # Apply initial walk legs from origins
        for sid in list(tau_star.keys()):
            for (to_sid, wm) in self.transfers.get(sid, []):
                arr = depart_min + math.ceil(wm)
                if arr < tau_star.get(to_sid, _INF):
                    tau_star[to_sid]   = arr
                    journey_star[to_sid] = []  # walk-only; bus leg follows next round

        # marked = stops to expand from in the current round
        marked = set(tau_star.keys())

        results: list[dict] = []

        for _round in range(1, max_transfers + 2):
            if not marked:
                break

            tau_k:    dict[int, int]  = {}   # improvements this round
            journey_k: dict[int, list] = {}

            for stop_id in marked:
                board_time = tau_star[stop_id]
                prev_legs  = journey_star.get(stop_id, [])
                prev_routes = {l["route"] for l in prev_legs if l["type"] == "bus"}

                for (dep_min, trip_id, board_pos) in self._trips_from(
                    stop_id, board_time, depart_min + window_min, service_set
                ):
                    _, rshort, rlong, headsign = self.trip_info[trip_id]

                    if rshort in prev_routes:
                        continue  # avoid circular same-route transfers

                    trip_stop_list = self.trip_stops[trip_id]

                    for i in range(board_pos + 1, len(trip_stop_list)):
                        _, cur_stop, _, arr_i = trip_stop_list[i]

                        if arr_i > limit_min:
                            break

                        # Only update if this genuinely improves the best known arrival
                        if arr_i >= tau_star.get(cur_stop, _INF):
                            continue
                        if arr_i >= tau_k.get(cur_stop, _INF):
                            continue

                        tau_k[cur_stop]    = arr_i
                        tau_star[cur_stop] = arr_i

                        bus_leg = {
                            "type":         "bus",
                            "route":        rshort,
                            "route_name":   rlong,
                            "headsign":     headsign,
                            "from_stop_id": stop_id,
                            "to_stop_id":   cur_stop,
                            "depart_min":   dep_min,
                            "arrive_min":   arr_i,
                            "depart":       _min_to_hhmm(dep_min),
                            "arrive":       _min_to_hhmm(arr_i),
                            "ride_min":     arr_i - dep_min,
                        }
                        journey_k[cur_stop]    = prev_legs + [bus_leg]
                        journey_star[cur_stop] = journey_k[cur_stop]

                        if cur_stop in dest_set:
                            results.append(
                                self._build_itinerary(journey_k[cur_stop])
                            )

            # Apply footpaths (transfer walks) from this round's improvements
            for stop_id in list(tau_k.keys()):
                arr_stop    = tau_k[stop_id]
                journey_to  = journey_k[stop_id]

                for (to_sid, wm) in self.transfers.get(stop_id, []):
                    xfer_arr = arr_stop + math.ceil(wm)
                    if xfer_arr >= tau_star.get(to_sid, _INF):
                        continue
                    if xfer_arr >= tau_k.get(to_sid, _INF):
                        continue

                    tau_k[to_sid]    = xfer_arr
                    tau_star[to_sid] = xfer_arr

                    xfer_leg = {
                        "type":               "transfer",
                        "at_stop_id":         stop_id,
                        "at_stop_name":       self.stops[stop_id][2],
                        "boarding_stop_id":   to_sid,
                        "boarding_stop_name": self.stops.get(to_sid, (0, 0, ""))[2],
                        "walk_min":           wm,
                        "wait_min":           0,  # will be updated in _build_itinerary
                        "same_side":          False,
                        "has_shelter":        False,
                    }
                    journey_k[to_sid]    = journey_to + [xfer_leg]
                    journey_star[to_sid] = journey_k[to_sid]

                    if to_sid in dest_set:
                        # Pure walk to destination (rare but valid)
                        results.append(
                            self._build_itinerary(journey_k[to_sid])
                        )

            journey_star.update(journey_k)
            marked = set(tau_k.keys())

        return results

    # ── RAPTOR backward routing (arrive-by) ───────────────────────────────────

    def route_arrive(
        self,
        origin_ids:   list[int],
        dest_ids:     list[int],
        service_ids:  tuple,
        arrive_min:   int,
        window_min:   int = 120,
        max_transfers: int = 3,
    ) -> list[dict]:
        """
        Backward RAPTOR: search from destination backwards to find trips
        that arrive at dest AT OR BEFORE arrive_min.

        Mirrors route_depart() with reversed trip direction and reversed time ordering.
        """
        service_set = set(service_ids)
        origin_set  = set(origin_ids)
        limit_min   = arrive_min - window_min - max_transfers * 90

        # Pre-build: trip_stop_reversed for backward scan
        # tau_star here means: latest feasible departure from this stop
        # (the latest you can leave and still reach dest by arrive_min)
        tau_star: dict[int, int] = {}
        for sid in dest_ids:
            if sid in self.stops:
                tau_star[sid] = arrive_min

        # Walk legs FROM dest backwards
        for sid in list(tau_star.keys()):
            for (from_sid, wm) in self.transfers.get(sid, []):
                dep = arrive_min - math.ceil(wm)
                if dep > tau_star.get(from_sid, -_INF):
                    tau_star[from_sid] = dep

        journey_star: dict[int, list] = {
            sid: [] for sid in dest_ids if sid in self.stops
        }

        marked = set(tau_star.keys())
        results: list[dict] = []

        for _round in range(1, max_transfers + 2):
            if not marked:
                break

            tau_k:    dict[int, int]  = {}
            journey_k: dict[int, list] = {}

            for stop_id in marked:
                latest_arr = tau_star[stop_id]
                next_legs  = journey_star.get(stop_id, [])
                next_routes = {l["route"] for l in next_legs if l["type"] == "bus"}

                # Find all trips that ARRIVE at stop_id at or before latest_arr
                # and departed within the window
                for (dep_min, trip_id, arr_pos) in self._trips_to(
                    stop_id, max(limit_min, 0), latest_arr, service_set
                ):
                    _, rshort, rlong, headsign = self.trip_info[trip_id]

                    if rshort in next_routes:
                        continue

                    trip_stop_list = self.trip_stops[trip_id]

                    # Scan BACKWARDS from arr_pos
                    for i in range(arr_pos - 1, -1, -1):
                        _, cur_stop, dep_i, arr_i = trip_stop_list[i]

                        if dep_i < limit_min:
                            break

                        # Improve: latest time we can depart cur_stop and still make it
                        if dep_i <= tau_star.get(cur_stop, -_INF):
                            continue
                        if dep_i <= tau_k.get(cur_stop, -_INF):
                            continue

                        tau_k[cur_stop]    = dep_i
                        tau_star[cur_stop] = dep_i

                        # The actual arrival at stop_id from this trip
                        _, _, _, arr_at_xfer = trip_stop_list[arr_pos]

                        bus_leg = {
                            "type":         "bus",
                            "route":        rshort,
                            "route_name":   rlong,
                            "headsign":     headsign,
                            "from_stop_id": cur_stop,
                            "to_stop_id":   stop_id,
                            "depart_min":   dep_i,
                            "arrive_min":   arr_at_xfer,
                            "depart":       _min_to_hhmm(dep_i),
                            "arrive":       _min_to_hhmm(arr_at_xfer),
                            "ride_min":     arr_at_xfer - dep_i,
                        }
                        journey_k[cur_stop]    = [bus_leg] + next_legs
                        journey_star[cur_stop] = journey_k[cur_stop]

                        if cur_stop in origin_set:
                            results.append(
                                self._build_itinerary(journey_k[cur_stop])
                            )

            # Backward footpaths
            for stop_id in list(tau_k.keys()):
                dep_stop   = tau_k[stop_id]
                journey_to = journey_k[stop_id]

                for (from_sid, wm) in self.transfers.get(stop_id, []):
                    dep_from = dep_stop - math.ceil(wm)
                    if dep_from <= tau_star.get(from_sid, -_INF):
                        continue
                    if dep_from <= tau_k.get(from_sid, -_INF):
                        continue

                    tau_k[from_sid]    = dep_from
                    tau_star[from_sid] = dep_from

                    xfer_leg = {
                        "type":               "transfer",
                        "at_stop_id":         from_sid,
                        "at_stop_name":       self.stops.get(from_sid, (0, 0, ""))[2],
                        "boarding_stop_id":   stop_id,
                        "boarding_stop_name": self.stops[stop_id][2],
                        "walk_min":           wm,
                        "wait_min":           0,
                        "same_side":          False,
                        "has_shelter":        False,
                    }
                    journey_k[from_sid]    = [xfer_leg] + journey_to
                    journey_star[from_sid] = journey_k[from_sid]

                    if from_sid in origin_set:
                        results.append(
                            self._build_itinerary(journey_k[from_sid])
                        )

            journey_star.update(journey_k)
            marked = set(tau_k.keys())

        return results

    def _trips_to(self, stop_id: int, from_min: int, to_min: int,
                  service_set: set) -> list[tuple]:
        """
        Return [(dep_at_first_stop, trip_id, pos_of_stop_id_in_trip)] for trips
        that ARRIVE at stop_id between from_min and to_min.
        Used by backward RAPTOR (route_arrive).
        """
        results = []
        for dep_at_start, trip_id, pos in self.stop_departs.get(stop_id, []):
            # pos is the index of stop_id in trip_stops[trip_id]
            # dep_at_start is the departure from stop_id — but we need the arrival
            _, _, _, arr_at_stop = self.trip_stops[trip_id][pos]
            if arr_at_stop < from_min or arr_at_stop > to_min:
                continue
            svc_id = self.trip_info[trip_id][0]
            if svc_id in service_set:
                # use arr_at_stop as the "departure" key for callers
                results.append((arr_at_stop, trip_id, pos))
        return results

    # ── Itinerary builder ─────────────────────────────────────────────────────

    def _build_itinerary(self, legs: list) -> dict:
        """
        Build an itinerary dict from a sequence of leg dicts.
        Inserts implicit transfer legs between consecutive bus legs on different routes.
        Calculates wait_min on all transfer legs.
        """
        if not legs:
            return {}

        # Insert transfer legs between consecutive bus legs (same-stop transfers)
        full_legs: list[dict] = []
        prev_bus: Optional[dict] = None

        for leg in legs:
            if leg["type"] == "bus":
                if prev_bus is not None and (
                    not full_legs or full_legs[-1]["type"] != "transfer"
                ):
                    # Implicit same-stop transfer
                    wait = max(0, leg["depart_min"] - prev_bus["arrive_min"])
                    ts   = prev_bus["to_stop_id"]
                    full_legs.append({
                        "type":               "transfer",
                        "at_stop_id":         ts,
                        "at_stop_name":       self.stops.get(ts, (0, 0, ""))[2],
                        "boarding_stop_id":   leg["from_stop_id"],
                        "boarding_stop_name": self.stops.get(leg["from_stop_id"], (0, 0, ""))[2],
                        "walk_min":           0.0,
                        "wait_min":           wait,
                        "same_side":          True,
                        "has_shelter":        False,
                    })
                prev_bus = leg
            elif leg["type"] == "transfer" and prev_bus is not None:
                # Update wait_min: time from prev bus arrival + walk to next boarding
                # wait_min will be correctly set when the next bus leg is known
                pass
            full_legs.append(leg)

        # Update wait_min on walk-type transfer legs
        for i, leg in enumerate(full_legs):
            if leg["type"] == "transfer" and leg.get("walk_min", 0) > 0:
                # Find next bus leg's depart_min
                for j in range(i + 1, len(full_legs)):
                    if full_legs[j]["type"] == "bus":
                        # Walk back to find the most recent BUS leg (transfer legs
                        # don't carry arrive_min). Without this, full_legs[i-1] can
                        # land on another transfer and KeyError.
                        prev_arr = 0
                        for k in range(i - 1, -1, -1):
                            if full_legs[k]["type"] == "bus":
                                prev_arr = full_legs[k]["arrive_min"]
                                break
                        leg["wait_min"] = max(
                            0,
                            full_legs[j]["depart_min"]
                            - prev_arr
                            - math.ceil(leg["walk_min"])
                        )
                        break

        bus_legs  = [l for l in full_legs if l["type"] == "bus"]
        xfer_legs = [l for l in full_legs if l["type"] == "transfer"]

        if not bus_legs:
            return {}

        first_dep = bus_legs[0]["depart_min"]
        last_arr  = bus_legs[-1]["arrive_min"]
        xfer_walk = sum(math.ceil(l.get("walk_min", 0)) for l in xfer_legs)
        total_min = last_arr - first_dep + xfer_walk

        n_xfer    = len(xfer_legs)
        itin_type = "direct" if n_xfer == 0 else "transfer"

        return {
            "type":      itin_type,
            "legs":      full_legs,
            "total_min": max(0, total_min),
            "realtime":  False,
            "same_side": all(l.get("same_side", True) for l in xfer_legs)
                         if xfer_legs else True,
        }

    # ── Info ──────────────────────────────────────────────────────────────────

    def info(self) -> dict:
        """Return engine metadata for the /api/gtfs-info endpoint."""
        return {
            "stops":      self.stop_count,
            "trips":      self.trip_count,
            "loaded_at":  self.loaded_at,
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_engine: Optional[GTFSEngine] = None


def get_engine() -> GTFSEngine:
    """Return the singleton GTFSEngine, initializing it on first call."""
    global _engine
    if _engine is None:
        _engine = GTFSEngine()
    return _engine
