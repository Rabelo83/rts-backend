import os
from flask import Blueprint, jsonify

import web_index
from db import schedule_db

health_bp = Blueprint("health", __name__)

@health_bp.route("/")
def health():
    has_index = os.path.exists(web_index.INDEX_PATH)
    sched_info = schedule_db.db_info()
    return jsonify({
        "status": "ok",
        "service": "rts-backend",
        "web_index": has_index,
        "schedule_db": {
            "exists": bool(sched_info.get("exists")),
            "db_path": sched_info.get("db_path"),
            "tables": sched_info.get("tables", []),
        }
    })
