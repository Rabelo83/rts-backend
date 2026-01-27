from flask import Blueprint, jsonify, request
import rts_api
from db import gtfs_db

schedule_bp = Blueprint("schedule", __name__)

@schedule_bp.route("/api/schedule/info")
def api_schedule_info():
    return jsonify(gtfs_db.db_info())

@schedule_bp.route("/api/schedule/routes")
def api_schedule_routes():
    routes = gtfs_db.list_routes()
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
    return jsonify({"stops": gtfs_db.find_stops(q, limit=limit)})

@schedule_bp.route("/api/schedule/route_stops")
def api_schedule_route_stops():
    route_id = (request.args.get("route_id") or "").strip()
    # GTFS service_ids are internal; we don't require callers to pass one.
    q = (request.args.get("q") or "").strip() or None
    limit = int(request.args.get("limit") or "200")

    if not route_id:
        return jsonify({"error": "Missing route_id"}), 400

    return jsonify({
        "route_id": route_id,
        "stops": gtfs_db.route_stops(route_id, q=q, limit=limit)
    })

@schedule_bp.route("/api/schedule/last_departure")
def api_schedule_last_departure():
    # This endpoint existed for the PDF parser. With GTFS we can expose a
    # better "next departures" API later.
    return jsonify({"error": "This endpoint is deprecated. Use the agent or a next-departures endpoint."}), 410
