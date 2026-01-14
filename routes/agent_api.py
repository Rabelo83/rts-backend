from flask import Blueprint, jsonify, request
from routes.agent_service import handle_agent_message

bp = Blueprint("agent", __name__)

@bp.route("/api/agent", methods=["POST"])
def api_agent():
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()

    if not msg:
        return jsonify({"error": "message is required"}), 400

    result = handle_agent_message(msg)
    return jsonify(result)
