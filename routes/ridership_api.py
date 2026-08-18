from flask import Blueprint, jsonify
from datetime import datetime, timezone
from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))

from routes.map_api import get_cached_vehicles

logger = logging.getLogger(__name__)

ridership_bp = Blueprint("ridership", __name__)

# Rider-count estimate per BusTime 'psgld' bucket (RTS-specified values).
# BusTime only reports these three occupancy bands (plus "N/A" = unknown) --
# there is no raw passenger-count field in the public BusTime API, so this is
# an estimate, not a precise headcount. N/A vehicles are excluded (contribute 0).
PSGLD_ESTIMATE = {
    "EMPTY": 5,
    "HALF_EMPTY": 18,
    "FULL": 36,
}


def _build_snapshot() -> dict:
    """
    Build the live ridership snapshot from the shared vehicle cache
    (routes/map_api.get_cached_vehicles) -- the same 30s-cached, single
    BusTime fetch that powers the Live Map, so this endpoint makes zero
    BusTime calls of its own.
    """
    vehicle_data = get_cached_vehicles()
    vehicles = vehicle_data.get("vehicles", []) or []

    breakdown = {"EMPTY": 0, "HALF_EMPTY": 0, "FULL": 0, "N/A": 0}
    per_route: dict[str, dict] = {}
    riders_estimate = 0
    buses_reporting = 0

    for v in vehicles:
        rt = v.get("route") or "unknown"
        psgld = (v.get("psgld") or "N/A").strip().upper()
        if psgld not in breakdown:
            psgld = "N/A"
        breakdown[psgld] += 1

        route_entry = per_route.setdefault(rt, {"buses": 0, "riders_estimate": 0})
        route_entry["buses"] += 1

        if psgld in PSGLD_ESTIMATE:
            est = PSGLD_ESTIMATE[psgld]
            riders_estimate += est
            route_entry["riders_estimate"] += est
            buses_reporting += 1

    return {
        "riders_estimate": riders_estimate,
        "buses_active": len(vehicles),
        "buses_reporting_load": buses_reporting,
        "breakdown": breakdown,
        "per_route": [
            {"route": rt, **data} for rt, data in sorted(per_route.items())
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "bustime",
        "realtime_status": vehicle_data.get("realtime_status", "ok"),
        "estimate_basis": "psgld occupancy band (EMPTY=5, HALF_EMPTY=18, FULL=36); N/A excluded; not a precise headcount",
    }


@ridership_bp.route("/api/ridership/live")
def api_ridership_live():
    try:
        snapshot = _build_snapshot()
    except Exception:
        return jsonify({
            "error": True,
            "error_message": "Unable to fetch live vehicle data from BusTime API",
        }), 503

    return jsonify(snapshot)


@ridership_bp.route("/api/ridership/board")
def api_ridership_board():
    """
    Full RTS Pulse board payload: right_now (live) + today/week/month/
    fiscal_year/lifetime (estimated/official, from ridership_calc) + today's
    system-wide first/last scheduled service (pure GTFS). Every section
    carries its own `status` so the frontend can label LIVE vs OFFICIAL vs
    ESTIMATED vs unavailable honestly.
    """
    try:
        right_now = _build_snapshot()
    except Exception:
        right_now = {"riders_estimate": None, "status": "unavailable"}

    try:
        import ridership_calc as rc
        today = rc.get_today_estimate()
        week = rc.get_week_estimate()
        month = rc.get_month_estimate()
        fiscal_year = rc.get_fiscal_year_estimate()
        lifetime = rc.get_lifetime_estimate()
    except Exception as exc:
        logger.warning("ridership board calc failed: %s", exc)
        today = week = month = fiscal_year = lifetime = {"riders_estimate": None, "status": "unavailable"}

    service = {"status": "unavailable"}
    try:
        from routes.agent_tools import _tool_get_system_first_last_today
        result = _tool_get_system_first_last_today()
        if result.get("status") == "ok":
            service = {
                "status": "ok",
                "starting": result["earliest_first"]["time"],
                "starting_route": result["earliest_first"]["route"],
                "ending": result["latest_last"]["time"],
                "ending_route": result["latest_last"]["route"],
                "day_label": result.get("day_label"),
            }
        elif result.get("status") == "no_service":
            service = {"status": "no_service", "day_label": result.get("day_label")}
    except Exception as exc:
        logger.warning("ridership board service-hours lookup failed: %s", exc)

    return jsonify({
        "right_now": right_now,
        "today": today,
        "this_week": week,
        "this_month": month,
        "this_year": fiscal_year,
        "lifetime": lifetime,
        "service": service,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
