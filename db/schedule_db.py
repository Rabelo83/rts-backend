# db/schedule_db.py
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# You can override this on Render with an env var if needed.
DB_PATH: str = os.environ.get("DB_PATH", "data/schedule.db")

AnyParams = Sequence[Any]


def _db_file() -> Path:
    # Always resolve relative to project root (this file lives in db/)
    here = Path(__file__).resolve()
    project_root = here.parent.parent
    return (project_root / DB_PATH).resolve()


def _connect() -> sqlite3.Connection:
    db_file = _db_file()
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn


def db_info() -> Dict[str, Any]:
    """Lightweight health info about the DB file and tables."""
    db_file = _db_file()
    info: Dict[str, Any] = {
        "db_path": str(db_file),
        "exists": db_file.exists(),
        "tables": [],
    }
    if not db_file.exists():
        return info

    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            info["tables"] = [r["name"] for r in rows]
    except Exception as e:
        info["error"] = str(e)
    return info


def list_routes() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT route_id, route_name FROM routes ORDER BY CAST(route_id AS INTEGER), route_id"
        ).fetchall()
    return [{"route_id": r["route_id"], "route_name": r["route_name"]} for r in rows]


def find_stops(q: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Search in the global stops table (not route-filtered)."""
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q.lower()}%"
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT stop_id, stop_name
            FROM stops
            WHERE LOWER(stop_name) LIKE ? OR LOWER(stop_id) LIKE ?
            ORDER BY stop_name
            LIMIT ?
            """,
            (like, like, int(limit)),
        ).fetchall()
    return [{"stop_id": r["stop_id"], "stop_name": r["stop_name"]} for r in rows]


def route_stops(
    route_id: str,
    service_id: str = "mon_fri",
    q: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    Stops that appear on a route for a given service_id.
    Includes min/max stop_sequence and count of stop_time rows.
    """
    route_id = (route_id or "").strip()
    service_id = (service_id or "").strip()
    if not route_id or not service_id:
        return []

    params: List[Any] = [route_id, service_id]
    where_extra = ""
    if q:
        q_like = f"%{q.lower()}%"
        where_extra = "AND (LOWER(s.stop_name) LIKE ? OR LOWER(st.stop_id) LIKE ?)"
        params.extend([q_like, q_like])

    params.append(int(limit))

    sql = f"""
        SELECT
            st.stop_id AS stop_id,
            s.stop_name AS stop_name,
            MIN(st.stop_sequence) AS min_seq,
            MAX(st.stop_sequence) AS max_seq,
            COUNT(*) AS n_rows
        FROM stop_times st
        JOIN stops s ON s.stop_id = st.stop_id
        JOIN trips t ON t.trip_id = st.trip_id
        WHERE st.route_id = ?
          AND t.service_id = ?
          {where_extra}
        GROUP BY st.stop_id, s.stop_name
        ORDER BY min_seq ASC, stop_name ASC
        LIMIT ?
    """

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "stop_id": r["stop_id"],
            "stop_name": r["stop_name"],
            "min_seq": int(r["min_seq"]) if r["min_seq"] is not None else None,
            "max_seq": int(r["max_seq"]) if r["max_seq"] is not None else None,
            "n_rows": int(r["n_rows"]) if r["n_rows"] is not None else 0,
        }
        for r in rows
    ]


def resolve_stop_on_route(route_id: str, service_id: str, stop: str) -> Optional[Dict[str, Any]]:
    """
    Accepts either:
      - exact stop_id (preferred)
      - partial stop name/id text
    Returns a dict with stop_id + stop_name (and stats) that exists on the route/service.
    """
    stop = (stop or "").strip()
    if not stop:
        return None

    # 1) Exact stop_id match on this route/service
    candidates = route_stops(route_id, service_id=service_id, q=None, limit=500)
    for c in candidates:
        if c["stop_id"] == stop:
            return c

    # 2) Filter candidates by substring match
    q_candidates = route_stops(route_id, service_id=service_id, q=stop, limit=50)
    if q_candidates:
        # pick the most frequent (best coverage)
        q_candidates.sort(key=lambda x: (x["n_rows"], -(x["min_seq"] or 0)), reverse=True)
        return q_candidates[0]

    # 3) Last attempt: global stop search then see if any appear on route
    global_hits = find_stops(stop, limit=25)
    if global_hits:
        stop_ids = {c["stop_id"]: c for c in candidates}
        for h in global_hits:
            if h["stop_id"] in stop_ids:
                return stop_ids[h["stop_id"]]

    return None


def last_departure_any(route_id: str, service_id: str, stop_id: str) -> Optional[Dict[str, Any]]:
    """
    Returns:
      {
        stop_id, stop_name,
        last_departure_time: "HH:MM:SS",
        last_departure_secs: int
      }
    Prefers stop_last_departure table, falls back to stop_times.
    """
    route_id = (route_id or "").strip()
    service_id = (service_id or "").strip()
    stop_id = (stop_id or "").strip()
    if not route_id or not service_id or not stop_id:
        return None

    with _connect() as conn:
        stop_row = conn.execute(
            "SELECT stop_name FROM stops WHERE stop_id = ?",
            (stop_id,),
        ).fetchone()
        stop_name = stop_row["stop_name"] if stop_row else stop_id

        row = conn.execute(
            """
            SELECT last_departure_time, last_departure_secs
            FROM stop_last_departure
            WHERE route_id = ? AND service_id = ? AND stop_id = ?
            """,
            (route_id, service_id, stop_id),
        ).fetchone()

        if row:
            return {
                "stop_id": stop_id,
                "stop_name": stop_name,
                "last_departure_time": row["last_departure_time"],
                "last_departure_secs": int(row["last_departure_secs"]),
            }

        # fallback: compute from stop_times/trips
        row2 = conn.execute(
            """
            SELECT st.departure_time, st.departure_secs
            FROM stop_times st
            JOIN trips t ON t.trip_id = st.trip_id
            WHERE st.route_id = ? AND t.service_id = ? AND st.stop_id = ?
            ORDER BY st.departure_secs DESC
            LIMIT 1
            """,
            (route_id, service_id, stop_id),
        ).fetchone()

        if not row2:
            return None

        return {
            "stop_id": stop_id,
            "stop_name": stop_name,
            "last_departure_time": row2["departure_time"],
            "last_departure_secs": int(row2["departure_secs"]),
        }
