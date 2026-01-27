import os
import io
import zipfile
import sqlite3
from datetime import datetime
import requests
import pandas as pd

DEFAULT_GTFS_URL = os.environ.get(
    "GTFS_URL",
    # RTS publishes GTFS, but filenames change. You can override via Render env var GTFS_URL.
    "https://go-rts.com/wp-content/uploads/2024/01/RTSGTFS_Spring24_V1.zip"
)

DB_PATH = os.environ.get("GTFS_DB_PATH", "data/gtfs.db")


def _http_get(url: str) -> bytes:
    # Some hosts block unknown/empty user agents; send a normal UA.
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; RTSBot/1.0; +https://53733956.com/)"
    }
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.content


def _read_csv_from_zip(zf: zipfile.ZipFile, name: str) -> pd.DataFrame:
    with zf.open(name) as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False)


def _time_to_secs(t: str) -> int | None:
    # GTFS times can be "25:10:00" (after midnight) -> allow >24h
    if not t:
        return None
    try:
        hh, mm, ss = t.split(":")
        return int(hh) * 3600 + int(mm) * 60 + int(ss)
    except Exception:
        return None


def build_gtfs_db(gtfs_zip_bytes: bytes, db_path: str = DB_PATH) -> dict:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    zf = zipfile.ZipFile(io.BytesIO(gtfs_zip_bytes))

    needed = {
        "stops.txt",
        "routes.txt",
        "trips.txt",
        "stop_times.txt",
    }
    missing = [f for f in needed if f not in zf.namelist()]
    if missing:
        raise RuntimeError(f"GTFS zip missing required files: {missing}")

    stops = _read_csv_from_zip(zf, "stops.txt")
    routes = _read_csv_from_zip(zf, "routes.txt")
    trips = _read_csv_from_zip(zf, "trips.txt")
    stop_times = _read_csv_from_zip(zf, "stop_times.txt")

    calendar = None
    calendar_dates = None
    if "calendar.txt" in zf.namelist():
        calendar = _read_csv_from_zip(zf, "calendar.txt")
    if "calendar_dates.txt" in zf.namelist():
        calendar_dates = _read_csv_from_zip(zf, "calendar_dates.txt")

    # Keep only columns we need (safe even if extra columns exist)
    stops = stops[[c for c in ["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"] if c in stops.columns]]
    routes = routes[[c for c in ["route_id", "route_short_name", "route_long_name"] if c in routes.columns]]
    trips = trips[[c for c in ["trip_id", "route_id", "service_id", "trip_headsign", "direction_id"] if c in trips.columns]]
    stop_times = stop_times[[c for c in ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"] if c in stop_times.columns]]

    # Add seconds fields for fast querying
    stop_times["arr_secs"] = stop_times["arrival_time"].apply(_time_to_secs)
    stop_times["dep_secs"] = stop_times["departure_time"].apply(_time_to_secs)

    # Build SQLite
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    cur.executescript(
        """
        DROP TABLE IF EXISTS stops;
        DROP TABLE IF EXISTS routes;
        DROP TABLE IF EXISTS trips;
        DROP TABLE IF EXISTS stop_times;
        DROP TABLE IF EXISTS calendar;
        DROP TABLE IF EXISTS calendar_dates;

        CREATE TABLE stops (
          stop_id TEXT PRIMARY KEY,
          stop_code TEXT,
          stop_name TEXT,
          stop_lat TEXT,
          stop_lon TEXT
        );

        CREATE TABLE routes (
          route_id TEXT PRIMARY KEY,
          route_short_name TEXT,
          route_long_name TEXT
        );

        CREATE TABLE trips (
          trip_id TEXT PRIMARY KEY,
          route_id TEXT,
          service_id TEXT,
          trip_headsign TEXT,
          direction_id TEXT
        );

        CREATE TABLE stop_times (
          trip_id TEXT,
          stop_id TEXT,
          stop_sequence INTEGER,
          arrival_time TEXT,
          departure_time TEXT,
          arr_secs INTEGER,
          dep_secs INTEGER
        );

        CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
        CREATE INDEX idx_stop_times_trip ON stop_times(trip_id);
        CREATE INDEX idx_trips_service ON trips(service_id);
        CREATE INDEX idx_routes_short ON routes(route_short_name);
        """
    )

    stops.to_sql("stops", con, if_exists="append", index=False)
    routes.to_sql("routes", con, if_exists="append", index=False)
    trips.to_sql("trips", con, if_exists="append", index=False)
    stop_times.to_sql("stop_times", con, if_exists="append", index=False)

    if calendar is not None:
        calendar = calendar[[c for c in [
            "service_id", "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday", "start_date", "end_date"
        ] if c in calendar.columns]]

        cur.executescript(
            """
            CREATE TABLE calendar (
              service_id TEXT PRIMARY KEY,
              monday TEXT, tuesday TEXT, wednesday TEXT, thursday TEXT,
              friday TEXT, saturday TEXT, sunday TEXT,
              start_date TEXT, end_date TEXT
            );
            """
        )
        calendar.to_sql("calendar", con, if_exists="append", index=False)

    if calendar_dates is not None:
        calendar_dates = calendar_dates[[c for c in ["service_id", "date", "exception_type"] if c in calendar_dates.columns]]
        cur.executescript(
            """
            CREATE TABLE calendar_dates (
              service_id TEXT,
              date TEXT,
              exception_type TEXT
            );
            CREATE INDEX idx_calendar_dates_date ON calendar_dates(date);
            """
        )
        calendar_dates.to_sql("calendar_dates", con, if_exists="append", index=False)

    con.commit()
    con.close()

    return {
        "db_path": db_path,
        "stops": len(stops),
        "routes": len(routes),
        "trips": len(trips),
        "stop_times": len(stop_times),
        "has_calendar": calendar is not None,
        "has_calendar_dates": calendar_dates is not None,
        "gtfs_url": DEFAULT_GTFS_URL,
        "built_at": datetime.utcnow().isoformat() + "Z",
    }


def main():
    url = DEFAULT_GTFS_URL
    print(f"GTFS ingest: downloading {url}")
    gtfs_bytes = _http_get(url)
    info = build_gtfs_db(gtfs_bytes, DB_PATH)
    print("GTFS ingest complete:", info)


if __name__ == "__main__":
    main()
