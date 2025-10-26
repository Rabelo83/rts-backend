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
    resp = requests.get(url, params=params, timeout=10)

    # If upstream errors, raise for debugging
    resp.raise_for_status()
    data = resp.json()

    # BusTime wraps actual payload in "bustime-response"
    return data.get("bustime-response", {})

def get_routes():
    return call_bustime("getroutes")

def get_directions(route_id: str):
    return call_bustime("getdirections", {
        "rt": route_id
    })

def get_stops(route_id: str, direction_id: str):
    return call_bustime("getstops", {
        "rt": route_id,
        "dir": direction_id
    })

def get_predictions(stop_id: str, top: int | None = None):
    params = { "stpid": stop_id }
    if top is not None:
        params["top"] = top
    return call_bustime("getpredictions", params)

def get_vehicles(route_id: str):
    return call_bustime("getvehicles", {
        "rt": route_id
    })
