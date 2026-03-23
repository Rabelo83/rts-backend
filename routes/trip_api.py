"""
routes/trip_api.py
Trip Planner API endpoints.

GET  /api/geocode/autocomplete?q=...          → address suggestions
POST /api/trip/plan                           → trip itineraries
"""
import os
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

from utils.geocoding import autocomplete, geocode
from utils.trip_planner import find_trips

trip_bp = Blueprint("trip_api", __name__)

_DATA_DIR     = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parents[1] / "data")))
_ANALYTICS_DB = _DATA_DIR / "analytics.sqlite"


def _log_trip_plan(origin_lat, origin_lon, dest_lat, dest_lon, success: bool, itinerary_count: int, duration_ms: int):
    """Log a trip plan request to analytics.sqlite — fails silently."""
    if not _ANALYTICS_DB.exists():
        return
    try:
        conn = sqlite3.connect(_ANALYTICS_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trip_plans (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc           TEXT,
                origin_lat       REAL,
                origin_lon       REAL,
                dest_lat         REAL,
                dest_lon         REAL,
                success          INTEGER,
                itinerary_count  INTEGER,
                duration_ms      INTEGER
            )
        """)
        conn.execute("""
            INSERT INTO trip_plans
                (ts_utc, origin_lat, origin_lon, dest_lat, dest_lon, success, itinerary_count, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            origin_lat, origin_lon, dest_lat, dest_lon,
            1 if success else 0,
            itinerary_count,
            duration_ms,
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


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
    arrive_by    = data.get("arrive_by")     # "HH:MM" or None

    target_date = None
    if data.get("date"):
        try:
            target_date = date.fromisoformat(data["date"])
        except Exception:
            pass

    t0 = time.monotonic()
    try:
        result = find_trips(
            float(origin_lat), float(origin_lon),
            float(dest_lat), float(dest_lon),
            depart_after=depart_after,
            arrive_by=arrive_by,
            target_date=target_date,
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Routing error: {exc}", "itineraries": []}), 500
    duration_ms = int((time.monotonic() - t0) * 1000)
    itineraries = result.get("itineraries") or []
    _log_trip_plan(
        float(origin_lat), float(origin_lon),
        float(dest_lat), float(dest_lon),
        success=len(itineraries) > 0,
        itinerary_count=len(itineraries),
        duration_ms=duration_ms,
    )
    return jsonify(result)
