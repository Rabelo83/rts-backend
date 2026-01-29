import os
from pathlib import Path
from flask import Blueprint, jsonify

import web_index

health_bp = Blueprint("health", __name__)

@health_bp.route("/api/health")
def health():
    has_index = os.path.exists(web_index.INDEX_PATH)
    # Backend Basics schedule engine (optional)
    project_root = Path(__file__).resolve().parents[1]
    bb_db = project_root / "Backend Basics" / "db" / "rts_gtfs.sqlite"
    bb_layer = project_root / "Backend Basics" / "db" / "answering_layer.py"
    bb_available = bb_db.exists() and bb_layer.exists()
    return jsonify({
        "status": "ok",
        "service": "rts-backend",
        "web_index": has_index,
        "backend_basics": {
            "available": bb_available,
            "db_path": str(bb_db),
            "db_exists": bb_db.exists(),
            "answering_layer_exists": bb_layer.exists(),
        },
    })
