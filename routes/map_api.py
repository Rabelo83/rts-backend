"""
routes/map_api.py
Backend endpoints powering the live Map tab.

GET /api/map/routes              List of all routes (id, name, color) — for chip rail
GET /api/map/route/<route_id>    Polyline shapes (per direction) + stops served by route
GET /api/map/vehicles            All active vehicles across all routes (cached 5s)

GTFS data is already loaded in-memory by GTFSEngine; the routes/route metadata and
shape coordinates come from the SQLite GTFS DB on first request and are cached for
the process lifetime (routes/shapes never change between deploys). Vehicle data is
cached 5s to amortize BusTime calls across concurrent map viewers.
"""
import logging
import sqlite3
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify

import rts_api

logger = logging.getLogger(__name__)

map_bp = Blueprint("map_api", __name__)

GTFS_DB_PATH = Path(__file__).resolve().parents[1] / "Backend Basics" / "db" / "rts_gtfs.sqlite"

# ── Static GTFS caches (process-lifetime) ────────────────────────────────────

_routes_cache: list[dict] | None = None
_route_detail_cache: dict[str, dict] = {}
_static_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(GTFS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_routes() -> list[dict]:
    global _routes_cache
    if _routes_cache is not None:
        return _routes_cache
    with _static_lock:
        if _routes_cache is not None:
            return _routes_cache
        conn = _conn()
        rows = conn.execute(
            "SELECT route_id, route_short_name, route_long_name, route_color "
            "FROM routes ORDER BY CAST(route_id AS INTEGER)"
        ).fetchall()
        conn.close()
        _routes_cache = [
            {
                "route_id":   r["route_id"],
                "short_name": r["route_short_name"] or r["route_id"],
                "long_name":  r["route_long_name"] or "",
                "color":      f"#{r['route_color']}" if r["route_color"] else "#888888",
            }
            for r in rows
        ]
        return _routes_cache


def _load_route_detail(route_id: str) -> dict | None:
    if route_id in _route_detail_cache:
        return _route_detail_cache[route_id]

    conn = _conn()
    route_row = conn.execute(
        "SELECT route_id, route_short_name, route_long_name, route_color "
        "FROM routes WHERE route_id = ?",
        (route_id,),
    ).fetchone()
    if not route_row:
        conn.close()
        return None

    # Shapes per direction. RTS routes typically have 2 shapes (IB / OB).
    shape_rows = conn.execute(
        """
        SELECT t.shape_id, t.direction_id, t.trip_headsign, COUNT(*) AS n
        FROM trips t
        WHERE t.route_id = ?
        GROUP BY t.shape_id, t.direction_id, t.trip_headsign
        ORDER BY n DESC
        """,
        (route_id,),
    ).fetchall()

    seen_dirs: set[str] = set()
    polylines: list[dict] = []
    for s in shape_rows:
        dir_id = str(s["direction_id"] or "")
        if dir_id in seen_dirs:
            continue
        seen_dirs.add(dir_id)
        pts = conn.execute(
            "SELECT shape_pt_lat, shape_pt_lon FROM shapes "
            "WHERE shape_id = ? ORDER BY CAST(shape_pt_sequence AS INTEGER)",
            (s["shape_id"],),
        ).fetchall()
        polylines.append({
            "shape_id":  s["shape_id"],
            "direction": dir_id,
            "headsign":  s["trip_headsign"] or "",
            "points":    [[float(p["shape_pt_lat"]), float(p["shape_pt_lon"])] for p in pts],
        })

    # Stops served by this route (any direction)
    stop_rows = conn.execute(
        """
        SELECT DISTINCT s.stop_id, s.stop_name, s.stop_lat, s.stop_lon
        FROM stops s
        JOIN stop_times st ON st.stop_id = s.stop_id
        JOIN trips t ON t.trip_id = st.trip_id
        WHERE t.route_id = ?
        """,
        (route_id,),
    ).fetchall()
    conn.close()

    detail = {
        "route_id":   route_row["route_id"],
        "short_name": route_row["route_short_name"] or route_row["route_id"],
        "long_name":  route_row["route_long_name"] or "",
        "color":      f"#{route_row['route_color']}" if route_row["route_color"] else "#888888",
        "shapes":     polylines,
        "stops": [
            {
                "stop_id":   s["stop_id"],
                "stop_name": s["stop_name"],
                "lat":       float(s["stop_lat"]) if s["stop_lat"] else None,
                "lon":       float(s["stop_lon"]) if s["stop_lon"] else None,
            }
            for s in stop_rows
            if s["stop_lat"] and s["stop_lon"]
        ],
    }
    _route_detail_cache[route_id] = detail
    return detail


# ── Vehicle aggregation (5s server-side cache) ───────────────────────────────

_VEHICLE_TTL_SEC = 5
_vehicle_cache: dict | None = None
_vehicle_cache_at: float = 0
_vehicle_lock = threading.Lock()


def _fetch_all_vehicles() -> list[dict]:
    """
    Fan out to BusTime in batches of 10 routes (the API's per-call limit) and merge
    the results. Each call costs one BusTime request; 27 routes = 3 calls per refresh.
    """
    routes = _load_routes()
    route_ids = [r["route_id"] for r in routes]
    cleaned: list[dict] = []
    BATCH = 10
    for i in range(0, len(route_ids), BATCH):
        batch = route_ids[i:i + BATCH]
        try:
            data = rts_api.get_vehicles(",".join(batch))
        except Exception as exc:
            logger.warning("BusTime getvehicles failed for batch %s: %s", batch, exc)
            continue
        for v in data.get("vehicle", []) or data.get("vehicles", []) or []:
            try:
                lat = float(v.get("lat"))
                lon = float(v.get("lon"))
            except (TypeError, ValueError):
                continue
            cleaned.append({
                "vehicle_id":  v.get("vid"),
                "lat":         lat,
                "lon":         lon,
                "heading":     v.get("hdg"),
                "speed":       v.get("spd"),
                "route":       v.get("rt"),
                "destination": v.get("des"),
                "delayed":     bool(v.get("dly", False)),
                "timestamp":   v.get("tmstmp"),
            })
    return cleaned


# ── Endpoints ────────────────────────────────────────────────────────────────

@map_bp.route("/api/map/routes")
def api_map_routes():
    return jsonify({"routes": _load_routes()})


@map_bp.route("/api/map/route/<route_id>")
def api_map_route_detail(route_id):
    detail = _load_route_detail(route_id)
    if not detail:
        return jsonify({"error": f"Route {route_id} not found"}), 404
    return jsonify(detail)


@map_bp.route("/api/map/vehicles")
def api_map_vehicles():
    global _vehicle_cache, _vehicle_cache_at
    now = time.monotonic()
    with _vehicle_lock:
        if _vehicle_cache is None or (now - _vehicle_cache_at) > _VEHICLE_TTL_SEC:
            _vehicle_cache = {
                "vehicles":  _fetch_all_vehicles(),
                "fetched_at": time.time(),
            }
            _vehicle_cache_at = now
        return jsonify(_vehicle_cache)
