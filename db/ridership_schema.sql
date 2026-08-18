-- db/ridership_schema.sql
-- Daily accumulated ridership estimates powering the RTS Pulse board's
-- Today / This Week / This Month rows. Run via utils/ridership_db.py
-- init_db() -- all statements are idempotent.

CREATE TABLE IF NOT EXISTS daily_ridership (
  date               TEXT PRIMARY KEY,          -- 'YYYY-MM-DD', agency-local (Eastern)
  sum_ratio          REAL    NOT NULL DEFAULT 0, -- running sum of (riders_estimate / buses_active) per sample
  sample_count       INTEGER NOT NULL DEFAULT 0,
  riders_estimate    INTEGER NOT NULL DEFAULT 0, -- avg_ratio * trips_completed_so_far, refreshed each sample
  buses_active_last  INTEGER,
  updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
