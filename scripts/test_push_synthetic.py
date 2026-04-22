#!/usr/bin/env python3
"""
scripts/test_push_synthetic.py
Manual E2E helper for testing the push alert pipeline.

Injects a synthetic favorite + subscription into push.sqlite, then
calls check_alerts() directly with a mocked real-time prediction that
returns a specified delay.

Usage:
    python scripts/test_push_synthetic.py \\
        --route 20 --stop 0173 --delay 6 \\
        --endpoint "https://fcm.googleapis.com/..." \\
        --p256dh "..." --auth "..."

If --endpoint is omitted, the script only tests the scheduler logic
without actually sending a push (useful to verify dedupe / DB writes).

Requires VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY to be set if you want
a real push to land on a browser.
"""
import argparse
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "utils"))

import os
os.environ.setdefault("ENABLE_ALERT_SCHEDULER", "false")  # don't start background thread

from push_db import get_push_db, init_db
from alert_scheduler import check_alerts, _get_delay_minutes, _was_recently_alerted
from agency_config import get_timezone


def _inject_favorite_and_sub(
    db,
    route_id: str,
    stop_id: str,
    endpoint: str | None,
    p256dh: str | None,
    auth: str | None,
    delay_threshold: int,
) -> tuple[str, int]:
    """Insert a test identity, subscription, and favorite. Returns (anon_uuid, fav_id)."""
    anon_uuid = str(uuid.uuid4())

    db.execute(
        "INSERT OR IGNORE INTO user_identities(anon_uuid, language) VALUES(?,?)",
        (anon_uuid, "en"),
    )

    if endpoint and p256dh and auth:
        db.execute(
            "INSERT OR REPLACE INTO push_subscriptions(anon_uuid,endpoint,p256dh,auth) VALUES(?,?,?,?)",
            (anon_uuid, endpoint, p256dh, auth),
        )

    # Departure 10 minutes from now
    import zoneinfo
    tz = zoneinfo.ZoneInfo(get_timezone())
    soon = datetime.now(tz) + timedelta(minutes=10)
    departure = soon.strftime("%H:%M")
    today_day = ["mon","tue","wed","thu","fri","sat","sun"][soon.weekday()]

    cur = db.execute(
        """
        INSERT INTO favorites(anon_uuid, route_id, stop_id, departure_hhmm, days_of_week,
                              delay_threshold_min, active)
        VALUES (?,?,?,?,?,?,1)
        """,
        (anon_uuid, route_id, stop_id, departure, today_day, delay_threshold),
    )
    db.commit()
    return anon_uuid, cur.lastrowid


def main():
    parser = argparse.ArgumentParser(description="Synthetic push alert E2E test")
    parser.add_argument("--route",     default="20",   help="Route ID")
    parser.add_argument("--stop",      default="0173", help="Stop ID")
    parser.add_argument("--delay",     type=int, default=6, help="Simulated delay_min to inject")
    parser.add_argument("--threshold", type=int, default=3, help="Favorite alert threshold")
    parser.add_argument("--endpoint",  default=None, help="Push subscription endpoint URL")
    parser.add_argument("--p256dh",    default=None, help="Subscription p256dh key")
    parser.add_argument("--auth",      default=None, help="Subscription auth key")
    args = parser.parse_args()

    print(f"[synthetic] Initialising DB at {_ROOT}/db/push.sqlite")
    init_db()
    db = get_push_db()

    anon_uuid, fav_id = _inject_favorite_and_sub(
        db,
        route_id=args.route,
        stop_id=args.stop,
        endpoint=args.endpoint,
        p256dh=args.p256dh,
        auth=args.auth,
        delay_threshold=args.threshold,
    )
    print(f"[synthetic] Created anon_uuid={anon_uuid} favorite_id={fav_id}")

    # Patch get_predictions so we control the delay
    simulated_delay = args.delay
    print(f"[synthetic] Injecting delay = {simulated_delay} min for route {args.route} stop {args.stop}")

    import utils.alert_scheduler as sched_mod
    original = sched_mod._get_delay_minutes
    sched_mod._get_delay_minutes = lambda route_id, stop_id: simulated_delay

    try:
        check_alerts()
    finally:
        sched_mod._get_delay_minutes = original

    # Show what happened
    rows = db.execute(
        "SELECT * FROM alert_log WHERE favorite_id=? ORDER BY id DESC LIMIT 5",
        (fav_id,),
    ).fetchall()

    print(f"\n[synthetic] Alert log entries for favorite {fav_id}:")
    for row in rows:
        print(f"  id={row['id']} fired_at={row['fired_at']} delay={row['delay_min']} outcome={row['outcome']}")

    if not rows:
        print("  (no entries — check if departure window condition matched)")


if __name__ == "__main__":
    main()
