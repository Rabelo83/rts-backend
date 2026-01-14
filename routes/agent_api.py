from flask import Blueprint, request, jsonify

from routes.agent_service import handle_agent_message

bp = Blueprint("agent_api", __name__)

@bp.route("/api/agent", methods=["GET", "POST"])
def api_agent():
    # If you open /api/agent in a browser, it's a GET request.
    # Returning a helpful JSON avoids "Method Not Allowed".
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "how_to_use": {
                "method": "POST",
                "content_type": "application/json",
                "body_example": {"message": "ETA for Route 38 to Reitz Union stop 1192"}
            }
        })

    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()

    if not msg:
        return jsonify({"error": "message is required"}), 400

    result = handle_agent_message(msg)
    return jsonify(result)
