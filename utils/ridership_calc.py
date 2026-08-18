"""
utils/ridership_calc.py
Ridership estimation for the RTS Pulse board: Today / This Week / This Month /
This Year (fiscal) / Lifetime.

Methodology (all explicitly ESTIMATES, not APC-grade boarding counts):

  Today = (average riders-per-active-bus observed so far today, sampled every
           few minutes via utils/ridership_scheduler.py) x (GTFS trips
           completed so far today).

  This Week / This Month = sum of each day's "Today" value (daily_ridership
  table), for days in the current calendar week (Mon-Sun) / month.

  This Year = official fiscal-year baseline (agency_config.yaml) + sum of
  daily values for every day AFTER the baseline's cutoff date, up to today.
  RTS fiscal year: Oct 1 - Sep 30.

  Lifetime = lifetime historical baseline (agency_config.yaml, anchored at
  the end of the prior fiscal year) + This Year's current total. This reuses
  This Year's number rather than computing a second, independent running
  total, so the two rows can never drift out of sync or double-count the
  same days.

This module never fabricates a number when no real underlying data exists --
every returned dict carries a `status` field (`estimated` / `official` /
`unavailable`) so callers can label results honestly.
"""
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency_config import get_timezone, get_ridership_config
from ridership_db import get_ridership_db

logger = logging.getLogger(__name__)

_TZ = ZoneInfo(get_timezone())
_GTFS_DB_PATH = Path(__file__).resolve().parents[1] / "Backend Basics" / "db" / "rts_gtfs.sqlite"


def _now() -> datetime:
    return datetime.now(_TZ)


def _today_str() -> str:
    return _now().date().isoformat()


# ── GTFS: trips completed so far today ────────────────────────────────────────

def count_trips_completed_today(now_et: datetime | None = None) -> int:
    """
    Count distinct GTFS trips, across every route, whose FINAL scheduled stop
    time has already passed today. Pure GTFS -- no BusTime call. Returns 0 if
    the GTFS DB isn't available (build artifact, not present in local dev).
    """
    if now_et is None:
        now_et = _now()
    if not _GTFS_DB_PATH.exists():
        return 0

    import sqlite3
    date_et = now_et.date()
    date_compact = date_et.strftime("%Y%m%d")
    date_iso = date_et.isoformat()
    now_hhmmss = now_et.strftime("%H:%M:%S")

    try:
        conn = sqlite3.connect(_GTFS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                WITH base_services AS (
                  SELECT c.service_id FROM calendar c
                  WHERE :date_compact BETWEEN c.start_date AND c.end_date
                    AND (
                      (c.monday    = 1 AND strftime('%w', :date_iso) = '1') OR
                      (c.tuesday   = 1 AND strftime('%w', :date_iso) = '2') OR
                      (c.wednesday = 1 AND strftime('%w', :date_iso) = '3') OR
                      (c.thursday  = 1 AND strftime('%w', :date_iso) = '4') OR
                      (c.friday    = 1 AND strftime('%w', :date_iso) = '5') OR
                      (c.saturday  = 1 AND strftime('%w', :date_iso) = '6') OR
                      (c.sunday    = 1 AND strftime('%w', :date_iso) = '0')
                    )
                ),
                exception_add    AS (SELECT service_id FROM calendar_dates WHERE date = :date_compact AND exception_type = 1),
                exception_remove AS (SELECT service_id FROM calendar_dates WHERE date = :date_compact AND exception_type = 2),
                active_services  AS (
                  SELECT service_id FROM base_services
                  UNION  SELECT service_id FROM exception_add
                  EXCEPT SELECT service_id FROM exception_remove
                ),
                trip_last_seq AS (
                  SELECT trip_id, MAX(CAST(stop_sequence AS INTEGER)) AS max_seq
                  FROM stop_times GROUP BY trip_id
                )
                SELECT COUNT(DISTINCT t.trip_id) AS n
                FROM trips t
                JOIN active_services a  ON a.service_id  = t.service_id
                JOIN trip_last_seq tls  ON tls.trip_id   = t.trip_id
                JOIN stop_times st      ON st.trip_id    = t.trip_id
                                        AND st.stop_sequence = tls.max_seq
                WHERE COALESCE(st.arrival_time, st.departure_time) <= :now_hhmmss
                """,
                {"date_compact": date_compact, "date_iso": date_iso, "now_hhmmss": now_hhmmss},
            ).fetchone()
            return int(row["n"]) if row and row["n"] is not None else 0
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("count_trips_completed_today failed: %s", exc)
        return 0


# ── Sampling (called by utils/ridership_scheduler.py) ─────────────────────────

def record_sample(riders_estimate: int, buses_active: int) -> None:
    """
    Record one live snapshot into today's running average, then recompute
    today's estimated total. Called every few minutes by the scheduler.
    Buses_active == 0 samples are skipped (no ratio to record; avoids
    corrupting the average with an empty-system reading, e.g. overnight).
    """
    if buses_active <= 0:
        return

    ratio = riders_estimate / buses_active
    today = _today_str()
    now = _now()
    trips_completed = count_trips_completed_today(now)

    db = get_ridership_db()
    row = db.execute("SELECT sum_ratio, sample_count FROM daily_ridership WHERE date = ?", (today,)).fetchone()
    if row:
        sum_ratio = row["sum_ratio"] + ratio
        sample_count = row["sample_count"] + 1
    else:
        sum_ratio = ratio
        sample_count = 1

    avg_ratio = sum_ratio / sample_count
    estimated = round(avg_ratio * trips_completed)

    db.execute(
        """
        INSERT INTO daily_ridership (date, sum_ratio, sample_count, riders_estimate, buses_active_last, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(date) DO UPDATE SET
          sum_ratio = excluded.sum_ratio,
          sample_count = excluded.sample_count,
          riders_estimate = excluded.riders_estimate,
          buses_active_last = excluded.buses_active_last,
          updated_at = CURRENT_TIMESTAMP
        """,
        (today, sum_ratio, sample_count, estimated, buses_active),
    )
    db.commit()


# ── Daily / range lookups ──────────────────────────────────────────────────────

def _daily_value(date_str: str) -> int:
    db = get_ridership_db()
    row = db.execute("SELECT riders_estimate FROM daily_ridership WHERE date = ?", (date_str,)).fetchone()
    return int(row["riders_estimate"]) if row else 0


def _range_sum(start: date, end: date) -> tuple[int, int]:
    """Return (sum of riders_estimate, number of days with a stored row) for [start, end]."""
    db = get_ridership_db()
    rows = db.execute(
        "SELECT riders_estimate FROM daily_ridership WHERE date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return sum(r["riders_estimate"] for r in rows), len(rows)


def get_today_estimate() -> dict:
    today = _now().date()
    value = _daily_value(today.isoformat())
    return {"riders_estimate": value, "status": "estimated", "date": today.isoformat()}


def get_week_estimate() -> dict:
    today = _now().date()
    monday = today - timedelta(days=today.weekday())
    total, days = _range_sum(monday, today)
    return {
        "riders_estimate": total,
        "status": "estimated",
        "period_start": monday.isoformat(),
        "period_end": today.isoformat(),
        "days_included": days,
    }


def get_month_estimate() -> dict:
    today = _now().date()
    first = today.replace(day=1)
    total, days = _range_sum(first, today)
    return {
        "riders_estimate": total,
        "status": "estimated",
        "period_start": first.isoformat(),
        "period_end": today.isoformat(),
        "days_included": days,
    }


# ── Fiscal year (Oct 1 - Sep 30) ────────────────────────────────────────────────

def fiscal_year_start(d: date) -> date:
    """RTS fiscal year runs Oct 1 - Sep 30. Return the Oct 1 that starts d's FY."""
    if d.month >= 10:
        return date(d.year, 10, 1)
    return date(d.year - 1, 10, 1)


def fiscal_year_label(fy_start: date) -> str:
    """Oct 1 2025 -> Sep 30 2026 is 'FY2026' (RTS convention: FY named for the ending year)."""
    return f"FY{fy_start.year + 1}"


def get_fiscal_year_estimate() -> dict:
    """
    This Year = official baseline (through its cutoff_date) + sum of daily
    estimates for every day after the cutoff, up to today -- only while
    today still falls within the SAME fiscal year as the baseline. If the
    fiscal year has since rolled over (past Oct 1) without a new baseline
    being entered, the stale baseline is dropped and This Year restarts from
    pure daily accumulation for the new fiscal year -- never silently
    carries a prior year's official number into a new year's total.
    """
    cfg = get_ridership_config().get("fiscal_year", {})
    today = _now().date()
    fy_start = fiscal_year_start(today)

    baseline_value = cfg.get("baseline_value")
    cutoff_str = cfg.get("baseline_cutoff_date")
    baseline_in_current_fy = False
    if baseline_value is not None and cutoff_str:
        try:
            cutoff_date = date.fromisoformat(cutoff_str)
            baseline_in_current_fy = fiscal_year_start(cutoff_date) == fy_start
        except ValueError:
            baseline_in_current_fy = False

    if baseline_in_current_fy:
        accum_start = cutoff_date + timedelta(days=1)
        since_cutoff, days = _range_sum(accum_start, today) if accum_start <= today else (0, 0)
        total = int(baseline_value) + since_cutoff
        return {
            "riders_estimate": total,
            "status": "official+estimated",
            "fiscal_year_start": fy_start.isoformat(),
            "fiscal_year_label": fiscal_year_label(fy_start),
            "baseline_value": int(baseline_value),
            "baseline_cutoff_date": cutoff_str,
            "baseline_status": cfg.get("status", "official"),
            "baseline_source_org": cfg.get("source_org"),
            "estimated_since_cutoff": since_cutoff,
            "days_estimated": days,
        }

    # No usable baseline for this fiscal year yet -- pure daily accumulation
    # since Oct 1, same shape as This Week/Month.
    total, days = _range_sum(fy_start, today)
    return {
        "riders_estimate": total,
        "status": "estimated",
        "fiscal_year_start": fy_start.isoformat(),
        "fiscal_year_label": fiscal_year_label(fy_start),
        "baseline_value": None,
        "days_estimated": days,
    }


# ── Lifetime ─────────────────────────────────────────────────────────────────

def get_lifetime_estimate() -> dict:
    """
    Lifetime = historical baseline (agency_config.yaml, status estimated or
    verified) + This Year's current total (reuses get_fiscal_year_estimate()
    rather than computing an independent sum, so the two board rows can
    never double-count or drift apart).

    Display precision matches the baseline's own precision: while the
    baseline status is "estimated" (a rounded, working figure), the display
    value is rounded down to the nearest million with a "+" suffix rather
    than showing exact digits that would imply false precision.
    """
    cfg = get_ridership_config().get("lifetime", {})
    baseline_value = cfg.get("baseline_value")
    if baseline_value is None:
        return {"riders_estimate": None, "status": "unavailable"}

    fy = get_fiscal_year_estimate()
    raw_total = int(baseline_value) + int(fy.get("riders_estimate", 0))
    status = cfg.get("status", "estimated")

    if status == "estimated":
        rounded = (raw_total // 1_000_000) * 1_000_000
        display = f"{rounded:,}+"
    else:
        rounded = raw_total
        display = f"{raw_total:,}"

    return {
        "riders_estimate": raw_total,
        "riders_display": display,
        "status": status,
        "baseline_value": int(baseline_value),
        "baseline_cutoff_date": cfg.get("baseline_cutoff_date"),
        "baseline_source": cfg.get("source_org"),
        "confidence": cfg.get("confidence"),
        "service_start_year": cfg.get("service_start_year"),
        "working_range_low": cfg.get("working_range_low"),
        "working_range_high": cfg.get("working_range_high"),
        "notes": cfg.get("notes"),
    }
