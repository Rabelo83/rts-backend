import csv
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "rts_gtfs.sqlite"
OUT_PATH = BASE_DIR / "db" / "unmatched_bus_stops.csv"


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.stop_id_padded, b.bus_stop_id_raw, b.bus_stop_name
            FROM stop_match b
            WHERE b.gtfs_stop_id IS NULL
            ORDER BY b.stop_id_padded;
            """
        )
        with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["stop_id_padded", "stop_id_raw", "stop_name"])
            writer.writerows(cur.fetchall())
    finally:
        conn.close()


if __name__ == "__main__":
    main()
