PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS routes (
  route_id TEXT PRIMARY KEY,
  route_name TEXT
);

CREATE TABLE IF NOT EXISTS stops (
  stop_id TEXT PRIMARY KEY,
  stop_name TEXT
);

CREATE TABLE IF NOT EXISTS calendar (
  service_id TEXT PRIMARY KEY,
  start_date TEXT,
  end_date TEXT,
  mon INTEGER,
  tue INTEGER,
  wed INTEGER,
  thu INTEGER,
  fri INTEGER,
  sat INTEGER,
  sun INTEGER
);

CREATE TABLE IF NOT EXISTS calendar_dates (
  service_id TEXT,
  date TEXT,
  exception_type INTEGER,
  PRIMARY KEY (service_id, date)
);

CREATE TABLE IF NOT EXISTS trips (
  trip_id TEXT PRIMARY KEY,
  route_id TEXT,
  service_id TEXT,
  direction_id INTEGER,
  headsign TEXT
);

CREATE TABLE IF NOT EXISTS stop_times (
  trip_id TEXT,
  route_id TEXT,
  stop_id TEXT,
  stop_sequence INTEGER,
  arrival_time TEXT,
  departure_time TEXT,
  arrival_secs INTEGER,
  departure_secs INTEGER,
  PRIMARY KEY (trip_id, stop_sequence)
);

CREATE TABLE IF NOT EXISTS stop_last_departure (
  route_id TEXT,
  service_id TEXT,
  stop_id TEXT,
  last_departure_time TEXT,
  last_departure_secs INTEGER,
  PRIMARY KEY (route_id, service_id, stop_id)
);

CREATE INDEX IF NOT EXISTS idx_stop_times_route_stop
  ON stop_times(route_id, stop_id);

CREATE INDEX IF NOT EXISTS idx_trips_route_service
  ON trips(route_id, service_id);
