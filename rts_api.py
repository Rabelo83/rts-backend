# rts_api.py
import requests
from config import API_KEY, RTPIDATAFEED, BASE_API

def call_bustime(endpoint, extra_params=None):
    """
    Low-level helper to call Clever BusTime v3 endpoints.
    Returns the 'bustime-response' object as a Python dict.
    """
    if extra_params is None:
        extra_params = {}

    params = {
        "key": API_KEY,
        "rtpidatafeed": RTPIDATAFEED,
        "format": "json",
        **extra_params
    }

    url = f"{BASE_API}/{endpoint}"
    resp = requests.get(url, params=params, timeout=12)
    resp.raise_for_status()
    data = resp.json()
    return data.get("bustime-response", {}) or {}

# ----- Raw passthroughs (for debugging) -----
def get_routes_raw():
    return call_bustime("getroutes")

def get_directions_raw(route_id: str):
    return call_bustime("getdirections", {"rt": route_id})

def get_stops_raw(route_id: str, direction_id: str):
    return call_bustime("getstops", {"rt": route_id, "dir": direction_id})

# ----- Normalized helpers your API uses -----
def get_routes():
    return get_routes_raw()

def get_directions(route_id: str):
    return get_directions_raw(route_id)

def get_stops(route_id: str, direction_id: str):
    return get_stops_raw(route_id, direction_id)

def get_predictions(stop_id: str, top: int | None = None):
    params = {"stpid": stop_id}
    if top is not None:
        params["top"] = top
    return call_bustime("getpredictions", params)

def get_vehicles(route_id: str):
    return call_bustime("getvehicles", {"rt": route_id})

# ----- Stop name helper (by route/direction scan) -----
def get_stop_name(route_id: str, stop_id: str) -> str | None:
    """
    Try to resolve a stop name from Bustime by scanning stops for route directions.
    """
    def _norm_stop(s: str | None) -> str | None:
        if not s:
            return None
        digits = "".join(ch for ch in str(s) if ch.isdigit())
        if not digits:
            return None
        if len(digits) > 4:
            digits = digits[-4:]
        return digits.zfill(4)

    try:
        dirs = get_directions_raw(route_id).get("directions", []) or []
        dir_ids = []
        for d in dirs:
            if isinstance(d, dict):
                dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d.get("direction")
            else:
                dir_id = d
            if dir_id:
                dir_ids.append(str(dir_id))
    except Exception:
        dir_ids = []

    if not dir_ids:
        dir_ids = COMMON_DIRECTIONS

    target = _norm_stop(stop_id)
    for d in dir_ids:
        try:
            stops = get_stops_raw(route_id, d).get("stops", []) or []
        except Exception:
            stops = []
        for s in stops:
            sid = _norm_stop(s.get("stpid"))
            if sid and sid == target:
                return (s.get("stpnm") or "").strip() or None

    return None

# ----- Convenience: try common direction labels until one works -----
COMMON_DIRECTIONS = [
    "NORTHBOUND","SOUTHBOUND","EASTBOUND","WESTBOUND",
    "INBOUND","OUTBOUND",
    "CW","CCW",  # loop routes
    "NB","SB","EB","WB"
]

def find_first_working_direction_and_stops(route_id: str, max_try: int = 10):
    """
    Try common direction IDs until getstops() returns any stops.
    Returns { 'direction': 'XXX', 'stops': [...] } or {} if none match.
    """
    tried = 0
    for d in COMMON_DIRECTIONS:
        if tried >= max_try:
            break
        data = get_stops_raw(route_id, d)
        stops = data.get("stops") or []
        if isinstance(stops, list) and len(stops) > 0:
            return {"direction": d, "stops": stops}
        tried += 1
    return {}
