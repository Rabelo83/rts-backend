from flask import Blueprint, request, jsonify
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from collections import deque

SESSION_MAX_AGE_SECONDS = 5 * 60  # 5 minutes
SESSION_MAX_TURNS = 12

_session_cache: dict[str, dict] = {}

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

def _session_prune(now: float) -> None:
    stale = []
    for sid, data in _session_cache.items():
        if now - data.get("ts", 0) > SESSION_MAX_AGE_SECONDS:
            stale.append(sid)
    for sid in stale:
        _session_cache.pop(sid, None)

def _session_get_history(session_id: str) -> list:
    if not session_id:
        return []
    entry = _session_cache.get(session_id)
    if not entry:
        return []
    # discard stale sessions on read
    if datetime.now(timezone.utc).timestamp() - entry.get("ts", 0) > SESSION_MAX_AGE_SECONDS:
        _session_cache.pop(session_id, None)
        return []
    return list(entry.get("turns", []))

def _session_update(session_id: str, new_turns: list) -> None:
    if not session_id:
        return
    turns = deque(_session_get_history(session_id), maxlen=SESSION_MAX_TURNS)
    for t in new_turns:
        if isinstance(t, dict) and t.get("role") and t.get("content"):
            turns.append({"role": t["role"], "content": t["content"]})
    _session_cache[session_id] = {
        "ts": datetime.now(timezone.utc).timestamp(),
        "turns": list(turns),
    }
    _session_prune(_session_cache[session_id]["ts"])

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
        return jsonify({"error": "message is required"}), 400

    if session_id:
        stored_history = _session_get_history(session_id)
        if stored_history:
            history = stored_history + [{"role": "user", "content": msg}]

    result = handle_agent_message(msg, history=history)
    if session_id:
        combined = history + [{"role": "assistant", "content": result.get("answer", "")}]
        _session_update(session_id, combined)

    _log_chat(msg, result.get("answer", ""))
    return jsonify(result)
