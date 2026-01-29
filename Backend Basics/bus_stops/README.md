# Bus Stops

This folder contains stop IDs and names derived from the local bus stop
inventory.

Files:
- `stops_id_name.csv`: two columns (stopId, stopName) for quick lookup.
- `stops_id_name_padded.csv`: includes stopId padded to 4 digits for GTFS matching.
- `bus_stops_optimized.json`: full source data (metadata + stops list).

Notes:
- Stop IDs on signs are typically the padded 4-digit IDs used in GTFS.