"""
GTFS/Bustime stop resolution for the RTS transit assistant.
Provides:
  - get_predictions_cached     — cached real-time predictions
  - infer_routes_from_predictions — extract route info from predictions
  - suggest_stops_by_route     — fuzzy stop name search via Bustime API
  - _gtfs_resolve_stop_name    — resolve stop name text → stop_id via GTFS DB
  - route_serves_stop          — GTFS verification that a route serves a stop
"""
import os
import re
import sys
import sqlite3
import logging
from pathlib import Path

import rts_api

# Add utils to path for cache import
utils_path = str(Path(__file__).resolve().parents[1] / "utils")
if utils_path not in sys.path:
    sys.path.insert(0, utils_path)

from cache import prediction_cache

from routes.parsing_helpers import (
    normalize_stop_id,
    digits_only,
    guess_destination_hint,
    _normalize_place,
    expand_landmark_aliases,
)

logger = logging.getLogger(__name__)

GTFS_DB_PATH = Path(__file__).resolve().parents[1] / "Backend Basics" / "db" / "rts_gtfs.sqlite"
PREDICTION_CACHE_TTL = int(os.getenv("PREDICTION_CACHE_TTL", "20"))


# ── Cached predictions ────────────────────────────────────────────────────────

def get_predictions_cached(stop_id: str):
    """Get real-time predictions with LRU caching (TTL=20s default)."""
    key = f"predictions:{stop_id}"
    cached = prediction_cache.get(key)
    if cached is not None:
        return cached
    data = rts_api.get_predictions(stop_id)
    prediction_cache.set(key, data, ttl=PREDICTION_CACHE_TTL)
    return data


def infer_routes_from_predictions(stop_id: str) -> dict[str, dict]:
    """
    Returns {route: {"directions": set(), "destinations": set()}} from Bustime predictions.
    """
    if not stop_id:
        return {}
    try:
        data = get_predictions_cached(stop_id) or {}
        preds = data.get("prd", []) or []
    except Exception:
        return {}
    routes: dict[str, dict] = {}
    for p in preds:
        rt = str(p.get("rt") or "").strip()
        if not rt:
            continue
        entry = routes.setdefault(rt, {"directions": set(), "destinations": set()})
        rtdir = (p.get("rtdir") or "").strip()
        des = (p.get("des") or "").strip()
        if rtdir:
            entry["directions"].add(rtdir)
        if des:
            entry["destinations"].add(des)
    return routes


# ── Stop name tokenization & scoring ─────────────────────────────────────────

def _tokenize_for_stop_match(text: str) -> list[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    tokens = [x for x in t.split() if len(x) >= 3]
    drop = {"route", "rt", "stop", "bus", "eta", "at", "to", "from", "the", "and", "for",
            "ruta", "parada", "autobus", "autobús", "bus", "a", "de", "en", "el", "la"}
    return [x for x in tokens if x not in drop]


def _score_stop_name(stop_name: str, tokens: list[str]) -> int:
    nm = (stop_name or "").lower()
    score = 0
    for tok in tokens:
        if tok in nm:
            score += 2 if nm.startswith(tok) else 1
    return score


def _dedupe_stop_rows(rows) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for row in rows or []:
        stop_id = row["stop_id_padded"]
        if stop_id in seen:
            continue
        seen.add(stop_id)
        deduped.append({"stop_id": stop_id, "stop_name": row["stop_name"]})
    return deduped


# ── Bustime stop suggestion ───────────────────────────────────────────────────

def suggest_stops_by_route(route_id: str, message: str, limit: int = 8) -> list[dict]:
    route_id = digits_only(route_id or "")
    if not route_id:
        return []

    tokens = _tokenize_for_stop_match(message)
    hint = guess_destination_hint(message)
    if hint:
        tokens += _tokenize_for_stop_match(hint)
    tokens = list(dict.fromkeys(tokens))

    if not tokens:
        return []

    try:
        dirs_data = rts_api.get_directions(route_id) or {}
        dirs_raw = dirs_data.get("directions", []) or []
    except Exception:
        dirs_raw = []

    dir_ids: list[str] = []
    for d in dirs_raw:
        if isinstance(d, dict):
            dir_id = d.get("dir") or d.get("id") or d.get("direction") or d.get("dirId")
        else:
            dir_id = d
        if dir_id:
            dir_ids.append(str(dir_id))

    if not dir_ids:
        dir_ids = ["NORTHBOUND", "SOUTHBOUND", "EASTBOUND", "WESTBOUND", "INBOUND", "OUTBOUND"]

    seen: set[tuple[str, str]] = set()
    scored: list[tuple[int, dict]] = []

    for dir_id in dir_ids:
        try:
            stops_data = rts_api.get_stops(route_id, dir_id) or {}
            stops_raw = stops_data.get("stops", []) or []
        except Exception:
            stops_raw = []

        for s in stops_raw:
            if not isinstance(s, dict):
                continue
            sid = normalize_stop_id(s.get("stpid") or "")
            nm = (s.get("stpnm") or "").strip()
            if not sid or not nm:
                continue
            key = (sid, nm)
            if key in seen:
                continue
            seen.add(key)

            score = _score_stop_name(nm, tokens)
            if score > 0:
                scored.append((score, {"id": sid, "name": nm}))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]


# ── GTFS stop name resolution ─────────────────────────────────────────────────

def _gtfs_resolve_stop_name(route_id: str, stop_name: str) -> dict | None:
    """
    Resolve a textual stop name to a stop_id using GTFS data.
    Returns:
      {'stop_id': '0520', 'stop_name': 'Santa Fe'}        — single match, use directly
      {'candidates': [{'stop_id':..., 'stop_name':...}]}  — ambiguous, offer buttons
      None                                                  — no match
    """
    if not route_id or not stop_name:
        return None
    if not GTFS_DB_PATH.exists():
        return None
    try:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(GTFS_DB_PATH))
        conn.row_factory = _sqlite3.Row
        try:
            variants = expand_landmark_aliases(stop_name) or [stop_name]

            # 1. LIKE search scoped to the route
            like_rows = []
            for variant in variants:
                like_rows.extend(conn.execute(
                    """
                    SELECT DISTINCT s.stop_id_padded, s.stop_name
                    FROM stops s
                    JOIN stop_times st ON st.stop_id = s.stop_id
                    JOIN trips t ON t.trip_id = st.trip_id
                    JOIN routes r ON r.route_id = t.route_id
                    WHERE TRIM(r.route_short_name) = ?
                      AND LOWER(TRIM(s.stop_name)) LIKE LOWER(?)
                    ORDER BY length(s.stop_name), s.stop_name
                    """,
                    (str(route_id), f"%{variant.strip()}%"),
                ).fetchall())
            rows = _dedupe_stop_rows(like_rows)
            if len(rows) == 1:
                return {"stop_id": rows[0]["stop_id"], "stop_name": rows[0]["stop_name"]}
            if len(rows) > 1:
                return {"candidates": rows[:5]}

            # 2. Fuzzy token search via fuzzy_lookup table
            fuzzy_rows = []
            for variant in variants:
                norm = variant.lower().strip()
                pattern = "%" + "%".join(norm.split()) + "%"
                fuzzy_rows.extend(conn.execute(
                    """
                    SELECT DISTINCT s.stop_id_padded, s.stop_name
                    FROM fuzzy_lookup f
                    JOIN stops s ON s.stop_id = f.entity_id
                    JOIN stop_times st ON st.stop_id = s.stop_id
                    JOIN trips t ON t.trip_id = st.trip_id
                    JOIN routes r ON r.route_id = t.route_id
                    WHERE f.entity_type = 'stop'
                      AND f.normalized LIKE ?
                      AND TRIM(r.route_short_name) = ?
                    ORDER BY length(s.stop_name), s.stop_name
                    """,
                    (pattern, str(route_id)),
                ).fetchall())
            rows2 = _dedupe_stop_rows(fuzzy_rows)
            if len(rows2) == 1:
                return {"stop_id": rows2[0]["stop_id"], "stop_name": rows2[0]["stop_name"]}
            if len(rows2) > 1:
                return {"candidates": rows2[:5]}

            return None
        finally:
            conn.close()
    except Exception:
        return None


def resolve_stop_global(stop_name: str) -> dict | None:
    """
    Resolve a textual stop name to a stop_id using GTFS data, without route constraint.
    Useful when the user mentions a landmark but no specific route (e.g. 'Next bus at Rosa Parks').
    Returns:
      {'stop_id': '0001', 'stop_name': '...'}        — single match, use directly
      {'candidates': [{'stop_id':..., 'stop_name':...}]}  — ambiguous, offer buttons
      None                                                  — no match
    """
    if not stop_name or not GTFS_DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(GTFS_DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            variants = expand_landmark_aliases(stop_name) or [stop_name]

            like_rows = []
            for variant in variants:
                like_rows.extend(conn.execute(
                    """
                    SELECT DISTINCT stop_id_padded, stop_name
                    FROM stops
                    WHERE LOWER(TRIM(stop_name)) LIKE LOWER(?)
                    ORDER BY length(stop_name), stop_name
                    """,
                    (f"%{variant.strip()}%",),
                ).fetchall())
            rows = _dedupe_stop_rows(like_rows)
            if len(rows) == 1:
                return {"stop_id": rows[0]["stop_id"], "stop_name": rows[0]["stop_name"]}
            if len(rows) > 1:
                return {"candidates": rows[:5]}

            # Fuzzy token search via fuzzy_lookup table
            fuzzy_rows = []
            for variant in variants:
                norm = variant.lower().strip()
                pattern = "%" + "%".join(norm.split()) + "%"
                fuzzy_rows.extend(conn.execute(
                    """
                    SELECT DISTINCT s.stop_id_padded, s.stop_name
                    FROM fuzzy_lookup f
                    JOIN stops s ON s.stop_id = f.entity_id
                    WHERE f.entity_type = 'stop'
                      AND f.normalized LIKE ?
                    ORDER BY length(s.stop_name), s.stop_name
                    """,
                    (pattern,),
                ).fetchall())
            rows2 = _dedupe_stop_rows(fuzzy_rows)
            if len(rows2) == 1:
                return {"stop_id": rows2[0]["stop_id"], "stop_name": rows2[0]["stop_name"]}
            if len(rows2) > 1:
                return {"candidates": rows2[:5]}

            return None
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("resolve_stop_global failed: %s", exc)
        return None


def route_serves_stop(route_id: str, stop_id_padded: str) -> bool:
    if not route_id or not stop_id_padded:
        return False
    if not GTFS_DB_PATH.exists():
        return True  # avoid blocking if GTFS isn't available
    try:
        conn = sqlite3.connect(GTFS_DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT 1
            FROM stops s
            JOIN stop_times st ON st.stop_id = s.stop_id
            JOIN trips t ON t.trip_id = st.trip_id
            JOIN routes r ON r.route_id = t.route_id
            WHERE TRIM(r.route_short_name) = ?
              AND s.stop_id_padded = ?
            LIMIT 1
            """,
            (str(route_id), str(stop_id_padded)),
        ).fetchone()
        return bool(row)
    except Exception:
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass
