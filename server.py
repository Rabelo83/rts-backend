from flask import Flask, jsonify, request
from flask_cors import CORS
import rts_api

app = Flask(__name__)
CORS(app)  # allow browser JS from your Hostinger site to call this API

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "rts-backend"})

@app.route("/api/routes")
def api_routes():
    data = rts_api.get_routes()
    # BusTime typically returns { "routes": [ { "rt": "5", "rtnm": "...", "rtclr": "#xxxxxx", ... }, ... ] }
    routes_raw = data.get("routes", [])
    cleaned = []
    for r in routes_raw:
        cleaned.append({
            "id": r.get("rt"),
            "name": r.get("rtnm"),
            "color": r.get("rtclr"),
        })
    return jsonify({"routes": cleaned})

@app.route("/api/directions")
def api_directions():
    route_id = request.args.get("route_id", "")
    data = rts_api.get_directions(route_id)

    # BusTime returns something like:
    # "directions": [
    #   { "id": "NORTHBOUND", "name": "Northbound" }
    # ]
    #
    # Some systems instead return { "dir": "NORTHBOUND", "name": "Northbound" }
    # We'll normalize both.
    dirs_raw = data.get("directions", [])
    cleaned = []
    for d in dirs_raw:
        dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid")
        dir_name = d.get("name") or d.get("dir") or d.get("dirName") or d.get("dirname")
        cleaned.append({
            "id": dir_id,
            "name": dir_name,
        })
    return jsonify({"directions": cleaned})

@app.route("/api/stops")
def api_stops():
    route_id = request.args.get("route_id", "")
    direction_id = request.args.get("direction_id", "")

    data = rts_api.get_stops(route_id, direction_id)
    # "stops": [
    #   { "stpid": "1234", "stpnm": "BUTLER PLAZA", "lat": 29.x, "lon": -82.x }
    # ]
    stops_raw = data.get("stops", [])
    cleaned = []
    for s in stops_raw:
        cleaned.append({
            "id": s.get("stpid"),
            "name": s.get("stpnm"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
        })
    return jsonify({"stops": cleaned})

@app.route("/api/predictions")
def api_predictions():
    stop_id = request.args.get("stop_id", "")
    data = rts_api.get_predictions(stop_id)

    # Predictions usually come back in "prd": [ {...}, {...} ]
    preds_raw = data.get("prd", [])
    cleaned = []
    for p in preds_raw:
        cleaned.append({
            "route": p.get("rt"),
            "direction": p.get("rtdir"),
            "destination": p.get("des"),
            "minutes": p.get("prdctdn"),   # "5", "12", "DUE"
            "vehicle_id": p.get("vid"),
            "arrival_time": p.get("prdtm"), # timestamp string
            "delayed": p.get("dly"),
        })
    return jsonify({"predictions": cleaned})

@app.route("/api/vehicles")
def api_vehicles():
    route_id = request.args.get("route_id", "")
    data = rts_api.get_vehicles(route_id)

    # vehicles often come back under "vehicle": [ { "vid": "...", "lat": 29..., "lon": -82..., "hdg": 90, ... } ]
    veh_raw = data.get("vehicle", [])
    cleaned = []
    for v in veh_raw:
        cleaned.append({
            "vehicle_id": v.get("vid"),
            "lat": v.get("lat"),
            "lon": v.get("lon"),
            "heading": v.get("hdg"),
            "speed": v.get("spd"),
            "route": v.get("rt"),
            "destination": v.get("des"),
            "delayed": v.get("dly"),
        })
    return jsonify({"vehicles": cleaned})

# Render will run this with gunicorn (see render.yaml)
# Running directly for local debug:
if __name__ == "__main__":
    # DO NOT use this in production, this is for local test only
    app.run(host="0.0.0.0", port=5000, debug=True)
