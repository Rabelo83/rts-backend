import json
import re
from pathlib import Path

from flask import Blueprint, jsonify

project_status_bp = Blueprint("project_status", __name__)


def _tasks_file() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "project_tasks.json"


def _slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "task"


def _normalize_task_ids(tasks: list[dict]) -> list[dict]:
    normalized = []
    used_ids = set()
    used_codes = set()

    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue

        out = dict(task)

        # Ensure every task has a stable slug ID.
        base_id = (out.get("id") or "").strip() or _slugify(out.get("title") or f"task-{index}")
        task_id = base_id
        suffix = 2
        while task_id in used_ids:
            task_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(task_id)
        out["id"] = task_id

        # Ensure every task has a numeric code for discussion (RTS-0001, RTS-0002, ...).
        existing_code = (out.get("task_code") or "").strip().upper()
        if not re.fullmatch(r"RTS-\d{4}", existing_code) or existing_code in used_codes:
            code = f"RTS-{index:04d}"
            while code in used_codes:
                index += 1
                code = f"RTS-{index:04d}"
            out["task_code"] = code
        else:
            out["task_code"] = existing_code
        used_codes.add(out["task_code"])

        normalized.append(out)

    return normalized


@project_status_bp.route("/api/project/tasks", methods=["GET"])
def get_project_tasks():
    tasks_path = _tasks_file()
    if not tasks_path.exists():
        return jsonify({"error": "project_tasks.json not found"}), 404

    with tasks_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    payload["tasks"] = _normalize_task_ids(tasks if isinstance(tasks, list) else [])
    payload["task_id_format"] = {
        "slug_id": "task.id (kebab-case unique id)",
        "task_code": "RTS-0001 (numeric code shown in dashboard)",
    }

    return jsonify(payload)
