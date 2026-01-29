from flask import Blueprint, jsonify, request
import re
import sqlite3
from pathlib import Path

import rts_api

bustime_bp = Blueprint("bustime", __name__)

GTFS_DB_PATH = Path(__file__).resolve().parents[1] / "Backend Basics" / "db" / "rts_gtfs.sqlite"

def normalize_stop_id(s: str):
    if not s:
        return None
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return None
    if len(digits) > 4:
        digits = digits[-4:]
    return digits.zfill(4)

def digits_only(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")

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
    stop4 = normalize_stop_id(request.args.get("stop_id", ""))
    if not stop4:
        return jsonify({"error": "invalid stop_id"}), 400

    data = rts_api.get_predictions(stop4)
    preds = data.get("prd", [])
    cleaned = [{
        "route": p.get("rt"),
        "direction": p.get("rtdir"),
        "destination": p.get("des"),
        "minutes": p.get("prdctdn"),
        "vehicle_id": p.get("vid"),
        "arrival_time": p.get("prdtm"),
        "delayed": p.get("dly"),
    } for p in preds]
    return jsonify({"predictions": cleaned, "stop_id": stop4})

@bustime_bp.route("/api/vehicles")
def api_vehicles():
    raw = request.args.get("route_id", "")
    route_id = digits_only(raw)  # ✅ auto-clean
    if not route_id:
        return jsonify({"error": "route_id is required"}), 400

    data = rts_api.get_vehicles(route_id)
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
            "delayed": v.get("dly"),
            "timestamp": v.get("tmstmp"),
        })

    return jsonify({"route_id": route_id, "vehicles": cleaned})

@bustime_bp.route("/api/validate_stop")
def api_validate_stop():
    s = request.args.get("stop_id", "")
    stop4 = normalize_stop_id(s)
    return jsonify({"ok": bool(stop4), "stop_id4": stop4})

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
