import csv
import json
import os
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
GTFS_DIR = BASE_DIR / "RTSGTFS_Spring2026_V6"
BUS_STOPS_JSON = BASE_DIR / "bus_stops" / "bus_stops_optimized.json"
DB_PATH = BASE_DIR / "db" / "rts_gtfs.sqlite"


GTFS_FILES = [
    "agency.txt",
    "calendar.txt",
    "calendar_dates.txt",
    "fare_attributes.txt",
    "fare_rules.txt",
    "feed_info.txt",
    "routes.txt",
    "shapes.txt",
    "stops.txt",
    "stop_times.txt",
    "trips.txt",
]


def ensure_db_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode = OFF;")
    conn.execute("PRAGMA synchronous = OFF;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


def quoted(cols):
    return ", ".join([f'"{c}"' for c in cols])


def create_table(cur, table, columns):
    cols = ", ".join([f'"{c}" TEXT' for c in columns])
    cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols});')


def load_csv_table(conn, filepath):
    table = filepath.stem
    with filepath.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)

        extra_cols = []
        if table == "stops":
            extra_cols = ["stop_id_padded"]

        columns = header + extra_cols
        cur = conn.cursor()
        create_table(cur, table, columns)

        insert_cols = quoted(columns)
        placeholders = ", ".join(["?"] * len(columns))
        sql = f'INSERT INTO "{table}" ({insert_cols}) VALUES ({placeholders});'

        batch = []
        for row in reader:
            if not row or all(not cell for cell in row):
                continue
            row = [cell.strip() if isinstance(cell, str) else cell for cell in row]
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[: len(header)]

            if table == "stops":
                stop_id = row[header.index("stop_id")] if "stop_id" in header else ""
                if stop_id.isdigit():
                    stop_id_padded = stop_id.zfill(4)
                else:
                    stop_id_padded = stop_id
                row = row + [stop_id_padded]
            batch.append(row)
            if len(batch) >= 5000:
                cur.executemany(sql, batch)
                batch = []
        if batch:
            cur.executemany(sql, batch)


def load_bus_stops(conn):
    if not BUS_STOPS_JSON.exists():
        return
    with BUS_STOPS_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)
    stops = data.get("busStops", [])

    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS bus_stops ("
        "stop_id_padded TEXT, "
        "stop_id_raw INTEGER, "
        "stop_name TEXT"
        ");"
    )

    batch = []
    for s in stops:
        raw = s.get("stopId")
        try:
            raw_int = int(raw)
            padded = f"{raw_int:04d}"
        except (TypeError, ValueError):
            raw_int = None
            padded = ""
        batch.append((padded, raw_int, s.get("stopName")))
        if len(batch) >= 5000:
            cur.executemany(
                "INSERT INTO bus_stops (stop_id_padded, stop_id_raw, stop_name) "
                "VALUES (?, ?, ?);",
                batch,
            )
            batch = []
    if batch:
        cur.executemany(
            "INSERT INTO bus_stops (stop_id_padded, stop_id_raw, stop_name) "
            "VALUES (?, ?, ?);",
            batch,
        )


def create_indexes(conn):
    cur = conn.cursor()
    cur.execute('CREATE INDEX IF NOT EXISTS idx_stops_stop_id ON stops("stop_id");')
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_stops_stop_id_padded ON stops("stop_id_padded");'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_stop_times_trip_id ON stop_times("trip_id");'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_stop_times_stop_id ON stop_times("stop_id");'
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_trips_route_id ON trips("route_id");')
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_trips_service_id ON trips("service_id");'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_calendar_service_id ON calendar("service_id");'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_calendar_dates_service_id '
        'ON calendar_dates("service_id");'
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_bus_stops_padded ON bus_stops(stop_id_padded);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_fuzzy_lookup_norm ON fuzzy_lookup(normalized);"
    )


def create_views(conn):
    cur = conn.cursor()
    cur.execute(
        "CREATE VIEW IF NOT EXISTS stop_match AS "
        "SELECT b.stop_id_padded AS stop_id_padded, "
        "b.stop_id_raw AS bus_stop_id_raw, "
        "b.stop_name AS bus_stop_name, "
        "s.stop_id AS gtfs_stop_id, "
        "s.stop_name AS gtfs_stop_name "
        "FROM bus_stops b "
        "LEFT JOIN stops s ON s.stop_id_padded = b.stop_id_padded;"
    )
    cur.execute(
        "CREATE VIEW IF NOT EXISTS calendar_service_days AS "
        "SELECT service_id, start_date, end_date, "
        "monday, tuesday, wednesday, thursday, friday, saturday, sunday "
        "FROM calendar;"
    )
    cur.execute(
        "CREATE VIEW IF NOT EXISTS calendar_exceptions AS "
        "SELECT service_id, date, exception_type "
        "FROM calendar_dates;"
    )


def normalize_text(text):
    if text is None:
        return ""
    cleaned = []
    for ch in text.lower().strip():
        if ch.isalnum() or ch.isspace():
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def create_fuzzy_lookup(conn):
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS fuzzy_lookup ("
        "entity_type TEXT, "
        "entity_id TEXT, "
        "display_name TEXT, "
        "normalized TEXT"
        ");"
    )

    batch = []
    for row in cur.execute("SELECT stop_id, stop_name FROM stops;"):
        stop_id, stop_name = row
        batch.append(("stop", stop_id, stop_name, normalize_text(stop_name)))
        if len(batch) >= 5000:
            cur.executemany(
                "INSERT INTO fuzzy_lookup (entity_type, entity_id, display_name, normalized) "
                "VALUES (?, ?, ?, ?);",
                batch,
            )
            batch = []

    for row in cur.execute(
        "SELECT route_id, route_short_name, route_long_name FROM routes;"
    ):
        route_id, short_name, long_name = row
        if short_name:
            batch.append(
                ("route", route_id, short_name, normalize_text(short_name))
            )
        if long_name:
            batch.append(("route", route_id, long_name, normalize_text(long_name)))
        if len(batch) >= 5000:
            cur.executemany(
                "INSERT INTO fuzzy_lookup (entity_type, entity_id, display_name, normalized) "
                "VALUES (?, ?, ?, ?);",
                batch,
            )
            batch = []

    for row in cur.execute(
        "SELECT DISTINCT trip_headsign FROM trips WHERE trip_headsign IS NOT NULL;"
    ):
        (headsign,) = row
        batch.append(("headsign", None, headsign, normalize_text(headsign)))
        if len(batch) >= 5000:
            cur.executemany(
                "INSERT INTO fuzzy_lookup (entity_type, entity_id, display_name, normalized) "
                "VALUES (?, ?, ?, ?);",
                batch,
            )
            batch = []

    if batch:
        cur.executemany(
            "INSERT INTO fuzzy_lookup (entity_type, entity_id, display_name, normalized) "
            "VALUES (?, ?, ?, ?);",
            batch,
        )


def main():
    ensure_db_dir()
    if not GTFS_DIR.exists():
        raise SystemExit(f"GTFS folder not found: {GTFS_DIR}")
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = connect_db()
    try:
        for name in GTFS_FILES:
            path = GTFS_DIR / name
            if path.exists():
                load_csv_table(conn, path)
        load_bus_stops(conn)
        create_fuzzy_lookup(conn)
        create_indexes(conn)
        create_views(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
