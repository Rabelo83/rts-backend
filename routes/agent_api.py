from flask import Blueprint, request, jsonify, Response, stream_with_context
import os
import re
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
from limiter import limiter

MAX_MSG_LEN = 1000
# Allow UUID v4 session IDs only (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# ── Escalation logic ─────────────────────────────────────────────────────────
# Sources where the bot couldn't resolve the user's request (need more info).
_FAILURE_SOURCES = {
    "need_stop_or_route", "need_stop_schedule", "need_stop_after_direction",
    "need_time_frame", "schedule_stop_not_found", "clarify_route_vs_stop",
    "clarify_number",
}
# Sources where the bot successfully answered the question.
_SUCCESS_SOURCES = {
    "realtime", "schedule_next", "schedule_first", "schedule_last",
    "backend_basics_schedule", "route_day_summary", "route_discovery",
}


def _check_escalation(session_id: str, prior_ctx: dict, sources: list, lang: str) -> str:
    """
    Track consecutive unresolved turns per session.
    After 2+ failures in a row, return an escalation note (phone + website).
    Resets the counter on any successful answer.
    Returns an empty string when no escalation is needed.
    """
    source_types = {s.get("type") for s in (sources or [])}
    count = prior_ctx.get("failure_count", 0)

    if source_types & _FAILURE_SOURCES:
        count += 1
    elif source_types & _SUCCESS_SOURCES:
        count = 0
    # Neutral sources (disambiguation, direction prompts) leave count unchanged.

    session_manager.update_session(session_id, {"failure_count": count})

    if count >= 2:
        if (lang or "").lower().startswith("es"):
            return (
                "\n\n¿Necesitas más ayuda? Llama al servicio al cliente de RTS: "
                "**(352) 334-2600** (lun–vie 8 AM–5 PM) o visita go-rts.com."
            )
        return (
            "\n\nStill having trouble? Call RTS Customer Service: "
            "**(352) 334-2600** (Mon–Fri 8 AM–5 PM) or visit go-rts.com."
        )
    return ""

# This MUST exist in routes/agent_service.py
from routes.agent_service import handle_agent_message, stream_agent_message
from routes.agent_v2 import handle_message as handle_message_v2

bp = Blueprint("agent_api", __name__)

LOG_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "chat_logs.sqlite"
ANALYTICS_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "analytics.sqlite"

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
        ANALYTICS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(ANALYTICS_DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT,
                    session_id TEXT,
                    message TEXT,
                    route TEXT,
                    stop_id TEXT,
                    destination TEXT,
                    intent TEXT,
                    language TEXT,
                    needs TEXT,
                    prefer_schedule INTEGER,
                    timeframe TEXT,
                    response_time_ms INTEGER,
                    success INTEGER,
                    source_types TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO analytics (
                    ts_utc, session_id, message, route, stop_id, destination,
                    intent, language, needs, prefer_schedule, timeframe,
                    response_time_ms, success, source_types
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("ts_utc"),
                    entry.get("session_id"),
                    entry.get("message"),
                    entry.get("route"),
                    entry.get("stop_id"),
                    entry.get("destination"),
                    entry.get("intent"),
                    entry.get("language"),
                    entry.get("needs"),
                    int(bool(entry.get("prefer_schedule"))),
                    entry.get("timeframe"),
                    entry.get("response_time_ms"),
                    int(bool(entry.get("success"))),
                    json.dumps(entry.get("source_types") or []),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return

# Session management functions now use session_manager utility
# No need for manual pruning, session_manager handles it automatically

@bp.route("/api/agent", methods=["GET", "POST"])
@limiter.limit(os.getenv("RATE_LIMIT", "30 per hour"))
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
    # Strip ASCII control characters (except tab/newline which are fine in messages)
    msg = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', msg)
    history = payload.get("history") or payload.get("messages") or []
    raw_sid = (payload.get("session_id") or payload.get("session") or "").strip()
    # Only accept well-formed UUID session IDs; discard anything else
    session_id = raw_sid if _UUID_RE.match(raw_sid) else ""

    if not msg:
        return jsonify({
            "error": True,
            "error_code": ErrorCode.MISSING_PARAMETER,
            "error_message": "message parameter is required"
        }), 400

    if len(msg) > MAX_MSG_LEN:
        return jsonify({
            "error": True,
            "error_code": ErrorCode.MISSING_PARAMETER,
            "error_message": f"Message too long (max {MAX_MSG_LEN} characters)"
        }), 400

    # Handle session management
    session_data = None
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

    # Escalation: after 2+ consecutive unresolved turns, append human-contact note.
    _lang = (result.get("meta") or {}).get("language", "en")
    _prior_ctx = (session_data or {}).get("context", {})
    _esc = _check_escalation(session_id, _prior_ctx, result.get("sources", []), _lang)
    if _esc:
        result["answer"] = result.get("answer", "") + _esc

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


@bp.route("/api/agent/stream", methods=["POST"])
@limiter.limit(os.getenv("RATE_LIMIT", "30 per hour"))
def api_agent_stream():
    """Streaming SSE endpoint — same logic as /api/agent but answers arrive word-by-word."""
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()
    msg = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', msg)
    history = payload.get("history") or payload.get("messages") or []
    raw_sid = (payload.get("session_id") or payload.get("session") or "").strip()
    session_id = raw_sid if _UUID_RE.match(raw_sid) else ""

    if not msg:
        return jsonify({"error": True, "error_message": "message required"}), 400
    if len(msg) > MAX_MSG_LEN:
        return jsonify({"error": True, "error_message": f"Message too long (max {MAX_MSG_LEN})"}), 400

    stream_session_data = None
    if session_id:
        stream_session_data = session_manager.get_session(session_id)
        if stream_session_data:
            history = stream_session_data.get("history", [])
        else:
            session_id = session_manager.create_session()
    else:
        session_id = session_manager.create_session()

    start = time.perf_counter()

    def generate():
        full_answer = ""
        result_meta = {}
        result_sources = []

        try:
            for event in stream_agent_message(msg, history=history):
                if event.get("type") == "done":
                    full_answer = event.get("answer", "")
                    result_meta = event.get("meta", {})
                    result_sources = event.get("sources", [])
                    # Escalation check before finalising the session.
                    _lang = result_meta.get("language", "en")
                    _prior_ctx = (stream_session_data or {}).get("context", {})
                    _esc = _check_escalation(session_id, _prior_ctx, result_sources, _lang)
                    if _esc:
                        # Stream the escalation note as a final token, then update answer.
                        yield f"data: {json.dumps({'type': 'token', 'text': _esc})}\n\n"
                        full_answer += _esc
                        event["answer"] = full_answer
                    # Update session BEFORE yielding done so the next request sees it
                    session_manager.add_message(session_id, "user", msg)
                    session_manager.add_message(session_id, "assistant", full_answer)
                    _log_chat(msg, full_answer)
                    event["session_id"] = session_id
                yield f"data: {json.dumps(event)}\n\n"
        except Exception:
            yield f"data: {json.dumps({'type': 'error', 'text': 'Service temporarily unavailable.'})}\n\n"
            return

        # Analytics (non-critical, runs after last byte is flushed)
        duration_ms = int((time.perf_counter() - start) * 1000)
        success = result_meta.get("intent") not in ("fallback", "error")
        _log_analytics({
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "message": msg,
            "route": result_meta.get("route"),
            "stop_id": result_meta.get("stop_id"),
            "destination": result_meta.get("destination"),
            "intent": result_meta.get("intent"),
            "language": result_meta.get("language"),
            "needs": result_meta.get("needs"),
            "prefer_schedule": result_meta.get("prefer_schedule"),
            "timeframe": result_meta.get("timeframe"),
            "response_time_ms": duration_ms,
            "success": success,
            "source_types": [s.get("type") for s in result_sources if isinstance(s, dict)],
        })

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx response buffering
            "Connection": "keep-alive",
        },
    )


# ── Tool-use agent v2 ─────────────────────────────────────────────────────────

def _v2_session_setup(payload: dict) -> tuple[str, list, dict | None]:
    """Shared session setup for both v2 endpoints. Returns (session_id, history, session_data)."""
    msg = (payload.get("message") or "").strip()
    history = payload.get("history") or payload.get("messages") or []
    raw_sid = (payload.get("session_id") or payload.get("session") or "").strip()
    session_id = raw_sid if _UUID_RE.match(raw_sid) else ""

    session_data = None
    if session_id:
        session_data = session_manager.get_session(session_id)
        if session_data:
            history = session_data.get("history", [])
        else:
            session_id = session_manager.create_session()
    else:
        session_id = session_manager.create_session()

    return session_id, history, session_data


@bp.route("/api/agent/v2", methods=["POST"])
@limiter.limit(os.getenv("RATE_LIMIT", "30 per hour"))
def api_agent_v2():
    """Tool-use agent v2 — JSON (non-streaming) endpoint."""
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()
    msg = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', msg)

    if not msg:
        return jsonify({"error": True, "error_code": ErrorCode.MISSING_PARAMETER,
                        "error_message": "message parameter is required"}), 400
    if len(msg) > MAX_MSG_LEN:
        return jsonify({"error": True, "error_code": ErrorCode.MISSING_PARAMETER,
                        "error_message": f"Message too long (max {MAX_MSG_LEN} characters)"}), 400

    session_id, history, session_data = _v2_session_setup(payload)

    start = time.perf_counter()
    try:
        result = handle_message_v2(msg, history=history, session_ctx=session_data or {})
    except Exception as exc:
        return jsonify({
            "error": True,
            "error_code": ErrorCode.API_UNAVAILABLE,
            "error_message": "Agent v2 temporarily unavailable",
            "details": {"exception": str(exc)} if os.getenv("DEBUG") else {},
        }), 500

    duration_ms = int((time.perf_counter() - start) * 1000)

    session_manager.add_message(session_id, "user", msg)
    session_manager.add_message(session_id, "assistant", result.get("answer", ""))
    _log_chat(msg, result.get("answer", ""))

    meta = result.get("meta") or {}
    _log_analytics({
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "message": msg,
        "route": None,
        "stop_id": None,
        "destination": None,
        "intent": f"v2/{meta.get('model', 'unknown')}",
        "language": meta.get("language"),
        "needs": None,
        "prefer_schedule": None,
        "timeframe": None,
        "response_time_ms": duration_ms,
        "success": not meta.get("error"),
        "source_types": [f"tool:{t}" for t in (meta.get("tool_calls_made") and ["*"] or [])],
    })

    return jsonify({
        "answer": result.get("answer", ""),
        "buttons": result.get("buttons", []),
        "meta": meta,
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_time_ms": duration_ms,
    })


@bp.route("/api/agent/v2/stream", methods=["POST"])
@limiter.limit(os.getenv("RATE_LIMIT", "30 per hour"))
def api_agent_v2_stream():
    """Tool-use agent v2 — SSE streaming endpoint (same wire format as /api/agent/stream)."""
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()
    msg = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', msg)

    if not msg:
        return jsonify({"error": True, "error_message": "message required"}), 400
    if len(msg) > MAX_MSG_LEN:
        return jsonify({"error": True, "error_message": f"Message too long (max {MAX_MSG_LEN})"}), 400

    session_id, history, session_data = _v2_session_setup(payload)
    start = time.perf_counter()

    def generate():
        try:
            result = handle_message_v2(msg, history=history, session_ctx=session_data or {})
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'text': 'Agent v2 temporarily unavailable.'})}\n\n"
            return

        answer = result.get("answer", "")
        meta = result.get("meta") or {}
        buttons = result.get("buttons", [])

        # Stream answer as word-level tokens so the frontend typewriter effect works
        words = answer.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == len(words) - 1 else word + " "
            yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"

        # Update session and log BEFORE yielding done
        session_manager.add_message(session_id, "user", msg)
        session_manager.add_message(session_id, "assistant", answer)
        _log_chat(msg, answer)

        duration_ms = int((time.perf_counter() - start) * 1000)
        yield f"data: {json.dumps({'type': 'done', 'answer': answer, 'buttons': buttons, 'meta': meta, 'session_id': session_id})}\n\n"

        _log_analytics({
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "message": msg,
            "route": None,
            "stop_id": None,
            "destination": None,
            "intent": f"v2/{meta.get('model', 'unknown')}",
            "language": meta.get("language"),
            "needs": None,
            "prefer_schedule": None,
            "timeframe": None,
            "response_time_ms": duration_ms,
            "success": not meta.get("error"),
            "source_types": [f"tool:{t}" for t in (meta.get("tool_calls_made") and ["*"] or [])],
        })

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@agent_api.route("/api/agent/v2/debug-tool", methods=["POST"])
def api_agent_v2_debug_tool():
    """Temp debug: call a tool directly and return its result."""
    from routes.agent_tools import dispatch_tool
    payload = request.get_json(silent=True) or {}
    tool_name = payload.get("tool", "")
    tool_args = payload.get("args", {})
    result = dispatch_tool(tool_name, tool_args)
    return jsonify(result)
