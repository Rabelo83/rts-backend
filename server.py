from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import rts_api

# --- OpenAI client ---
from openai import OpenAI
# Read key from environment; set OPENAI_API_KEY on Render
_openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=_openai_key) if _openai_key else None

app = Flask(__name__)
CORS(app)  # allow browser JS from your site(s) to call this API


# ------------------------
# Health
# ------------------------
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "rts-backend", "openai": bool(client)})


# ------------------------
# RTS: Routes
# ------------------------
@app.route("/api/routes")
def api_routes():
    data = rts_api.get_routes()
    routes_raw = data.get("routes", [])
    cleaned = []
    for r in routes_raw:
        cleaned.append({
            "id": r.get("rt"),
            "name": r.get("rtnm"),
            "color": r.get("rtclr"),
        })
    return jsonify({"routes": cleaned})


# ------------------------
# RTS: Directions
# ------------------------
@app.route("/api/directions")
def api_directions():
    route_id = request.args.get("route_id", "")
    data = rts_api.get_directions(route_id)

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


# ------------------------
# RTS: Stops
# ------------------------
@app.route("/api/stops")
def api_stops():
    route_id = request.args.get("route_id", "")
    direction_id = request.args.get("direction_id", "")

    data = rts_api.get_stops(route_id, direction_id)
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


# ------------------------
# RTS: Predictions
# ------------------------
@app.route("/api/predictions")
def api_predictions():
    stop_id = request.args.get("stop_id", "")
    data = rts_api.get_predictions(stop_id)

    preds_raw = data.get("prd", [])
    cleaned = []
    for p in preds_raw:
        cleaned.append({
            "route": p.get("rt"),
            "direction": p.get("rtdir"),
            "destination": p.get("des"),
            "minutes": p.get("prdctdn"),   # string like "5", "12", or "DUE"
            "vehicle_id": p.get("vid"),
            "arrival_time": p.get("prdtm"),
            "delayed": p.get("dly"),
        })
    return jsonify({"predictions": cleaned})


# ------------------------
# RTS: Vehicles
# ------------------------
@app.route("/api/vehicles")
def api_vehicles():
    route_id = request.args.get("route_id", "")
    data = rts_api.get_vehicles(route_id)

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


# ------------------------
# Assistant (OpenAI) - basic
# ------------------------
@app.post("/api/agent")
def agent():
    if client is None:
        return jsonify({"error": "OpenAI not configured. Set OPENAI_API_KEY in environment."}), 500

    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "message is required"}), 400

    # Simple system prompt (we’ll extend with tools in the next step)
    system = (
        "You are RTS Gainesville’s assistant. Be concise and helpful. "
        "If the user asks for ETAs, ask for a stop ID or route+stop. "
        "If the user writes in Spanish, answer in Spanish; otherwise use English."
    )

    try:
        # Chat Completions for broad compatibility
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
        )
        answer = resp.choices[0].message.content
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------
# Local dev only
# ------------------------
if __name__ == "__main__":
    # DO NOT use in production; Render will run gunicorn
    app.run(host="0.0.0.0", port=5000, debug=True)
