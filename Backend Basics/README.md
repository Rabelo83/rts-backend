# RTS Schedule Q&A (GTFS + Bus Stops)

This folder builds a local SQLite database from GTFS and bus stop inventory,
then provides a lightweight natural language answering layer for schedule
questions.

## What is inside
- `RTSGTFS_*` (local GTFS folders) - source schedules
- `bus_stops/` - stop inventory + ID/name exports
- `db/` - build scripts, query templates, QA tools

## Quick start
Build the database:

```powershell
python db/build_gtfs_db.py
```

Ask a question:

```powershell
python db/answering_layer.py
```

## Example questions
- "When does route 5 leave Rosa Parks after 2:30 pm?"
- "What is the last bus 12 leaving the hub today?"
- "Fastest way from Reitz Union to Butler Plaza on 01/31/2026 at 2:50 pm?"

## Defaults
Stop aliases (for example "Rosa Parks" or "The Hub") are stored in:
`db/answering_defaults.json`

## Adding a new GTFS feed
1) Drop the new GTFS folder at repo root (for example `RTSGTFS_Fall2026_V1/`).
2) Update `GTFS_DIR` in `db/build_gtfs_db.py` to point at the new folder.
3) Rebuild the DB:

```powershell
python db/build_gtfs_db.py
```

## Notes
- GTFS feeds, the SQLite DB, and reports are versioned here to preserve state.
- For transfer-based routing, see `db/transfer_search.py`.