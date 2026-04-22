"""
routes/favorites.py
CRUD blueprint for saved route favorites.

Endpoints:
  POST   /api/favorites           → created favorite (201)
  GET    /api/favorites?anon_uuid → list
  PATCH  /api/favorites/<id>      → updated favorite
  DELETE /api/favorites/<id>      → 204
"""
import re
import sys
import logging
from pathlib import Path

from flask import Blueprint, request, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from push_db import get_push_db

logger = logging.getLogger(__name__)
favorites_bp = Blueprint("favorites", __name__)

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_DAYS_VALID = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
_HHMM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

# Fields that PATCH is allowed to modify
_PATCHABLE = {"active", "delay_threshold_min", "days_of_week", "departure_hhmm"}


def _valid_uuid(val: str) -> bool:
    return bool(val and _UUID4_RE.match(val.strip()))


def _valid_days(val: str) -> bool:
    parts = {d.strip().lower() for d in val.split(",") if d.strip()}
    return bool(parts) and parts.issubset(_DAYS_VALID)


def _row_to_dict(row) -> dict:
    return dict(row)


# ── Create ────────────────────────────────────────────────────────────────────

@favorites_bp.route("/api/favorites", methods=["POST"])
def create_favorite():
    body = request.get_json(silent=True) or {}
    anon_uuid = (body.get("anon_uuid") or "").strip()
    route_id = str(body.get("route_id") or "").strip()
    stop_id = str(body.get("stop_id") or "").strip()
    departure_hhmm = str(body.get("departure_hhmm") or "").strip()
    days_of_week = str(body.get("days_of_week") or "").strip().lower()
    threshold = int(body.get("delay_threshold_min") or 3)

    if not _valid_uuid(anon_uuid):
        return jsonify({"error": "invalid anon_uuid"}), 400
    if not route_id or not stop_id:
        return jsonify({"error": "route_id and stop_id required"}), 400
    if not _HHMM_RE.match(departure_hhmm):
        return jsonify({"error": "departure_hhmm must be HH:MM"}), 400
    if not _valid_days(days_of_week):
        return jsonify({"error": "days_of_week must be comma-separated from mon,tue,wed,thu,fri,sat,sun"}), 400
    if not (1 <= threshold <= 60):
        return jsonify({"error": "delay_threshold_min must be 1–60"}), 400

    db = get_push_db()
    try:
        # Ensure identity exists
        db.execute(
            "INSERT OR IGNORE INTO user_identities (anon_uuid) VALUES (?)",
            (anon_uuid,),
        )
        cur = db.execute(
            """
            INSERT INTO favorites
              (anon_uuid, route_id, stop_id, departure_hhmm, days_of_week, delay_threshold_min)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (anon_uuid, route_id, stop_id, departure_hhmm, days_of_week, threshold),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM favorites WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return jsonify(_row_to_dict(row)), 201
    except Exception as exc:
        logger.error("create_favorite_error: %s", repr(exc))
        return jsonify({"error": "db error"}), 500


# ── List ──────────────────────────────────────────────────────────────────────

@favorites_bp.route("/api/favorites", methods=["GET"])
def list_favorites():
    anon_uuid = (request.args.get("anon_uuid") or "").strip()
    if not _valid_uuid(anon_uuid):
        return jsonify({"error": "invalid anon_uuid"}), 400

    db = get_push_db()
    try:
        rows = db.execute(
            "SELECT * FROM favorites WHERE anon_uuid=? ORDER BY id DESC",
            (anon_uuid,),
        ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])
    except Exception as exc:
        logger.error("list_favorites_error: %s", repr(exc))
        return jsonify({"error": "db error"}), 500


# ── Patch ─────────────────────────────────────────────────────────────────────

@favorites_bp.route("/api/favorites/<int:fav_id>", methods=["PATCH"])
def patch_favorite(fav_id: int):
    body = request.get_json(silent=True) or {}
    anon_uuid = (body.get("anon_uuid") or "").strip()
    if not _valid_uuid(anon_uuid):
        return jsonify({"error": "invalid anon_uuid"}), 400

    updates = {k: v for k, v in body.items() if k in _PATCHABLE}
    if not updates:
        return jsonify({"error": "no patchable fields provided"}), 400

    # Validate individual fields if present
    if "days_of_week" in updates and not _valid_days(str(updates["days_of_week"])):
        return jsonify({"error": "invalid days_of_week"}), 400
    if "departure_hhmm" in updates and not _HHMM_RE.match(str(updates["departure_hhmm"])):
        return jsonify({"error": "departure_hhmm must be HH:MM"}), 400
    if "delay_threshold_min" in updates:
        t = int(updates["delay_threshold_min"])
        if not (1 <= t <= 60):
            return jsonify({"error": "delay_threshold_min must be 1–60"}), 400

    db = get_push_db()
    try:
        # Verify ownership
        row = db.execute(
            "SELECT id FROM favorites WHERE id=? AND anon_uuid=?",
            (fav_id, anon_uuid),
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [fav_id]
        db.execute(f"UPDATE favorites SET {set_clause} WHERE id=?", values)
        db.commit()
        updated = db.execute("SELECT * FROM favorites WHERE id=?", (fav_id,)).fetchone()
        return jsonify(_row_to_dict(updated))
    except Exception as exc:
        logger.error("patch_favorite_error: %s", repr(exc))
        return jsonify({"error": "db error"}), 500


# ── Delete ────────────────────────────────────────────────────────────────────

@favorites_bp.route("/api/favorites/<int:fav_id>", methods=["DELETE"])
def delete_favorite(fav_id: int):
    # anon_uuid may come from query string or JSON body
    anon_uuid = (request.args.get("anon_uuid") or "").strip()
    if not anon_uuid:
        body = request.get_json(silent=True, force=True) or {}
        anon_uuid = (body.get("anon_uuid") or "").strip()
    if not _valid_uuid(anon_uuid):
        return jsonify({"error": "invalid anon_uuid"}), 400

    db = get_push_db()
    try:
        row = db.execute(
            "SELECT id FROM favorites WHERE id=? AND anon_uuid=?",
            (fav_id, anon_uuid),
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        db.execute("DELETE FROM favorites WHERE id=?", (fav_id,))
        db.commit()
        return "", 204
    except Exception as exc:
        logger.error("delete_favorite_error: %s", repr(exc))
        return jsonify({"error": "db error"}), 500
