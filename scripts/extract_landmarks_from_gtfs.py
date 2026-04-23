#!/usr/bin/env python3
"""
Extract likely landmark destinations from the GTFS stops table and print a
YAML-ready block for agency_config.yaml common_destinations.landmarks.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from pathlib import Path


_DB_CANDIDATES = [
    Path("Backend Basics/db/rts_gtfs.sqlite"),
    Path("data/rts_gtfs.sqlite"),
]

_DIR_SUFFIX_RE = re.compile(r"\b(?:NB|SB|EB|WB|Northbound|Southbound|Eastbound|Westbound)\b", re.IGNORECASE)
_AT_RE = re.compile(r"\s+(?:@|at)\s+.*$", re.IGNORECASE)
_FILLER_RE = re.compile(
    r"\b(?:station|stop|platform|center|centre|campus|garage|park(?:ing)?|lot)\b",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def _open_db() -> sqlite3.Connection:
    for candidate in _DB_CANDIDATES:
        if candidate.exists():
            return sqlite3.connect(candidate)
    raise SystemExit("No GTFS SQLite DB found in Backend Basics/db or data/")


def _looks_like_intersection(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return True
    if "&" in text:
        return True
    if "@" in text and re.search(r"\b(?:st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|way|ter|terrace)\b", text, re.IGNORECASE):
        return True
    return False


def _normalize_landmark(name: str) -> str:
    text = (name or "").strip()
    text = _AT_RE.sub("", text)
    text = _DIR_SUFFIX_RE.sub("", text)
    text = _FILLER_RE.sub("", text)
    text = re.sub(r"\s*[-/]\s*", " ", text)
    text = re.sub(r"[()]", "", text)
    text = _WS_RE.sub(" ", text).strip(" ,-/")
    return text


def _alias_for(name: str) -> list[str]:
    alias = (name or "").strip().lower()
    aliases = {alias}
    shortened = re.sub(r"\b(?:transfer|transit)\b", "", alias)
    shortened = _WS_RE.sub(" ", shortened).strip(" ,-/")
    if shortened and shortened != alias:
        aliases.add(shortened)
    if " plaza" in alias:
        aliases.add(alias.replace(" plaza", "").strip())
    return sorted(a for a in aliases if a)


def main() -> None:
    conn = _open_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT DISTINCT stop_name, COALESCE(stop_id_padded, stop_id) AS stop_id
        FROM stops
        WHERE stop_name IS NOT NULL AND TRIM(stop_name) != ''
        ORDER BY stop_name
        """
    ).fetchall()
    conn.close()

    grouped: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        stop_name = row["stop_name"]
        stop_id = str(row["stop_id"] or "").zfill(4)
        if _looks_like_intersection(stop_name):
            continue
        canonical = _normalize_landmark(stop_name)
        if not canonical or _looks_like_intersection(canonical):
            continue
        if len(canonical) < 4:
            continue
        grouped[canonical].add(stop_id)

    for canonical in sorted(grouped):
        aliases = _alias_for(canonical)
        stops = sorted(grouped[canonical])
        print(f'"{canonical}":')
        print(f"  stops: {stops}")
        print(f"  aliases: {aliases}")


if __name__ == "__main__":
    main()
