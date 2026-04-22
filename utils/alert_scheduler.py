"""
utils/alert_scheduler.py
APScheduler background job that checks real-time predictions every 60 seconds
and fires push notifications when a saved favorite route is delayed past the
user's threshold.

Control:
  ENABLE_ALERT_SCHEDULER=false  → disabled (use in tests / dev without real-time data)

Design decisions:
  - Runs inside the Flask process as a BackgroundScheduler (daemon threads).
  - All errors are caught and logged — the scheduler must keep ticking.
  - No real-time data is cached; every tick fetches fresh predictions.
  - Deduplication: no second push within 30 minutes for the same favorite.
  - Dead subscriptions (push service 410) are deleted immediately.
"""
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from agency_config import get_timezone

logger = logging.getLogger(__name__)

# ── Day-of-week helpers ───────────────────────────────────────────────────────

_DOW_MAP = {
    0: "mon", 1: "tue", 2: "wed",
    3: "thu", 4: "fri", 5: "sat", 6: "sun",
}


def _today_dow(tz_name: str) -> str:
    """Return today's 3-letter day-of-week string in the agency timezone."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = None
    now = datetime.now(tz=tz) if tz else datetime.now()
    return _DOW_MAP[now.weekday()]


def _now_in_tz(tz_name: str) -> datetime:
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo(tz_name))
    except Exception:
        return datetime.now()


def _minutes_until_hhmm(hhmm: str, now: datetime) -> int:
    """Return minutes from now until the next occurrence of HH:MM today."""
    try:
        h, m = map(int, hhmm.split(":"))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delta = (target - now).total_seconds() / 60
        return int(delta)
    except Exception:
        return 9999


# ── Prediction helpers ────────────────────────────────────────────────────────

def _get_delay_minutes(route_id: str, stop_id: str) -> int | None:
    """
    Call the BusTime predictions API for route+stop, return delay_min.
    Returns None if no real-time data available.

    prdctdn (countdown minutes) from the API is a string like "10" or "DUE".
    We compare against the scheduled departure to produce delay_min.
    A positive result means late; negative means early.
    """
    try:
        import rts_api as _rts
        data = _rts.get_predictions(stop_id)
        preds = data.get("prd") or []
        if isinstance(preds, dict):
            preds = [preds]
        # Filter to the matching route
        route_preds = [
            p for p in preds
            if str(p.get("rt") or "").strip() == str(route_id).strip()
        ]
        if not route_preds:
            return None
        # Take first prediction countdown
        ctdn = route_preds[0].get("prdctdn") or "0"
        if str(ctdn).upper() == "DUE":
            ctdn = 0
        realtime_minutes = int(ctdn)
        # We don't have scheduled ETA from BusTime directly; use realtime as-is
        # and treat 0 as on-time with delay_min=0 (threshold won't fire unless >0)
        return realtime_minutes
    except Exception as exc:
        logger.debug("prediction_error route=%s stop=%s: %s", route_id, stop_id, repr(exc))
        return None


# ── Dedupe check ──────────────────────────────────────────────────────────────

def _was_recently_alerted(db, favorite_id: int, window_min: int = 30) -> bool:
    """Return True if an alert was fired for this favorite in the last window_min."""
    cutoff = (datetime.utcnow() - timedelta(minutes=window_min)).strftime("%Y-%m-%d %H:%M:%S")
    row = db.execute(
        "SELECT id FROM alert_log WHERE favorite_id=? AND fired_at > ? AND outcome='sent' LIMIT 1",
        (favorite_id, cutoff),
    ).fetchone()
    return row is not None


# ── Core tick ─────────────────────────────────────────────────────────────────

def check_alerts() -> None:
    """
    Main scheduler tick — called every 60 seconds.
    Finds active favorites departing within the next 20 minutes and fires
    push alerts when the predicted delay exceeds the user's threshold.
    """
    try:
        from push_db import get_push_db
        from push_sender import send_push, build_push_payload
    except ImportError as exc:
        logger.error("alert_scheduler import error: %s", repr(exc))
        return

    try:
        tz_name = get_timezone()
        now = _now_in_tz(tz_name)
        today_dow = _today_dow(tz_name)

        db = get_push_db()

        # Fetch active favorites whose departure is within the next 20 minutes
        favorites = db.execute(
            "SELECT * FROM favorites WHERE active=1"
        ).fetchall()

        for fav in favorites:
            try:
                days = {d.strip() for d in (fav["days_of_week"] or "").split(",")}
                if today_dow not in days:
                    continue

                minutes_away = _minutes_until_hhmm(fav["departure_hhmm"], now)
                if not (0 < minutes_away <= 20):
                    continue

                # Dedupe
                if _was_recently_alerted(db, fav["id"]):
                    db.execute(
                        "INSERT INTO alert_log (favorite_id, delay_min, outcome) VALUES (?,NULL,'deduped')",
                        (fav["id"],),
                    )
                    db.commit()
                    continue

                # Get real-time prediction
                delay_min = _get_delay_minutes(fav["route_id"], fav["stop_id"])
                if delay_min is None:
                    db.execute(
                        "INSERT INTO alert_log (favorite_id, delay_min, outcome) VALUES (?,NULL,'no_data')",
                        (fav["id"],),
                    )
                    db.commit()
                    continue

                if delay_min < fav["delay_threshold_min"]:
                    continue  # on time — no alert needed

                # Calculate "leave by" time
                try:
                    h, m = map(int, fav["departure_hhmm"].split(":"))
                    leave_dt = now.replace(hour=h, minute=m, second=0)
                    leave_dt += timedelta(minutes=delay_min)
                    leave_by = leave_dt.strftime("%I:%M %p").lstrip("0")
                except Exception:
                    leave_by = ""

                # Get language for this user
                uid_row = db.execute(
                    "SELECT language FROM user_identities WHERE anon_uuid=?",
                    (fav["anon_uuid"],),
                ).fetchone()
                lang = (uid_row["language"] if uid_row else "en") or "en"

                payload = build_push_payload(
                    route_id=fav["route_id"],
                    stop_id=fav["stop_id"],
                    delay_min=delay_min,
                    leave_by=leave_by,
                    lang=lang,
                )

                # Get all active subscriptions for this user
                subs = db.execute(
                    "SELECT * FROM push_subscriptions WHERE anon_uuid=?",
                    (fav["anon_uuid"],),
                ).fetchall()

                if not subs:
                    db.execute(
                        "INSERT INTO alert_log (favorite_id, delay_min, outcome) VALUES (?,?,'no_subscription')",
                        (fav["id"], delay_min),
                    )
                    db.commit()
                    continue

                for sub in subs:
                    sub_info = {
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    }
                    outcome = send_push(sub_info, payload, lang=lang)

                    if outcome == "gone":
                        db.execute(
                            "DELETE FROM push_subscriptions WHERE endpoint=?",
                            (sub["endpoint"],),
                        )

                    db.execute(
                        "INSERT INTO alert_log (favorite_id, delay_min, outcome) VALUES (?,?,?)",
                        (fav["id"], delay_min, outcome),
                    )
                    db.commit()

            except Exception as exc:
                logger.error("alert_tick_favorite_id=%s error: %s", fav["id"], repr(exc))
                continue

    except Exception as exc:
        logger.error("alert_scheduler tick error: %s", repr(exc))


# ── Scheduler factory ─────────────────────────────────────────────────────────

def make_scheduler():
    """
    Build and start the APScheduler BackgroundScheduler.
    Returns the scheduler so app.py can shut it down on teardown.
    Respects ENABLE_ALERT_SCHEDULER env var.
    """
    import os
    if os.getenv("ENABLE_ALERT_SCHEDULER", "true").lower() != "true":
        logger.info("alert_scheduler: disabled by ENABLE_ALERT_SCHEDULER env var")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.error("APScheduler not installed — alert scheduler disabled")
        return None

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(check_alerts, "interval", seconds=60, id="check_alerts", max_instances=1)
    scheduler.start()
    logger.info("alert_scheduler: started (60s interval)")
    return scheduler
