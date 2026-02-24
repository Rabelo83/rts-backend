import json
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

project_status_bp = Blueprint("project_status", __name__)


def _tasks_file() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "project_tasks.json"


def _load_payload(tasks_path: Path) -> dict:
    with tasks_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_payload(tasks_path: Path, payload: dict) -> None:
    with tasks_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


@project_status_bp.route("/api/project/tasks", methods=["GET"])
def get_project_tasks():
    tasks_path = _tasks_file()
    if not tasks_path.exists():
        return jsonify({"error": "project_tasks.json not found"}), 404

    payload = _load_payload(tasks_path)

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

    payload = _load_payload(tasks_path)
    tasks = payload.setdefault("tasks", [])

    task_id = f"manual-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    task = {
        "id": task_id,
        "title": title,
        "status": status,
        "area": area,
        "details": details or "Added manually from dashboard."
    }
    if blocker and status == "blocked":
        task["blocker"] = blocker

    tasks.append(task)
    payload["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d")
    _save_payload(tasks_path, payload)

    return jsonify({"ok": True, "task": task, "updated_at": payload["updated_at"]}), 201
