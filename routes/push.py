"""
routes/push.py
Flask blueprint for web push subscription management and identity registration.

Endpoints:
  GET  /api/push/vapid-public-key   → { "key": "<url-safe-b64>" }
  POST /api/push/subscribe          → 204
  DELETE /api/push/unsubscribe      → 204
  POST /api/identity                → { "anon_uuid": "..." }

No agency-specific strings here — all come from utils/push_sender.py or config.
"""
import re
import sys
import logging
from pathlib import Path

from flask import Blueprint, request, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from push_db import get_push_db
from push_sender import vapid_public_key_b64
from limiter import limiter

logger = logging.getLogger(__name__)
push_bp = Blueprint("push", __name__)

# UUIDv4 pattern
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _valid_uuid(val: str) -> bool:
    return bool(val and _UUID4_RE.match(val.strip()))


# ── VAPID public key ──────────────────────────────────────────────────────────

@push_bp.route("/api/push/vapid-public-key", methods=["GET"])
def get_vapid_key():
    try:
        key = vapid_public_key_b64()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"key": key})


# ── Subscribe ─────────────────────────────────────────────────────────────────

@push_bp.route("/api/push/subscribe", methods=["POST"])
@limiter.limit("10 per minute")
def subscribe():
    body = request.get_json(silent=True) or {}
    anon_uuid = (body.get("anon_uuid") or "").strip()
    sub = body.get("subscription") or {}
    endpoint = (sub.get("endpoint") or "").strip()
    keys = sub.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    ua = (request.user_agent.string or "")[:512]

    if not _valid_uuid(anon_uuid):
        return jsonify({"error": "invalid anon_uuid"}), 400
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "missing subscription fields"}), 400

    db = get_push_db()
    try:
        # Upsert identity (may already exist)
        db.execute(
            "INSERT OR IGNORE INTO user_identities (anon_uuid) VALUES (?)",
            (anon_uuid,),
        )
        # Upsert subscription by endpoint
        db.execute(
            """
            INSERT INTO push_subscriptions (anon_uuid, endpoint, p256dh, auth, user_agent)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
              p256dh=excluded.p256dh,
              auth=excluded.auth,
              user_agent=excluded.user_agent,
              last_seen=CURRENT_TIMESTAMP
            """,
            (anon_uuid, endpoint, p256dh, auth, ua),
        )
        db.commit()
    except Exception as exc:
        logger.error("subscribe_error: %s", repr(exc))
        return jsonify({"error": "db error"}), 500

    return "", 204


# ── Unsubscribe ───────────────────────────────────────────────────────────────

@push_bp.route("/api/push/unsubscribe", methods=["DELETE"])
def unsubscribe():
    body = request.get_json(silent=True) or {}
    endpoint = (body.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "missing endpoint"}), 400

    db = get_push_db()
    try:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        db.commit()
    except Exception as exc:
        logger.error("unsubscribe_error: %s", repr(exc))
        return jsonify({"error": "db error"}), 500

    return "", 204


# ── Identity registration ─────────────────────────────────────────────────────

@push_bp.route("/api/identity", methods=["POST"])
def register_identity():
    body = request.get_json(silent=True) or {}
    anon_uuid = (body.get("anon_uuid") or "").strip()
    language = (body.get("language") or "en").strip()[:8]

    if not _valid_uuid(anon_uuid):
        return jsonify({"error": "invalid anon_uuid"}), 400

    db = get_push_db()
    try:
        db.execute(
            """
            INSERT INTO user_identities (anon_uuid, language)
            VALUES (?, ?)
            ON CONFLICT(anon_uuid) DO UPDATE SET language=excluded.language
            """,
            (anon_uuid, language),
        )
        db.commit()
    except Exception as exc:
        logger.error("identity_error: %s", repr(exc))
        return jsonify({"error": "db error"}), 500

    return jsonify({"anon_uuid": anon_uuid})
