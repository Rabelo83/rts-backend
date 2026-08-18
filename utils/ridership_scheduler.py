"""
utils/ridership_scheduler.py
APScheduler background job that samples live system-wide ridership every few
minutes and records it via ridership_calc.record_sample(), building up each
day's "Today" estimate over the course of the day.

Control:
  ENABLE_RIDERSHIP_SCHEDULER=false  -> disabled (use in tests / local dev)

Design decisions (mirrors utils/alert_scheduler.py):
  - Runs inside the Flask process as a BackgroundScheduler (daemon thread).
  - All errors are caught and logged -- the scheduler must keep ticking.
  - Reads from the same shared 30s vehicle cache as the Live Map and Pulse
    board (routes/map_api.get_cached_vehicles) -- this job makes ZERO extra
    BusTime calls of its own.
"""
import logging

logger = logging.getLogger(__name__)

_SAMPLE_INTERVAL_SECONDS = 300  # 5 minutes


def sample_tick() -> None:
    """One scheduler tick: read the shared live snapshot, record a sample."""
    try:
        from routes.ridership_api import PSGLD_ESTIMATE
        from routes.map_api import get_cached_vehicles
        from ridership_calc import record_sample

        vehicles = get_cached_vehicles().get("vehicles", []) or []
        riders_estimate = 0
        for v in vehicles:
            psgld = (v.get("psgld") or "N/A").strip().upper()
            if psgld in PSGLD_ESTIMATE:
                riders_estimate += PSGLD_ESTIMATE[psgld]

        record_sample(riders_estimate, len(vehicles))
    except Exception as exc:
        logger.error("ridership_scheduler tick error: %s", repr(exc))


def make_scheduler():
    """
    Build and start the APScheduler BackgroundScheduler for ridership sampling.
    Returns the scheduler so app.py can shut it down on teardown, or None if
    disabled/unavailable.
    """
    import os
    if os.getenv("ENABLE_RIDERSHIP_SCHEDULER", "true").lower() != "true":
        logger.info("ridership_scheduler: disabled by ENABLE_RIDERSHIP_SCHEDULER env var")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.error("APScheduler not installed -- ridership scheduler disabled")
        return None

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        sample_tick, "interval",
        seconds=_SAMPLE_INTERVAL_SECONDS,
        id="ridership_sample_tick",
        max_instances=1,
    )
    scheduler.start()
    logger.info("ridership_scheduler: started (%ss interval)", _SAMPLE_INTERVAL_SECONDS)
    return scheduler
