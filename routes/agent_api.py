from flask import Blueprint, request, jsonify

bp = Blueprint("agent_api", __name__)


@bp.route("/api/agent", methods=["POST"])
def api_agent():
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()

    if not msg:
        return jsonify({"error": "message is required"}), 400

    # Lazy import to avoid deploy-time import errors during refactors
    from routes import agent_service

    handler = getattr(agent_service, "handle_agent_message", None)
    if handler is None:
        # Optional fallback if you renamed it
        handler = getattr(agent_service, "handle_message", None)

    if handler is None:
        return jsonify({"error": "agent handler not found in routes/agent_service.py"}), 500

    result = handler(msg)

    # Ensure consistent response shape
    if isinstance(result, dict):
        return jsonify({
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
        })

    return jsonify({"answer": str(result), "sources": []})
