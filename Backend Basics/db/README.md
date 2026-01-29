# GTFS + Bus Stops SQLite

This folder contains a local SQLite database built from the GTFS feed and the
bus stops inventory.

## Build
Run this from the project root:

```powershell
python db/build_gtfs_db.py
```

## Output
- `db/rts_gtfs.sqlite`: SQLite database.

## Key tables
- `stops`, `stop_times`, `trips`, `routes`, `calendar`, `calendar_dates`
- `bus_stops`: stop_id_padded, stop_id_raw, stop_name
- `stop_match` (view): joins bus stops to GTFS stops by padded stop_id

## Notes
- `stop_id_padded` is the canonical ID for matching (4-digit padded string).
- Original numeric IDs are preserved in `bus_stops.stop_id_raw`.
- `db/queries.sql` contains ready-to-use query templates for the LLM.
- `db/validate_gtfs_db.py` runs basic data sanity checks.
- `db/export_unmatched_bus_stops.py` writes `db/unmatched_bus_stops.csv`.
- `db/export_matched_bus_stops.py` writes `db/matched_bus_stops.csv`.
- `db/export_bus_stops_summary.py` writes `db/bus_stops_summary.csv`.
- `db/transfer_search.py` computes fastest 1-transfer trips between two stops.
- `db/answering_layer.py` provides a basic NL Q&A layer for schedule queries.
- `db/answering_defaults.json` stores default stop aliases for Q&A.
