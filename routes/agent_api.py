from flask import Blueprint, request, jsonify
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# This MUST exist in routes/agent_service.py
from routes.agent_service import handle_agent_message

bp = Blueprint("agent_api", __name__)

LOG_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "chat_logs.sqlite"

def _log_chat(message: str, response: str) -> None:
    if os.environ.get("CHAT_LOG_ENABLED", "false").lower() not in ("1", "true", "yes", "on"):
        return
    try:
        LOG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(LOG_DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT NOT NULL,
                    message TEXT NOT NULL,
                    response TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO chat_logs (ts_utc, message, response) VALUES (?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), message, response),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # never break chat flow
        return

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
                "body_example": {"message": "ETA for Route 38 stop 1192"}
            }
        })

    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()
    history = payload.get("history") or payload.get("messages") or []

    if not msg:
        return jsonify({"error": "message is required"}), 400

    result = handle_agent_message(msg, history=history)
    _log_chat(msg, result.get("answer", ""))
    return jsonify(result)
