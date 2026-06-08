from flask import Blueprint, jsonify, request
import sqlite3
from pathlib import Path
import sys

# Add utils to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))

import rts_api
from validation import normalize_stop_id, normalize_route_id, validate_stop_id
from api_schemas import ErrorCode

bustime_bp = Blueprint("bustime", __name__)

GTFS_DB_PATH = Path(__file__).resolve().parents[1] / "Backend Basics" / "db" / "rts_gtfs.sqlite"

def _stop_exists_in_gtfs(stop_id_padded: str) -> bool:
    if not stop_id_padded or not GTFS_DB_PATH.exists():
        return True
    try:
        conn = sqlite3.connect(GTFS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT 1 FROM stops WHERE stop_id_padded = ? LIMIT 1",
                (stop_id_padded,),
            ).fetchone()
            return bool(row)
        finally:
            conn.close()
    except Exception:
        return True

def _gtfs_direction_id(direction_id: str) -> int | None:
    d = (direction_id or "").strip().lower()
    if d in ("inbound", "ib", "in"):
        return 1
    if d in ("outbound", "ob", "out"):
        return 0
    return None

def _direction_headsign(route_id: str, direction_id: str) -> str | None:
    if not route_id or not GTFS_DB_PATH.exists():
        return None
    dir_id = _gtfs_direction_id(direction_id)
    if dir_id is None:
        return None
    try:
        conn = sqlite3.connect(GTFS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT t.trip_headsign
                FROM trips t
                JOIN routes r ON r.route_id = t.route_id
                WHERE r.route_short_name = ?
                  AND t.direction_id = ?
                  AND t.trip_headsign IS NOT NULL
                LIMIT 1
                """,
                (route_id, dir_id),
            ).fetchone()
            return (row["trip_headsign"] if row else None) or None
        finally:
            conn.close()
    except Exception:
        return None

def order_stops_by_gtfs(route_id: str, direction_hint: str, stops: list[dict]) -> list[dict]:
    if not route_id or not stops or not GTFS_DB_PATH.exists():
        return stops
    try:
        conn = sqlite3.connect(GTFS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            # Try to find a trip matching the direction/headsign.
            trip_row = None
            if direction_hint:
                trip_row = conn.execute(
                    """
                    SELECT t.trip_id
                    FROM trips t
                    JOIN routes r ON r.route_id = t.route_id
                    WHERE r.route_short_name = ?
                      AND t.trip_headsign LIKE ?
                    LIMIT 1
                    """,
                    (route_id, f"%{direction_hint}%"),
                ).fetchone()

            if not trip_row:
                trip_row = conn.execute(
                    """
                    SELECT t.trip_id
                    FROM trips t
                    JOIN routes r ON r.route_id = t.route_id
                    WHERE r.route_short_name = ?
                    LIMIT 1
                    """,
                    (route_id,),
                ).fetchone()

            if not trip_row:
                return stops

            trip_id = trip_row["trip_id"]
            rows = conn.execute(
                """
                SELECT s.stop_id_padded AS stop_id_padded
                FROM stop_times st
                JOIN stops s ON s.stop_id = st.stop_id
                WHERE st.trip_id = ?
                ORDER BY st.stop_sequence
                """,
                (trip_id,),
            ).fetchall()

            order = {row["stop_id_padded"]: i for i, row in enumerate(rows)}
            if not order:
                return stops

            def key_fn(s):
                sid = normalize_stop_id(s.get("id"))
                return order.get(sid, 999999)

            return sorted(stops, key=key_fn)
        finally:
            conn.close()
    except Exception:
        return stops

@bustime_bp.route("/api/routes")
def api_routes():
    data = rts_api.get_routes()
    routes_raw = data.get("routes", [])
    cleaned = [{"id": r.get("rt"), "name": r.get("rtnm"), "color": r.get("rtclr")} for r in routes_raw]
    return jsonify({"routes": cleaned})

@bustime_bp.route("/api/directions")
def api_directions():
    route_id = request.args.get("route_id", "")
    data = rts_api.get_directions(route_id)
    dirs_raw = data.get("directions", [])
    cleaned = []
    for d in dirs_raw:
        dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d
        dir_name = d.get("name") or d.get("dir") or d.get("dirName") or d.get("dirname") or dir_id
        headsign = _direction_headsign(route_id, dir_id)
        if headsign and "to" not in dir_name.lower():
            dir_name = f"{dir_name} - to {headsign}"
        cleaned.append({"id": dir_id, "name": dir_name})
    return jsonify({"directions": cleaned})

@bustime_bp.route("/api/stops")
def api_stops():
    route_id = request.args.get("route_id", "")
    direction_id = request.args.get("direction_id", "")
    data = rts_api.get_stops(route_id, direction_id)
    stops_raw = data.get("stops", [])
    cleaned = [{
        "id": s.get("stpid"),
        "name": s.get("stpnm"),
        "lat": s.get("lat"),
        "lon": s.get("lon")
    } for s in stops_raw]
    cleaned = order_stops_by_gtfs(route_id, direction_id, cleaned)
    return jsonify({"stops": cleaned})

@bustime_bp.route("/api/predictions")
def api_predictions():
    raw_stop_id = request.args.get("stop_id", "")

    # Validate stop ID using shared utility
    validation = validate_stop_id(raw_stop_id)
    if not validation["valid"]:
        return jsonify({
            "error": True,
            "error_code": ErrorCode.INVALID_STOP_ID,
            "error_message": validation["error_message"],
            "details": {"provided": raw_stop_id}
        }), 400

    stop4 = validation["normalized"]

    if not _stop_exists_in_gtfs(stop4):
        return jsonify({
            "error": True,
            "error_code": ErrorCode.STOP_NOT_FOUND,
            "error_message": f"Stop ID {stop4} not found",
            "details": {"stop_id": stop4}
        }), 404

    try:
        data = rts_api.get_predictions(stop4)
    except Exception as e:
        return jsonify({
            "error": True,
            "error_code": ErrorCode.API_UNAVAILABLE,
            "error_message": "Unable to fetch predictions from BusTime API",
            "details": {"stop_id": stop4}
        }), 503

    preds = data.get("prd", [])

    # Enhanced prediction data with delay indicator
    cleaned = [{
        "route": p.get("rt"),
        "direction": p.get("rtdir"),
        "destination": p.get("des"),
        "minutes": p.get("prdctdn"),
        "vehicle_id": p.get("vid"),
        "arrival_time": p.get("prdtm"),
        "delayed": p.get("dly", False),  # Include delay status
        "is_scheduled": False  # Real-time data
    } for p in preds]

    return jsonify({
        "predictions": cleaned,
        "stop_id": stop4,
        "timestamp": data.get("tmstmp", ""),
        "source": "bustime",
        "cached": False
    })

@bustime_bp.route("/api/vehicles")
def api_vehicles():
    raw = request.args.get("route_id", "")
    route_id = normalize_route_id(raw)
    if not route_id:
        return jsonify({
            "error": True,
            "error_code": ErrorCode.INVALID_ROUTE_ID,
            "error_message": "route_id is required and must contain digits",
            "details": {"provided": raw}
        }), 400

    try:
        data = rts_api.get_vehicles(route_id)
    except Exception as e:
        return jsonify({
            "error": True,
            "error_code": ErrorCode.API_UNAVAILABLE,
            "error_message": "Unable to fetch vehicle data from BusTime API",
            "details": {"route_id": route_id}
        }), 503

    vehicles_raw = data.get("vehicle", []) or data.get("vehicles", []) or []

    cleaned = []
    for v in vehicles_raw:
        cleaned.append({
            "vehicle_id": v.get("vid"),
            "lat": v.get("lat"),
            "lon": v.get("lon"),
            "heading": v.get("hdg"),
            "speed": v.get("spd"),
            "route": v.get("rt"),
            "destination": v.get("des"),
            "delayed": v.get("dly", False),
            "timestamp": v.get("tmstmp"),
        })

    return jsonify({"route_id": route_id, "vehicles": cleaned})

@bustime_bp.route("/api/alerts")
def api_alerts():
    route_id = request.args.get("route_id") or None
    try:
        data = rts_api.get_service_advisories(route_id)
    except Exception:
        return jsonify({"alerts": [], "count": 0, "source": "bustime", "error": True}), 503

    advisories_raw = data.get("sb", []) or []
    cleaned = []
    for a in advisories_raw:
        routes = [s.get("rt") for s in (a.get("srvc") or []) if s.get("rt")]
        cleaned.append({
            "name":     a.get("nm", ""),
            "subject":  a.get("sbj", ""),
            "detail":   a.get("dtl", ""),
            "brief":    a.get("brf", ""),
            "priority": a.get("prty", ""),
            "routes":   routes,
            "starts":   a.get("beg", ""),
            "ends":     a.get("end", ""),
        })

    return jsonify({"alerts": cleaned, "count": len(cleaned), "source": "bustime"})


@bustime_bp.route("/api/validate_stop")
def api_validate_stop():
    s = request.args.get("stop_id", "")
    validation = validate_stop_id(s)
    return jsonify({
        "valid": validation["valid"],
        "normalized": validation["normalized"],
        "error_code": validation["error_code"],
        "error_message": validation["error_message"]
    })

@bustime_bp.route("/api/stops_anydir")
def api_stops_anydir():
    route_id = request.args.get("route_id", "")
    dirs = rts_api.get_directions(route_id).get("directions", [])
    dir_ids = [(d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d) for d in dirs]

    for d in dir_ids:
        st = rts_api.get_stops(route_id, d).get("stops", [])
        if st:
            cleaned = [{"id": s.get("stpid"), "name": s.get("stpnm"), "lat": s.get("lat"), "lon": s.get("lon")} for s in st]
            return jsonify({"route_id": route_id, "direction": d, "stops": cleaned})

    return jsonify({"route_id": route_id, "direction": None, "stops": []})
