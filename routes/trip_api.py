"""
routes/trip_api.py
Trip Planner API endpoints.

GET  /api/geocode/autocomplete?q=...          → address suggestions
POST /api/trip/plan                           → trip itineraries
"""
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from utils.geocoding import autocomplete, geocode
from utils.trip_planner import find_trips

trip_bp = Blueprint("trip_api", __name__)


@trip_bp.route("/api/geocode/autocomplete")
def geocode_autocomplete():
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify([])
    suggestions = autocomplete(q)
    return jsonify(suggestions)


@trip_bp.route("/api/trip/plan", methods=["POST"])
def plan_trip():
    data = request.get_json(silent=True) or {}

    # Accept pre-resolved coords OR address strings
    origin_lat = data.get("origin_lat")
    origin_lon = data.get("origin_lon")
    dest_lat = data.get("dest_lat")
    dest_lon = data.get("dest_lon")

    if origin_lat is None or origin_lon is None:
        origin_addr = str(data.get("origin_address") or "").strip()
        if not origin_addr:
            return jsonify({"error": "origin_address or origin_lat/lon required"}), 400
        geo = geocode(origin_addr)
        if not geo:
            return jsonify({"error": f"Could not locate: {origin_addr}"}), 422
        origin_lat, origin_lon = geo["lat"], geo["lon"]

    if dest_lat is None or dest_lon is None:
        dest_addr = str(data.get("dest_address") or "").strip()
        if not dest_addr:
            return jsonify({"error": "dest_address or dest_lat/lon required"}), 400
        geo = geocode(dest_addr)
        if not geo:
            return jsonify({"error": f"Could not locate: {dest_addr}"}), 422
        dest_lat, dest_lon = geo["lat"], geo["lon"]

    depart_after = data.get("depart_after")  # "HH:MM" or None

    target_date = None
    if data.get("date"):
        try:
            target_date = date.fromisoformat(data["date"])
        except Exception:
            pass

    result = find_trips(
        float(origin_lat), float(origin_lon),
        float(dest_lat), float(dest_lon),
        depart_after=depart_after,
        target_date=target_date,
    )
    return jsonify(result)
