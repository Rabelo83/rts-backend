# rts_api.py
import requests
from config import API_KEYS, RTPIDATAFEED, BASE_API


def _is_transaction_limit(data):
    errors = data.get("error") if isinstance(data, dict) else None
    if not errors:
        return False
    if not isinstance(errors, list):
        errors = [errors]
    for err in errors:
        if isinstance(err, dict):
            msg = str(err.get("msg") or err.get("message") or "")
        else:
            msg = str(err)
        if "transaction limit" in msg.lower() or "limit" in msg.lower():
            return True
    return False

def call_bustime(endpoint, extra_params=None):
    """
    Low-level helper to call Clever BusTime v3 endpoints.
    Returns the 'bustime-response' object as a Python dict.
    """
    if extra_params is None:
        extra_params = {}

    url = f"{BASE_API}/{endpoint}"
    last_payload = {}
    for api_key in API_KEYS:
        params = {
            "key": api_key,
            "rtpidatafeed": RTPIDATAFEED,
            "format": "json",
            **extra_params
        }

        resp = requests.get(url, params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        payload = data.get("bustime-response", {}) or {}
        last_payload = payload
        if _is_transaction_limit(payload):
            continue
        return payload
    return last_payload

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

# ----- Route ID resolver -----
def resolve_route_id(route_id: str) -> str | None:
    """
    Resolve a rider-entered route number to the exact Bustime route id (rt).
    Matches by numeric value (handles leading zeros).
    """
    rid = str(route_id or "").strip()
    if not rid:
        return None
    rid_digits = re.sub(r"[^0-9]", "", rid)
    if not rid_digits:
        return rid

    try:
        routes = get_routes_raw().get("routes", []) or []
    except Exception:
        return rid

    for r in routes:
        rt = (r.get("rt") or "").strip()
        rt_digits = re.sub(r"[^0-9]", "", rt)
        if rt_digits and int(rt_digits) == int(rid_digits):
            return rt

    return rid

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

    resolved_rt = resolve_route_id(route_id) or route_id
    try:
        dirs = get_directions_raw(resolved_rt).get("directions", []) or []
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
            stops = get_stops_raw(resolved_rt, d).get("stops", []) or []
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
