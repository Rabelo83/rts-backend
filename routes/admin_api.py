"""
Admin + Dashboard metrics API
/api/dashboard/metrics  — live stats for dashboard (public)
/api/admin/analytics/export  — full analytics dump (PIN-protected)
"""
import os
import re
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, request

admin_bp = Blueprint("admin_api", __name__)

_DATA_DIR     = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parents[1] / "data")))
_ANALYTICS_DB = _DATA_DIR / "analytics.sqlite"
_LOG_PATH     = Path(__file__).resolve().parents[1] / "PROJECT_LOG.md"
_TASKS_PATH   = Path(__file__).resolve().parents[1] / "TASKS.md"
_QA_HISTORY   = Path(__file__).resolve().parents[1] / "tests" / "qa_history.sqlite"


def _get_qa_summary() -> dict:
    """Read latest QA run stats from qa_history.sqlite (populated by qa_report.py)."""
    if not _QA_HISTORY.exists():
        return {}
    try:
        conn = sqlite3.connect(_QA_HISTORY)
        result = {}
        for rtype in ("scenario", "replay"):
            row = conn.execute(
                "SELECT total, passed, failed, pass_pct, judged_by, run_at "
                "FROM runs WHERE run_type=? ORDER BY run_at DESC LIMIT 1",
                (rtype,)
            ).fetchone()
            if row:
                result[rtype] = {
                    "total": row[0], "passed": row[1], "failed": row[2],
                    "pass_pct": row[3], "judged_by": row[4], "run_at": row[5],
                }
        trend = conn.execute(
            "SELECT pass_pct FROM runs WHERE run_type='scenario' ORDER BY run_at DESC LIMIT 5"
        ).fetchall()
        result["scenario_trend"] = [r[0] for r in reversed(trend)]
        conn.close()
        return result
    except Exception:
        return {}


def _analytics_conn():
    if not _ANALYTICS_DB.exists():
        return None
    conn = sqlite3.connect(_ANALYTICS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_feedback_table(conn) -> bool:
    """Create feedback table if it doesn't exist. Returns True on success."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc        TEXT,
                session_id    TEXT,
                message_index INTEGER,
                rating        INTEGER,
                user_message  TEXT,
                answer_preview TEXT
            )
        """)
        conn.commit()
        return True
    except Exception:
        return False


def _get_trip_stats(conn, today_start: str, week_start: str) -> dict:
    """Return trip planner stats from trip_plans table."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trip_plans (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc           TEXT,
                origin_lat       REAL,
                origin_lon       REAL,
                dest_lat         REAL,
                dest_lon         REAL,
                success          INTEGER,
                itinerary_count  INTEGER,
                duration_ms      INTEGER
            )
        """)
        row = conn.execute("""
            SELECT
                SUM(CASE WHEN ts_utc >= ? THEN 1 ELSE 0 END)           AS today,
                COUNT(*)                                                 AS week,
                AVG(CASE WHEN success = 1 THEN 100.0 ELSE 0.0 END)     AS success_pct
            FROM trip_plans
            WHERE ts_utc >= ?
        """, (today_start, week_start)).fetchone()
        if row:
            return {
                "trips_today":       int(row[0] or 0),
                "trips_week":        int(row[1] or 0),
                "trip_success_rate": round(float(row[2] or 0), 1),
            }
    except Exception:
        pass
    return {"trips_today": 0, "trips_week": 0, "trip_success_rate": 0.0}


def _get_satisfaction(conn, week_start: str) -> float | None:
    """Return 7-day satisfaction % from feedback table, or None if no data."""
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) AS positive
            FROM feedback WHERE ts_utc >= ?
        """, (week_start,)).fetchone()
        if row and row["total"] > 0:
            return round(float(row["positive"]) / row["total"] * 100, 1)
    except Exception:
        pass
    return None


def _parse_recent_log(n: int = 5) -> list:
    """Parse last n entries from PROJECT_LOG.md."""
    if not _LOG_PATH.exists():
        return []
    text = _LOG_PATH.read_text(encoding="utf-8")
    sections = re.split(r"\n### (\d{4}-\d{2}-\d{2})\n", text)
    entries = []
    i = 1
    while i + 1 < len(sections):
        date = sections[i]
        content = sections[i + 1].strip()
        type_m = re.search(r"-\s*Type:\s*`([^`]+)`", content)
        sum_m = re.search(r"-\s*Summary:\s*(.+?)(?:\n-\s|\Z)", content, re.DOTALL)
        entry_type = type_m.group(1).split(",")[0].strip() if type_m else "update"
        summary = sum_m.group(1).strip().replace("\n", " ") if sum_m else content[:120]
        entries.append({"date": date, "type": entry_type, "summary": summary[:220]})
        i += 2
    # Most recent first
    entries.reverse()
    return entries[:n]


@admin_bp.route("/api/dashboard/metrics")
def dashboard_metrics():
    """Live stats for the dashboard — queries, sessions, health, recent log."""
    queries_today = 0
    queries_week = 0
    success_rate = 0.0
    avg_response_ms = 0
    satisfaction_pct = None
    trip_stats: dict = {}

    conn = _analytics_conn()
    if conn:
        try:
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            week_start = (now - timedelta(days=7)).isoformat()

            row = conn.execute("""
                SELECT
                    COUNT(*) AS total_week,
                    SUM(CASE WHEN ts_utc >= ? THEN 1 ELSE 0 END) AS today,
                    AVG(CASE WHEN success = 1 THEN 100.0 ELSE 0.0 END) AS success_pct,
                    AVG(response_time_ms) AS avg_ms
                FROM analytics
                WHERE ts_utc >= ?
            """, (today_start, week_start)).fetchone()

            if row:
                queries_today = int(row["today"] or 0)
                queries_week = int(row["total_week"] or 0)
                success_rate = round(float(row["success_pct"] or 0), 1)
                avg_response_ms = int(row["avg_ms"] or 0)

            _ensure_feedback_table(conn)
            satisfaction_pct = _get_satisfaction(conn, week_start)
            trip_stats = _get_trip_stats(conn, today_start, week_start)
        except Exception:
            pass
        finally:
            conn.close()

    # Health checks
    gtfs_ok = False
    try:
        from routes.schedule_service import connect_db
        c = connect_db()
        c.execute("SELECT 1")
        c.close()
        gtfs_ok = True
    except Exception:
        pass

    claude_ok = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    bustime_ok = False
    try:
        import rts_api
        rts_api.get_routes()
        bustime_ok = True
    except Exception:
        pass

    from utils.session_manager import session_manager
    sess = session_manager.stats()

    return jsonify({
        "queries_today": queries_today,
        "queries_week": queries_week,
        "success_rate": success_rate,
        "avg_response_ms": avg_response_ms,
        "satisfaction_pct": satisfaction_pct,
        "trips_today": trip_stats.get("trips_today", 0),
        "trips_week": trip_stats.get("trips_week", 0),
        "trip_success_rate": trip_stats.get("trip_success_rate", 0.0),
        "active_sessions": sess.get("active_sessions", 0),
        "health": {
            "claude_api": claude_ok,
            "bustime_api": bustime_ok,
            "gtfs_db": gtfs_ok,
            "session_store": True,
        },
        "recent_log": _parse_recent_log(6),
        "qa": _get_qa_summary(),
    })


@admin_bp.route("/api/admin/analytics/export")
def export_analytics():
    """Full analytics export — PIN protected via ?pin= or X-Dashboard-Pin header."""
    expected_pin = os.environ.get("DASHBOARD_PIN", "")
    if expected_pin:
        provided = request.args.get("pin", "") or request.headers.get("X-Dashboard-Pin", "")
        if provided != expected_pin:
            return jsonify({"error": "unauthorized"}), 401

    conn = _analytics_conn()
    if not conn:
        return jsonify({
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": 0,
            "rows": [],
        })
    try:
        rows = conn.execute("SELECT * FROM analytics ORDER BY id").fetchall()
        return jsonify({
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": len(rows),
            "rows": [dict(r) for r in rows],
        })
    finally:
        conn.close()


@admin_bp.route("/api/project/log")
def project_log():
    """Return PROJECT_LOG.md as raw markdown text."""
    if not _LOG_PATH.exists():
        return jsonify({"content": "_PROJECT_LOG.md not found._"})
    return jsonify({"content": _LOG_PATH.read_text(encoding="utf-8")})


@admin_bp.route("/api/project/tasks-md")
def project_tasks_md():
    """Return TASKS.md as raw markdown text."""
    if not _TASKS_PATH.exists():
        return jsonify({"content": "_TASKS.md not found._"})
    return jsonify({"content": _TASKS_PATH.read_text(encoding="utf-8")})


@admin_bp.route("/api/feedback", methods=["POST"])
def submit_feedback():
    """Store a thumbs-up/down rating for a bot response."""
    data = request.get_json(silent=True) or {}
    rating = data.get("rating")
    if rating not in (1, -1):
        return jsonify({"error": "rating must be 1 or -1"}), 400

    session_id    = str(data.get("session_id") or "")[:64]
    message_index = int(data.get("message_index") or 0)
    user_message  = str(data.get("user_message") or "")[:200]
    answer_preview = str(data.get("answer_preview") or "")[:200]

    conn = _analytics_conn()
    if not conn:
        return jsonify({"status": "ok"})   # no DB yet — fail silently
    try:
        _ensure_feedback_table(conn)
        conn.execute("""
            INSERT INTO feedback
                (ts_utc, session_id, message_index, rating, user_message, answer_preview)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            session_id, message_index, rating, user_message, answer_preview,
        ))
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception:
        return jsonify({"status": "ok"})   # fail silently — never break chat
    finally:
        conn.close()


@admin_bp.route("/api/admin/push-stats")
def push_stats():
    """Active push subscriptions, favorites, and 24h alert counts."""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "utils"))
        from push_db import get_push_db
        db = get_push_db()
        active_subs = db.execute(
            "SELECT COUNT(*) FROM push_subscriptions"
        ).fetchone()[0]
        active_favs = db.execute(
            "SELECT COUNT(*) FROM favorites WHERE active=1"
        ).fetchone()[0]
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        alerts_24h = db.execute(
            "SELECT COUNT(*) FROM alert_log WHERE fired_at > ? AND outcome='sent'",
            (cutoff,),
        ).fetchone()[0]
        return jsonify({
            "active_subscriptions": active_subs,
            "active_favorites": active_favs,
            "alerts_sent_24h": alerts_24h,
        })
    except Exception as exc:
        return jsonify({"active_subscriptions": 0, "active_favorites": 0, "alerts_sent_24h": 0, "error": str(exc)})
