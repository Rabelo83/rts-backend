"""
routes/map_api.py
Backend endpoints powering the live Map tab.

GET /api/map/routes              List of all routes (id, name, color) — for chip rail
GET /api/map/route/<route_id>    Polyline shapes (per direction) + stops served by route
GET /api/map/route/<route_id>/overview
                                  First/last/frequency route summary
GET /api/map/vehicles            All active vehicles across all routes (cached 5s)
GET /api/map/stop/<stop_id>/schedule
                                  Next scheduled departures for a stop

GTFS data is already loaded in-memory by GTFSEngine; the routes/route metadata and
shape coordinates come from the SQLite GTFS DB on first request and are cached for
the process lifetime (routes/shapes never change between deploys). Vehicle data is
cached 5s to amortize BusTime calls across concurrent map viewers.
"""
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

import rts_api
from routes import schedule_service
from routes.parsing_helpers import format_time_12h, normalize_stop_id

logger = logging.getLogger(__name__)

map_bp = Blueprint("map_api", __name__)

GTFS_DB_PATH = Path(__file__).resolve().parents[1] / "Backend Basics" / "db" / "rts_gtfs.sqlite"
_SCHEDULE_LOOKAHEAD_DAYS = 14

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


# ── Vehicle aggregation (30s server-side cache) ──────────────────────────────

_VEHICLE_TTL_SEC = 30
_vehicle_cache: dict | None = None
_vehicle_cache_at: float = 0
_vehicle_lock = threading.Lock()


class BustimeVehicleError(RuntimeError):
    """Raised when BusTime returns an explicit error payload for vehicles."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _bustime_error_message(data: dict) -> str | None:
    errors = data.get("error") if isinstance(data, dict) else None
    if not errors:
        return None
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("msg") or first.get("message") or first)
        return str(first)
    return str(errors)


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
        error_msg = _bustime_error_message(data)
        if error_msg:
            raise BustimeVehicleError(error_msg)
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


def _human_day_label(target_date, today):
    if target_date == today:
        return "Today"
    if target_date == today + timedelta(days=1):
        return "Tomorrow"
    return f"{target_date.strftime('%a, %b')} {target_date.day}"


def _find_next_stop_schedule(stop_id: str, limit: int) -> dict:
    today = datetime.now(schedule_service.TZ).date()
    last_payload = None

    for offset in range(_SCHEDULE_LOOKAHEAD_DAYS + 1):
        target_date = today + timedelta(days=offset)
        text = "now" if offset == 0 else f"{target_date.isoformat()} midnight"
        data = schedule_service.get_schedule_all_routes(text, stop_id=stop_id)
        if data.get("error"):
            return data
        last_payload = data
        rows = data.get("next_by_route") or []
        if rows:
            return {
                "stop_id": stop_id,
                "stop_name": data.get("stop"),
                "date": data.get("date"),
                "after": data.get("time"),
                "service_day_label": _human_day_label(target_date, today),
                "departures": [
                    {
                        "route": route,
                        "time": time_str,
                        "time_label": format_time_12h(time_str),
                        "headsign": headsign,
                        "is_scheduled": True,
                    }
                    for route, time_str, headsign in rows[:limit]
                ],
                "source": "gtfs_schedule",
            }

    return {
        "stop_id": stop_id,
        "stop_name": last_payload.get("stop") if last_payload else None,
        "date": last_payload.get("date") if last_payload else None,
        "after": last_payload.get("time") if last_payload else None,
        "service_day_label": None,
        "departures": [],
        "source": "gtfs_schedule",
    }


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


@map_bp.route("/api/map/route/<route_id>/overview")
def api_map_route_overview(route_id):
    route_id = str(route_id or "").strip()
    if not route_id:
        return jsonify({"error": "invalid_route_id"}), 400

    summary = schedule_service.get_route_day_summary(route_id)
    if summary is None:
        return jsonify({"error": "route_not_found", "route": route_id}), 404

    payload = {
        "route": summary["route_id"],
        "route_name": summary["route_long_name"],
        "date": summary["date_iso"],
        "day_label": summary["day_label"],
        "runs_today": summary["runs_today"],
        "directions": summary.get("directions", []),
        "schedule_by_service_type": schedule_service.get_route_first_last_by_service_type(route_id),
        "source": "gtfs_schedule",
    }
    return jsonify(payload)


@map_bp.route("/api/map/vehicles")
def api_map_vehicles():
    global _vehicle_cache, _vehicle_cache_at
    now = time.monotonic()
    with _vehicle_lock:
        if _vehicle_cache is None or (now - _vehicle_cache_at) > _VEHICLE_TTL_SEC:
            try:
                _vehicle_cache = {
                    "vehicles":  _fetch_all_vehicles(),
                    "fetched_at": time.time(),
                    "realtime_status": "ok",
                }
            except BustimeVehicleError as exc:
                logger.warning("BusTime vehicle data unavailable: %s", exc.message)
                _vehicle_cache = {
                    "vehicles": [],
                    "fetched_at": time.time(),
                    "realtime_status": "limit_exceeded" if "limit" in exc.message.lower() else "unavailable",
                    "realtime_message": exc.message,
                }
            _vehicle_cache_at = now
        return jsonify(_vehicle_cache)


@map_bp.route("/api/map/stop/<stop_id>/schedule")
def api_map_stop_schedule(stop_id):
    """Stop schedule + lightweight stop info (lat/lon) in a single response.

    The lat/lon fields make this endpoint the one-stop-shop for the map's
    stop-ID search box: caller gets coordinates to fly to AND the next
    scheduled departures in one round trip. The bottom sheet then fetches
    live predictions separately because those are real-time (no caching)
    and live in a different blueprint.
    """
    normalized = normalize_stop_id(stop_id)
    if not normalized:
        return jsonify({"error": "invalid_stop_id"}), 400

    limit_raw = request.args.get("limit", "6")
    try:
        limit = max(1, min(int(limit_raw), 12))
    except (TypeError, ValueError):
        limit = 6

    data = _find_next_stop_schedule(normalized, limit)
    if data.get("error"):
        return jsonify({"error": data["error"], "stop_id": normalized}), 404

    # Attach lat/lon from the in-memory engine so the map UI can pan to the
    # stop without an extra round trip.
    try:
        sid_int = int(normalized.lstrip("0") or "0")
    except ValueError:
        sid_int = None
    if sid_int is not None:
        from utils.gtfs_engine import get_engine
        stop_tuple = get_engine().stops.get(sid_int)
        if stop_tuple:
            lat, lon, name = stop_tuple
            data["lat"] = lat
            data["lon"] = lon
            if not data.get("stop_name"):
                data["stop_name"] = name

    return jsonify(data)


@map_bp.route("/api/map/nearby-stops")
def api_map_nearby_stops():
    """Find stops near a coordinate. Powers the 'center on me' workflow."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon query params are required"}), 400

    try:
        radius_m = max(50, min(int(request.args.get("radius_m", "500")), 3000))
    except (TypeError, ValueError):
        radius_m = 500

    try:
        limit = max(1, min(int(request.args.get("limit", "5")), 15))
    except (TypeError, ValueError):
        limit = 5

    from utils.stop_finder import find_nearest_stops
    stops = find_nearest_stops(lat, lon, radius_m=radius_m, limit=limit)
    return jsonify({
        "stops":    stops,
        "lat":      lat,
        "lon":      lon,
        "radius_m": radius_m,
    })
