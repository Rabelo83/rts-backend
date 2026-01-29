import os
from flask import Blueprint, jsonify, request

from routes import schedule_service

schedule_bp = Blueprint("schedule", __name__)


@schedule_bp.route("/api/schedule/debug", methods=["POST"])
def schedule_debug():
    if os.environ.get("SCHEDULE_DEBUG", "false").lower() not in ("1", "true", "yes", "on"):
        return jsonify({"error": "debug_disabled"}), 403

    body = request.get_json(silent=True) or {}
    text = (body.get("question") or "").strip()
    route = (body.get("route") or "").strip()
    stop_id = (body.get("stop_id") or "").strip() or None
    stop_name = (body.get("stop_name") or "").strip() or None
    kind = (body.get("kind") or "").strip().lower() or "next"

    if not text:
        return jsonify({"error": "question is required"}), 400
    if not route:
        return jsonify({"error": "route is required"}), 400

    data = schedule_service.get_schedule(route, text, stop_id=stop_id, stop_name=stop_name, kind=kind, debug=True)
    return jsonify(data)
