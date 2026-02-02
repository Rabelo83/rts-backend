import os
import sys
from pathlib import Path
from flask import Blueprint, jsonify
from datetime import datetime, timezone

import web_index

# Add utils to path
utils_path = str(Path(__file__).resolve().parents[1] / "utils")
if utils_path not in sys.path:
    sys.path.insert(0, utils_path)

from cache import prediction_cache, schedule_cache, metadata_cache
from session_manager import session_manager

health_bp = Blueprint("health", __name__)

@health_bp.route("/api/health")
def health():
    has_index = os.path.exists(web_index.INDEX_PATH)
    # Backend Basics schedule engine (optional)
    project_root = Path(__file__).resolve().parents[1]
    bb_db = project_root / "Backend Basics" / "db" / "rts_gtfs.sqlite"
    bb_layer = project_root / "Backend Basics" / "db" / "answering_layer.py"
    bb_available = bb_db.exists() and bb_layer.exists()

    # Try to ping BusTime API
    bustime_healthy = True
    try:
        import rts_api
        rts_api.get_routes()  # Quick test call
    except Exception:
        bustime_healthy = False

    # Get cache stats
    pred_stats = prediction_cache.stats()
    sched_stats = schedule_cache.stats()
    sess_stats = session_manager.stats()

    # Determine overall health
    if bustime_healthy and bb_available:
        overall_status = "healthy"
    elif bustime_healthy or bb_available:
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return jsonify({
        "status": overall_status,
        "service": "rts-backend",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "web_index": has_index,
        "bustime_api": bustime_healthy,
        "backend_basics": bb_available,
        "cache": {
            "predictions": pred_stats,
            "schedule": sched_stats,
        },
        "sessions": sess_stats,
    })
