"""
utils/geocoding.py
Abstracted geocoding + autocomplete.

Provider is selected via GEOCODING_PROVIDER env var:
  nominatim  (default, free, OpenStreetMap)
  google     (requires GOOGLE_GEOCODING_KEY)
  mapbox     (requires MAPBOX_TOKEN)

Swap providers with zero frontend/routing code changes.
"""
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from agency_config import get_geocoding_bbox, get_city_hint

PROVIDER = os.getenv("GEOCODING_PROVIDER", "nominatim")
GOOGLE_KEY = os.getenv("GOOGLE_GEOCODING_KEY", "")
MAPBOX_KEY = os.getenv("MAPBOX_TOKEN", "")

# Agency service-area bounding box and city hint — read from agency_config.yaml
_bbox = get_geocoding_bbox()            # [W, S, E, N]
_BBOX = tuple(_bbox)                   # (-82.55, 29.55, -82.10, 29.85) for Gainesville
_VIEWBOX = f"{_BBOX[0]},{_BBOX[3]},{_BBOX[2]},{_BBOX[1]}"  # Nominatim: W,N,E,S
_CITY = get_city_hint()                # e.g. "Gainesville FL"

_CACHE: dict = {}
_CACHE_TTL = timedelta(hours=24)
_TIMEOUT = 5


# ── cache helpers ─────────────────────────────────────────────────────────────

def _key(tag: str, query: str) -> str:
    return hashlib.md5(f"{tag}:{query.lower()}".encode()).hexdigest()


def _get(k: str):
    entry = _CACHE.get(k)
    if entry and datetime.now() < entry[1]:
        return entry[0]
    _CACHE.pop(k, None)
    return None


def _put(k: str, value):
    _CACHE[k] = (value, datetime.now() + _CACHE_TTL)


# ── public API ────────────────────────────────────────────────────────────────

def geocode(query: str) -> dict | None:
    """Return {lat, lon, formatted_address} or None."""
    k = _key("geo", query)
    cached = _get(k)
    if cached is not None:
        return cached

    fn = _GEOCODERS.get(PROVIDER, _nominatim_geocode)
    result = fn(query)
    _put(k, result)
    return result


def autocomplete(query: str) -> list[dict]:
    """Return list of {display, lat, lon} suggestions (max 5)."""
    if len(query.strip()) < 3:
        return []
    k = _key("ac", query)
    cached = _get(k)
    if cached is not None:
        return cached

    fn = _AUTOCOMPLETE.get(PROVIDER, _nominatim_autocomplete)
    results = fn(query)
    _put(k, results)
    return results


# ── Nominatim ─────────────────────────────────────────────────────────────────

def _nominatim_geocode(query: str) -> dict | None:
    params = urllib.parse.urlencode({
        "q": f"{query}, {_CITY}",
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
        "bounded": 1,
        "viewbox": _VIEWBOX,
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RTS-Transit-Assistant/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read())
        if data:
            return {
                "lat": float(data[0]["lat"]),
                "lon": float(data[0]["lon"]),
                "formatted_address": _shorten(data[0].get("display_name", query)),
            }
    except Exception:
        pass
    return None


def _nominatim_autocomplete(query: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "q": f"{query}, {_CITY}",
        "format": "json",
        "limit": 5,
        "countrycodes": "us",
        "bounded": 1,
        "viewbox": _VIEWBOX,
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RTS-Transit-Assistant/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read())
        return [
            {"display": _shorten(item.get("display_name", "")),
             "lat": float(item["lat"]),
             "lon": float(item["lon"])}
            for item in data
        ]
    except Exception:
        return []


# ── Google ────────────────────────────────────────────────────────────────────

def _google_geocode(query: str) -> dict | None:
    if not GOOGLE_KEY:
        return _nominatim_geocode(query)
    params = urllib.parse.urlencode({
        "address": f"{query}, {_CITY}",
        "key": GOOGLE_KEY,
    })
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
            data = json.loads(r.read())
        if data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return {
                "lat": loc["lat"],
                "lon": loc["lng"],
                "formatted_address": data["results"][0]["formatted_address"],
            }
    except Exception:
        pass
    return None


def _google_autocomplete(query: str) -> list[dict]:
    # Google Places Autocomplete requires a separate endpoint + place_id → geocode step
    # For now, use geocode as single suggestion
    result = _google_geocode(query)
    return [{"display": result["formatted_address"], "lat": result["lat"], "lon": result["lon"]}] if result else []


# ── Mapbox ────────────────────────────────────────────────────────────────────

def _mapbox_geocode(query: str) -> dict | None:
    if not MAPBOX_KEY:
        return _nominatim_geocode(query)
    q = urllib.parse.quote(f"{query} {_CITY}")
    bbox = f"{_BBOX[0]},{_BBOX[1]},{_BBOX[2]},{_BBOX[3]}"
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{q}.json?access_token={MAPBOX_KEY}&bbox={bbox}&limit=1&country=us"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
            data = json.loads(r.read())
        if data.get("features"):
            coords = data["features"][0]["center"]
            return {
                "lat": coords[1],
                "lon": coords[0],
                "formatted_address": data["features"][0]["place_name"],
            }
    except Exception:
        pass
    return None


def _mapbox_autocomplete(query: str) -> list[dict]:
    if not MAPBOX_KEY:
        return _nominatim_autocomplete(query)
    q = urllib.parse.quote(f"{query} {_CITY}")
    bbox = f"{_BBOX[0]},{_BBOX[1]},{_BBOX[2]},{_BBOX[3]}"
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{q}.json?access_token={MAPBOX_KEY}&bbox={bbox}&limit=5&country=us"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
            data = json.loads(r.read())
        return [
            {"display": f["place_name"], "lat": f["center"][1], "lon": f["center"][0]}
            for f in data.get("features", [])
        ]
    except Exception:
        return []


# ── helpers ───────────────────────────────────────────────────────────────────

def _shorten(display_name: str) -> str:
    """Trim Nominatim's verbose display_name to 3 parts for mobile."""
    parts = [p.strip() for p in display_name.split(",")]
    return ", ".join(parts[:3])


_GEOCODERS = {"nominatim": _nominatim_geocode, "google": _google_geocode, "mapbox": _mapbox_geocode}
_AUTOCOMPLETE = {"nominatim": _nominatim_autocomplete, "google": _google_autocomplete, "mapbox": _mapbox_autocomplete}
