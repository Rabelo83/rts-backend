import csv
import json
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "rts_gtfs.sqlite"
BUS_STOPS_JSON = BASE_DIR / "bus_stops" / "bus_stops_optimized.json"
OUT_PATH = BASE_DIR / "db" / "bus_stops_summary.csv"


def load_bus_stop_metadata():
    if not BUS_STOPS_JSON.exists():
        return {}
    with BUS_STOPS_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("metadata", {})


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")

    meta = load_bus_stop_metadata()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM bus_stops;")
        total_stops = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM stop_match WHERE gtfs_stop_id IS NOT NULL;")
        matched = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM stop_match WHERE gtfs_stop_id IS NULL;")
        unmatched = cur.fetchone()[0]

        with BUS_STOPS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        stops = data.get("busStops", [])

        status_counts = {}
        area_counts = {}
        for s in stops:
            status = s.get("status") or "UNKNOWN"
            area = s.get("area") or "UNKNOWN"
            status_counts[status] = status_counts.get(status, 0) + 1
            area_counts[area] = area_counts.get(area, 0) + 1

        status_rows = [(k, status_counts[k]) for k in sorted(status_counts)]
        area_rows = [(k, area_counts[k]) for k in sorted(area_counts)]

        with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "value"])
            writer.writerow(["total_stops", total_stops])
            writer.writerow(["matched_stops", matched])
            writer.writerow(["unmatched_stops", unmatched])
            if meta:
                writer.writerow(["metadata_totalStops", meta.get("totalStops")])
                classification = meta.get("classification", {})
                writer.writerow(["metadata_ufStops", classification.get("ufStops")])
                writer.writerow(["metadata_nonUfStops", classification.get("nonUfStops")])
                status_dist = meta.get("statusDistribution", {})
                writer.writerow(["metadata_status_active", status_dist.get("ACTIVE")])
                writer.writerow(["metadata_status_inactive", status_dist.get("INACTIVE")])
                writer.writerow(["metadata_status_proposed", status_dist.get("PROPOSED")])

            writer.writerow([])
            writer.writerow(["status", "count"])
            writer.writerows(status_rows)

            writer.writerow([])
            writer.writerow(["area", "count"])
            writer.writerows(area_rows)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
