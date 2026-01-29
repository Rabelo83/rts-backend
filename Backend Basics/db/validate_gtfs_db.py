import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "rts_gtfs.sqlite"


CHECKS = [
    (
        "Unmatched bus stops (padded) vs GTFS stops",
        """
        SELECT COUNT(*) FROM stop_match
        WHERE gtfs_stop_id IS NULL;
        """,
    ),
    (
        "Stops with no stop_times",
        """
        SELECT COUNT(*)
        FROM stops s
        LEFT JOIN stop_times st ON st.stop_id = s.stop_id
        WHERE st.stop_id IS NULL;
        """,
    ),
    (
        "Trips without stop_times",
        """
        SELECT COUNT(*)
        FROM trips t
        LEFT JOIN stop_times st ON st.trip_id = t.trip_id
        WHERE st.trip_id IS NULL;
        """,
    ),
    (
        "Trips without route",
        """
        SELECT COUNT(*)
        FROM trips t
        LEFT JOIN routes r ON r.route_id = t.route_id
        WHERE r.route_id IS NULL;
        """,
    ),
]


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        print("Validation checks")
        for label, sql in CHECKS:
            cur = conn.execute(sql)
            value = cur.fetchone()[0]
            print(f"- {label}: {value}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
