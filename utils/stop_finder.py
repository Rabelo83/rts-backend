"""
utils/stop_finder.py
Find bus stops near a lat/lon — delegates to GTFSEngine for spatial lookups.

Adds optional enrichment from bus_stops.geojson (street/crossroad/direction/
shelters) for same-side transfer detection and amenity display. If the geojson
is not present, routing still works — same-side detection degrades to "unknown".
"""
import json
import math
import sqlite3
from pathlib import Path

from routes.schedule_service import DB_PATH as _GTFS_DB

_GEOJSON = Path(__file__).resolve().parents[1] / "Backend Basics" / "bus_stops" / "bus_stops.geojson"
_DIRECTIONS = ("Northbound", "Southbound", "Eastbound", "Westbound")
_WALK_SPEED_MPS = 1.2
_WALK_CROSSING_SEC = 60

# In-memory enrichment cache (loaded once from geojson if available)
_geo_extra: dict[int, dict] = {}   # stop_id → {street, crossroad, direction, shelters, is_uf}
_geo_loaded = False

# Stop data cache — eliminates repeated SQLite round-trips in the transfer search
_stop_cache: dict[int, dict] = {}  # stop_id → full stop dict


# ── Geojson enrichment (optional) ─────────────────────────────────────────────

def _load_geo_extra() -> None:
    global _geo_loaded
    if _geo_loaded:
        return
    _geo_loaded = True
    if not _GEOJSON.exists():
        return
    try:
        with open(_GEOJSON, encoding="utf-8") as f:
            geojson = json.load(f)
        for feature in geojson["features"]:
            p = feature["properties"]
            if p.get("status") != "ACTIVE":
                continue
            sid = int(p["stopId"])
            name = p.get("stopName", "")
            _geo_extra[sid] = {
                "street":    p.get("street"),
                "crossroad": p.get("crossroad"),
                "direction": next((d for d in _DIRECTIONS if d in name), None),
                "shelters":  p.get("shelters", 0),
                "lighting":  p.get("lighting"),
                "is_uf":     bool(p.get("isUF")),
            }
    except Exception:
        pass


# ── GTFS connection ───────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_GTFS_DB)
    c.row_factory = sqlite3.Row
    return c


# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((phi2 - phi1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def walk_minutes(dist_m: float) -> float:
    return round(dist_m / (_WALK_SPEED_MPS * 60), 1)


# ── Public API ────────────────────────────────────────────────────────────────

def ensure_stops_db() -> None:
    """Load geojson enrichment and warm the GTFSEngine singleton."""
    _load_geo_extra()
    try:
        from utils.gtfs_engine import get_engine
        get_engine()   # initialises singleton (no-op if already done)
    except Exception:
        pass


def find_nearest_stops(lat: float, lon: float,
                       radius_m: int = 500, limit: int = 5,
                       service_ids: list[str] | None = None) -> list[dict]:
    """
    Return nearest active stops within radius_m, sorted by walking distance.
    Delegates spatial lookup to GTFSEngine; adds geojson enrichment if available.
    """
    _load_geo_extra()
    try:
        from utils.gtfs_engine import get_engine
        engine = get_engine()
        svc_tuple = tuple(service_ids) if service_ids else None
        raw = engine.find_stops_near(lat, lon, radius_m, svc_tuple)
    except Exception:
        # Fallback to legacy SQL if engine unavailable
        return _find_nearest_stops_sql(lat, lon, radius_m, limit, service_ids)

    results = []
    for s in raw[:limit]:
        extra = _geo_extra.get(s["stop_id"], {})
        results.append({
            "stop_id":    s["stop_id"],
            "stop_name":  s["stop_name"],
            "lat":        s["lat"],
            "lon":        s["lon"],
            "street":     extra.get("street"),
            "crossroad":  extra.get("crossroad"),
            "direction":  extra.get("direction"),
            "distance_m": s["distance_m"],
            "walk_min":   s["walk_min"],
            "has_shelter": (extra.get("shelters") or 0) > 0,
            "is_uf":      extra.get("is_uf", False),
        })
        _stop_cache[s["stop_id"]] = results[-1]
    return results


def _find_nearest_stops_sql(lat: float, lon: float,
                             radius_m: int, limit: int,
                             service_ids: list[str] | None) -> list[dict]:
    """Legacy SQL fallback — used only if GTFSEngine fails to load."""
    deg  = 1 / 111_000
    dlat = radius_m * deg
    dlon = radius_m * deg / max(math.cos(math.radians(lat)), 0.001)
    c    = _conn()
    if service_ids:
        s_ph = ",".join("?" * len(service_ids))
        rows = c.execute(f"""
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
    else:
        rows = c.execute("""
            SELECT DISTINCT CAST(s.stop_id AS INTEGER) AS stop_id,
                   s.stop_name,
                   CAST(s.stop_lat AS REAL) AS lat,
                   CAST(s.stop_lon AS REAL) AS lon
            FROM   stops s
            WHERE  CAST(s.stop_lat AS REAL) BETWEEN ? AND ?
              AND  CAST(s.stop_lon AS REAL) BETWEEN ? AND ?
              AND  EXISTS (
                  SELECT 1 FROM stop_times st
                  WHERE CAST(st.stop_id AS INTEGER) = CAST(s.stop_id AS INTEGER)
                  LIMIT 1
              )
        """, (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
    c.close()
    results = []
    for r in rows:
        dist  = haversine_m(lat, lon, r["lat"], r["lon"])
        if dist <= radius_m:
            extra = _geo_extra.get(r["stop_id"], {})
            results.append({
                "stop_id":    r["stop_id"],
                "stop_name":  r["stop_name"],
                "lat":        r["lat"],
                "lon":        r["lon"],
                "street":     extra.get("street"),
                "crossroad":  extra.get("crossroad"),
                "direction":  extra.get("direction"),
                "distance_m": round(dist),
                "walk_min":   walk_minutes(dist),
                "has_shelter": (extra.get("shelters") or 0) > 0,
                "is_uf":      extra.get("is_uf", False),
            })
    sorted_results = sorted(results, key=lambda x: x["distance_m"])[:limit]
    for item in sorted_results:
        _stop_cache[item["stop_id"]] = item
    return sorted_results


def get_stop_by_id(stop_id: int) -> dict | None:
    """Look up a single stop by ID (cached; delegates to GTFSEngine)."""
    if stop_id in _stop_cache:
        return _stop_cache[stop_id]
    _load_geo_extra()
    try:
        from utils.gtfs_engine import get_engine
        engine = get_engine()
        if stop_id not in engine.stops:
            return None
        slat, slon, sname = engine.stops[stop_id]
        extra  = _geo_extra.get(stop_id, {})
        result = {
            "stop_id":   stop_id,
            "stop_name": sname,
            "lat":       slat,
            "lon":       slon,
            "street":    extra.get("street"),
            "crossroad": extra.get("crossroad"),
            "direction": extra.get("direction"),
            "shelters":  extra.get("shelters", 0),
            "is_uf":     extra.get("is_uf", False),
        }
        _stop_cache[stop_id] = result
        return result
    except Exception:
        pass

    # SQL fallback
    c = _conn()
    row = c.execute(
        "SELECT CAST(stop_id AS INTEGER) AS stop_id, stop_name, "
        "CAST(stop_lat AS REAL) AS lat, CAST(stop_lon AS REAL) AS lon "
        "FROM stops WHERE CAST(stop_id AS INTEGER) = ?", (stop_id,)
    ).fetchone()
    c.close()
    if not row:
        return None
    extra  = _geo_extra.get(stop_id, {})
    result = {
        "stop_id":   row["stop_id"],
        "stop_name": row["stop_name"],
        "lat":       row["lat"],
        "lon":       row["lon"],
        "street":    extra.get("street"),
        "crossroad": extra.get("crossroad"),
        "direction": extra.get("direction"),
        "shelters":  extra.get("shelters", 0),
        "is_uf":     extra.get("is_uf", False),
    }
    _stop_cache[stop_id] = result
    return result


def same_side_penalty_sec(stop_a_id: int, stop_b_id: int) -> int:
    """
    Return crossing penalty in seconds between two transfer stops.
    Requires geojson enrichment — returns 0 (no penalty) if not available.
    """
    if stop_a_id == stop_b_id:
        return 0
    a = _geo_extra.get(stop_a_id)
    b = _geo_extra.get(stop_b_id)
    if not a or not b:
        return 0
    if (a.get("crossroad") and a["crossroad"] == b.get("crossroad")
            and a.get("direction") and b.get("direction")
            and a["direction"] != b["direction"]):
        return _WALK_CROSSING_SEC
    return 0
