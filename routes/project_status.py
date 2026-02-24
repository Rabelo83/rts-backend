import json
from pathlib import Path

from flask import Blueprint, jsonify

project_status_bp = Blueprint("project_status", __name__)


def _tasks_file() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "project_tasks.json"


@project_status_bp.route("/api/project/tasks", methods=["GET"])
def get_project_tasks():
    tasks_path = _tasks_file()
    if not tasks_path.exists():
        return jsonify({"error": "project_tasks.json not found"}), 404

    with tasks_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    return jsonify(payload)
