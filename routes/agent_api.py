from flask import Blueprint, jsonify, request
import traceback

from services.agent_service import answer_agent

agent_bp = Blueprint("agent", __name__)

@agent_bp.route("/api/agent", methods=["POST"])
def api_agent():
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()
    include_sources = bool(payload.get("include_sources", False))  # ✅ default OFF

    if not msg:
        return jsonify({"error": "message is required"}), 400

    try:
        result = answer_agent(msg)

        # Hide sources unless explicitly requested
        if not include_sources:
            return jsonify({"answer": result.get("answer", ""), "sources": []})

        return jsonify({"answer": result.get("answer", ""), "sources": result.get("sources", [])})

    except Exception as e:
        print("agent_error:", repr(e))
        print(traceback.format_exc())
        return jsonify({"error": "agent_failed", "detail": str(e)}), 500
