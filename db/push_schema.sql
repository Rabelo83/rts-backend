-- db/push_schema.sql
-- Web push subscriptions, user identities, favorites, and alert log.
-- Run via utils/push_db.py init_db() — all statements are idempotent.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_identities (
  anon_uuid  TEXT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  language   TEXT DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  anon_uuid  TEXT NOT NULL REFERENCES user_identities(anon_uuid),
  endpoint   TEXT NOT NULL UNIQUE,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  user_agent TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS favorites (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  anon_uuid           TEXT    NOT NULL REFERENCES user_identities(anon_uuid),
  route_id            TEXT    NOT NULL,
  stop_id             TEXT    NOT NULL,
  departure_hhmm      TEXT    NOT NULL,   -- "07:30"
  days_of_week        TEXT    NOT NULL,   -- "mon,tue,wed,thu,fri"
  delay_threshold_min INTEGER NOT NULL DEFAULT 3,
  active              INTEGER NOT NULL DEFAULT 1,
  created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  favorite_id INTEGER NOT NULL REFERENCES favorites(id),
  fired_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  delay_min   INTEGER,
  outcome     TEXT    -- "sent", "failed", "no_subscription", "deduped", "gone"
);

CREATE INDEX IF NOT EXISTS idx_fav_active
  ON favorites(active, days_of_week, departure_hhmm);

CREATE INDEX IF NOT EXISTS idx_alert_dedupe
  ON alert_log(favorite_id, fired_at);

CREATE INDEX IF NOT EXISTS idx_sub_uuid
  ON push_subscriptions(anon_uuid);
