import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

project_status_bp = Blueprint("project_status", __name__)


def _tasks_file() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "project_tasks.json"


def _tasks_db_file() -> Path:
    configured = os.environ.get("PROJECT_TASKS_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "data" / "project_tasks.sqlite"


def _load_payload(tasks_path: Path) -> dict:
    with tasks_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _db_conn() -> sqlite3.Connection:
    db_path = _tasks_db_file()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_tasks_manual (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            area TEXT NOT NULL,
            details TEXT NOT NULL,
            blocker TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def _load_manual_tasks() -> list[dict]:
    with _db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, status, area, details, blocker, created_at
            FROM project_tasks_manual
            ORDER BY datetime(created_at) DESC, id DESC
            """
        ).fetchall()

    tasks = []
    for row in rows:
        task = {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "area": row["area"],
            "details": row["details"],
            "source": "manual",
            "created_at": row["created_at"],
        }
        if row["blocker"]:
            task["blocker"] = row["blocker"]
        tasks.append(task)
    return tasks


@project_status_bp.route("/api/project/tasks", methods=["GET"])
def get_project_tasks():
    tasks_path = _tasks_file()
    if not tasks_path.exists():
        return jsonify({"error": "project_tasks.json not found"}), 404

    payload = _load_payload(tasks_path)
    base_tasks = payload.get("tasks", [])
    manual_tasks = _load_manual_tasks()
    payload["tasks"] = [*manual_tasks, *base_tasks]
    payload["storage"] = {
        "base_tasks": "data/project_tasks.json",
        "manual_tasks": str(_tasks_db_file()),
    }

    return jsonify(payload)


@project_status_bp.route("/api/project/tasks", methods=["POST"])
def add_project_task():
    tasks_path = _tasks_file()
    if not tasks_path.exists():
        return jsonify({"error": "project_tasks.json not found"}), 404

    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    allowed_status = {"completed", "pending", "blocked", "next"}
    status = (body.get("status") or "pending").strip().lower()
    if status not in allowed_status:
        return jsonify({"error": "invalid status"}), 400

    area = (body.get("area") or "General").strip() or "General"
    details = (body.get("details") or "").strip()
    blocker = (body.get("blocker") or "").strip()
    created_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    task_id = f"manual-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

    task = {
        "id": task_id,
        "title": title,
        "status": status,
        "area": area,
        "details": details or "Added manually from dashboard.",
        "source": "manual",
        "created_at": created_at,
    }
    if blocker and status == "blocked":
        task["blocker"] = blocker

    with _db_conn() as conn:
        conn.execute(
            """
            INSERT INTO project_tasks_manual (id, title, status, area, details, blocker, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["id"],
                task["title"],
                task["status"],
                task["area"],
                task["details"],
                task.get("blocker"),
                task["created_at"],
            ),
        )
        conn.commit()

    return jsonify(
        {
            "ok": True,
            "task": task,
            "updated_at": datetime.utcnow().strftime("%Y-%m-%d"),
            "storage": str(_tasks_db_file()),
        }
    ), 201
