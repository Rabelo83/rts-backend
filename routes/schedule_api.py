from flask import Blueprint, jsonify, request
import rts_api
from db import schedule_db

schedule_bp = Blueprint("schedule", __name__)

@schedule_bp.route("/api/schedule/info")
def api_schedule_info():
    return jsonify(schedule_db.db_info())

@schedule_bp.route("/api/schedule/routes")
def api_schedule_routes():
    routes = schedule_db.list_routes()
    # Fill route names from live data if missing
    try:
        live = rts_api.get_routes()
        live_routes = live.get("routes", [])
        name_map = {r.get("rt"): r.get("rtnm") for r in live_routes if r.get("rt")}
        for item in routes:
            if not item.get("route_name"):
                item["route_name"] = name_map.get(item.get("route_id"))
    except Exception as e:
        print("schedule_routes_name_fill_error:", repr(e))
    return jsonify({"routes": routes})

@schedule_bp.route("/api/schedule/find_stops")
def api_schedule_find_stops():
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", "25"))
    return jsonify({"stops": schedule_db.find_stops(q, limit=limit)})

@schedule_bp.route("/api/schedule/route_stops")
def api_schedule_route_stops():
    route_id = (request.args.get("route_id") or "").strip()
    service_id = (request.args.get("service_id") or "mon_fri").strip()
    q = (request.args.get("q") or "").strip() or None
    limit = int(request.args.get("limit") or "200")

    if not route_id:
        return jsonify({"error": "Missing route_id"}), 400

    return jsonify({
        "route_id": route_id,
        "service_id": service_id,
        "stops": schedule_db.route_stops(route_id, service_id=service_id, q=q, limit=limit)
    })

@schedule_bp.route("/api/schedule/last_departure")
def api_schedule_last_departure():
    route_id = (request.args.get("route_id") or "").strip()
    service_id = (request.args.get("service_id") or "").strip()
    stop_id = (request.args.get("stop_id") or "").strip()

    if not route_id or not service_id or not stop_id:
        return jsonify({"error": "Missing route_id, service_id, or stop_id"}), 400

    row = schedule_db.last_departure_any(route_id, service_id, stop_id)
    if not row:
        return jsonify({"error": "No schedule found for that route/service/stop"}), 404

    return jsonify({"route_id": route_id, "service_id": service_id, **row})
