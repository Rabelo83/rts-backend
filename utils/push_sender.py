"""
utils/push_sender.py
Send web push notifications via pywebpush.

VAPID key loading priority:
  1. VAPID_SUBJECT env var (override)
  2. mailto:<contact.email from agency_config.yaml>
  3. fallback: 'mailto:admin@example.com'

VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY must always be env vars
(never committed to source control).

User-facing push body text is bilingual (en/es) and reads the agency
short name from agency_config — no hardcoded strings.
"""
import os
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from agency_config import get_agency_short_name, get_vapid_subject

logger = logging.getLogger(__name__)

# ── VAPID credential loader ───────────────────────────────────────────────────

def _get_vapid_claims() -> dict:
    subject = get_vapid_subject()
    return {"sub": subject}


def _vapid_private_key() -> str:
    key = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "VAPID_PRIVATE_KEY env var is not set. "
            "Run scripts/generate_vapid_keys.py and add to .env.local."
        )
    return key


def vapid_public_key_b64() -> str:
    key = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "VAPID_PUBLIC_KEY env var is not set. "
            "Run scripts/generate_vapid_keys.py and add to .env.local."
        )
    return key


# ── Push payload templates ────────────────────────────────────────────────────

def _get_templates() -> dict:
    """
    Bilingual push notification templates.
    Uses agency short name from config — no hardcoded agency strings.
    """
    short = get_agency_short_name()
    return {
        "en": {
            "title": f"{short} Bus Alert",
            "body": "Your Route {route_id} is {delay_min} min late at Stop {stop_id} — leave by {leave_by}.",
            "body_no_delay": "Your Route {route_id} is on time at Stop {stop_id}.",
        },
        "es": {
            "title": f"Alerta Bus {short}",
            "body": "Tu Ruta {route_id} llega {delay_min} min tarde a la Parada {stop_id} — sal antes de las {leave_by}.",
            "body_no_delay": "Tu Ruta {route_id} va a tiempo en la Parada {stop_id}.",
        },
    }


def build_push_payload(
    route_id: str,
    stop_id: str,
    delay_min: int,
    leave_by: str = "",
    lang: str = "en",
    chat_url: str = "/chat",
) -> dict:
    """Return a dict suitable for JSON-encoding as the push payload."""
    tmpl = _get_templates().get(lang) or _get_templates()["en"]
    body = tmpl["body"].format(
        route_id=route_id,
        stop_id=stop_id,
        delay_min=delay_min,
        leave_by=leave_by,
    )
    return {
        "title": tmpl["title"],
        "body": body,
        "url": chat_url,
        "tag": f"route-{route_id}-stop-{stop_id}",
        "route_id": route_id,
        "stop_id": stop_id,
    }


# ── Send ──────────────────────────────────────────────────────────────────────

def send_push(subscription_info: dict, payload: dict, lang: str = "en") -> str:
    """
    Send a web push notification.

    subscription_info: { "endpoint": str, "keys": { "p256dh": str, "auth": str } }
    payload: dict (will be JSON-encoded)
    Returns: "sent" | "failed" | "gone" (410 from push service → delete subscription)
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.error("pywebpush not installed — push skipped")
        return "failed"

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=_vapid_private_key(),
            vapid_claims=_get_vapid_claims(),
        )
        logger.info("push sent to endpoint %.40s", subscription_info.get("endpoint", ""))
        return "sent"
    except Exception as exc:
        # pywebpush raises WebPushException with response attribute for HTTP errors
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 410:
            logger.info("push endpoint gone (410): %.40s", subscription_info.get("endpoint", ""))
            return "gone"
        logger.warning("push failed: %s", repr(exc))
        return "failed"
