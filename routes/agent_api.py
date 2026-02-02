from flask import Blueprint, request, jsonify
import os
import sqlite3
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

# Add utils to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))

from session_manager import session_manager
from api_schemas import ErrorCode

# This MUST exist in routes/agent_service.py
from routes.agent_service import handle_agent_message

bp = Blueprint("agent_api", __name__)

LOG_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "chat_logs.sqlite"
ANALYTICS_LOG_PATH = Path(__file__).resolve().parents[1] / "data" / "analytics.log"

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

def _log_analytics(entry: dict) -> None:
    if os.environ.get("ANALYTICS_ENABLED", "true").lower() not in ("1", "true", "yes", "on"):
        return
    try:
        ANALYTICS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ANALYTICS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return

# Session management functions now use session_manager utility
# No need for manual pruning, session_manager handles it automatically

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
    session_id = (payload.get("session_id") or payload.get("session") or "").strip()

    if not msg:
        return jsonify({
            "error": True,
            "error_code": ErrorCode.MISSING_PARAMETER,
            "error_message": "message parameter is required"
        }), 400

    # Handle session management
    if session_id:
        # Try to get existing session
        session_data = session_manager.get_session(session_id)
        if session_data:
            history = session_data.get("history", [])
        else:
            # Session expired or invalid, create new one
            session_id = session_manager.create_session()
    else:
        # No session_id provided, create new session
        session_id = session_manager.create_session()

    start = time.perf_counter()
    try:
        result = handle_agent_message(msg, history=history)
    except Exception as e:
        # Catch and return structured error
        return jsonify({
            "error": True,
            "error_code": ErrorCode.API_UNAVAILABLE,
            "error_message": "Agent service temporarily unavailable",
            "details": {"exception": str(e)} if os.getenv("DEBUG") else {}
        }), 500

    duration_ms = int((time.perf_counter() - start) * 1000)

    # Update session with new message
    session_manager.add_message(session_id, "user", msg)
    session_manager.add_message(session_id, "assistant", result.get("answer", ""))

    _log_chat(msg, result.get("answer", ""))

    # Build response with all required fields (per API schema)
    response_data = {
        "answer": result.get("answer", ""),
        "meta": result.get("meta", {}),
        "sources": result.get("sources", []),
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_time_ms": duration_ms
    }
    if "buttons" in result:
        response_data["buttons"] = result.get("buttons")

    # Log analytics
    meta = result.get("meta") or {}
    sources = result.get("sources") or []
    success = meta.get("intent") not in ("fallback", "error")
    entry = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "message": msg,
        "route": meta.get("route"),
        "stop_id": meta.get("stop_id"),
        "destination": meta.get("destination"),
        "intent": meta.get("intent"),
        "language": meta.get("language"),
        "needs": meta.get("needs"),
        "prefer_schedule": meta.get("prefer_schedule"),
        "timeframe": meta.get("timeframe"),
        "response_time_ms": duration_ms,
        "success": success,
        "source_types": [s.get("type") for s in sources if isinstance(s, dict)],
    }
    _log_analytics(entry)
    return jsonify(response_data)
