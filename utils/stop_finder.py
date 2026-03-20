"""
utils/stop_finder.py
Load bus_stops.geojson into a fast SQLite index and find stops near a lat/lon.
"""
import json
import math
import os
import sqlite3
from pathlib import Path

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
_STOPS_DB = _DATA_DIR / "stops_geo.sqlite"
_GEOJSON = Path(__file__).resolve().parents[1] / "Backend Basics" / "bus_stops" / "bus_stops.geojson"
_GTFS_DB = Path(__file__).resolve().parents[1] / "Backend Basics" / "db" / "rts_gtfs.sqlite"

_DIRECTIONS = ("Northbound", "Southbound", "Eastbound", "Westbound")
_WALK_SPEED_MPS = 1.2        # metres per second (~4.3 km/h)
_WALK_CROSSING_SEC = 60      # penalty for crossing the street at a transfer


# ── DB bootstrap ─────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_STOPS_DB)
    c.row_factory = sqlite3.Row
    return c


def ensure_stops_db() -> None:
    """Create stops_geo.sqlite from bus_stops.geojson if not already loaded."""
    _STOPS_DB.parent.mkdir(parents=True, exist_ok=True)
    c = _conn()
    c.execute("""
        CREATE TABLE IF NOT EXISTS stops_geo (
            stop_id    INTEGER PRIMARY KEY,
            stop_name  TEXT,
            lat        REAL NOT NULL,
            lon        REAL NOT NULL,
            street     TEXT,
            crossroad  TEXT,
            direction  TEXT,
            area       TEXT,
            status     TEXT,
            shelters   INTEGER DEFAULT 0,
            lighting   TEXT,
            is_uf      INTEGER DEFAULT 0
        )
    """)
    c.commit()

    if c.execute("SELECT COUNT(*) FROM stops_geo").fetchone()[0] > 0:
        c.close()
        return

    if not _GEOJSON.exists():
        c.close()
        return

    # Load stop IDs that actually appear in GTFS stop_times (active stops only)
    gtfs_stop_ids: set[int] = set()
    if _GTFS_DB.exists():
        gc = sqlite3.connect(_GTFS_DB)
        for row in gc.execute("SELECT DISTINCT CAST(stop_id AS INTEGER) FROM stop_times"):
            gtfs_stop_ids.add(row[0])
        gc.close()

    with open(_GEOJSON, encoding="utf-8") as f:
        geojson = json.load(f)

    rows = []
    for feature in geojson["features"]:
        p = feature["properties"]
        if p.get("status") != "ACTIVE":
            continue
        stop_id = p["stopId"]
        # Skip stops that don't exist in GTFS stop_times (ghost stops)
        if gtfs_stop_ids and stop_id not in gtfs_stop_ids:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        name = p.get("stopName", "")
        direction = next((d for d in _DIRECTIONS if d in name), None)
        rows.append((
            stop_id, name, lat, lon,
            p.get("street"), p.get("crossroad"), direction,
            p.get("area"), p.get("status"),
            p.get("shelters", 0), p.get("lighting"),
            1 if p.get("isUF") else 0,
        ))

    c.executemany("""
        INSERT OR REPLACE INTO stops_geo
        (stop_id, stop_name, lat, lon, street, crossroad, direction, area, status, shelters, lighting, is_uf)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    c.commit()
    c.close()


# ── Haversine ────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((phi2 - phi1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def walk_minutes(dist_m: float) -> float:
    return round(dist_m / (_WALK_SPEED_MPS * 60), 1)


# ── Public API ───────────────────────────────────────────────────────────────

def find_nearest_stops(lat: float, lon: float, radius_m: int = 500, limit: int = 5) -> list[dict]:
    """Return nearest active stops within radius_m, sorted by walking distance."""
    ensure_stops_db()
    deg = 1 / 111_000
    dlat = radius_m * deg
    dlon = radius_m * deg / max(math.cos(math.radians(lat)), 0.001)

    c = _conn()
    rows = c.execute("""
        SELECT stop_id, stop_name, lat, lon, street, crossroad, direction, shelters, lighting, is_uf
        FROM stops_geo
        WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
    """, (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
    c.close()

    results = []
    for r in rows:
        dist = haversine_m(lat, lon, r["lat"], r["lon"])
        if dist <= radius_m:
            results.append({
                "stop_id": r["stop_id"],
                "stop_name": r["stop_name"],
                "lat": r["lat"],
                "lon": r["lon"],
                "street": r["street"],
                "crossroad": r["crossroad"],
                "direction": r["direction"],
                "distance_m": round(dist),
                "walk_min": walk_minutes(dist),
                "has_shelter": (r["shelters"] or 0) > 0,
                "is_uf": bool(r["is_uf"]),
            })

    return sorted(results, key=lambda x: x["distance_m"])[:limit]


def get_stop_by_id(stop_id: int) -> dict | None:
    """Look up a single stop by ID."""
    ensure_stops_db()
    c = _conn()
    row = c.execute(
        "SELECT * FROM stops_geo WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    c.close()
    return dict(row) if row else None


def same_side_penalty_sec(stop_a_id: int, stop_b_id: int) -> int:
    """
    Return extra seconds penalty for the transfer walk between two stops.
    0   = same stop or same side of street (no crossing).
    60  = opposite side (need to cross + wait for signal).
    """
    if stop_a_id == stop_b_id:
        return 0
    a = get_stop_by_id(stop_a_id)
    b = get_stop_by_id(stop_b_id)
    if not a or not b:
        return 0
    # Same crossroad but opposite direction → crossing required
    if (a.get("crossroad") and a["crossroad"] == b.get("crossroad")
            and a.get("direction") and b.get("direction")
            and a["direction"] != b["direction"]):
        return _WALK_CROSSING_SEC
    return 0
