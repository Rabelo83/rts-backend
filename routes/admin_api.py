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
