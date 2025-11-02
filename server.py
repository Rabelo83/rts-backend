# server.py
from flask import Flask, jsonify, request
from flask_cors import CORS
import os, json

import rts_api  # Clever/BusTime helper

# --- OpenAI setup ---
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    OPENAI_OK = True
except Exception:
    client = None
    OPENAI_OK = False

app = Flask(__name__)
CORS(app)

# -----------------------
# Agent runner (unchanged)
# -----------------------
def run_agent(user_text: str) -> str:
    if not OPENAI_OK:
        return "OpenAI is not configured on the server."

    system_prompt = (
        "You are RTS Assistant for Gainesville’s Regional Transit System.\n"
        "- Be concise and bilingual if user speaks Spanish; otherwise reply in English.\n"
        "- When the user asks for ETAs/arrivals, use the `get_predictions` tool with a stop_id.\n"
        "- If the message lacks a stop_id, politely ask for it (and explain how to find it in the app).\n"
        "- When you DO have predictions, return a short list like ‘Route X to DEST in N min’ (max 3-5)."
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_predictions",
                "description": "Get live arrivals for a stop_id from RTS BusTime.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "stop_id": {"type": "string","description": "e.g. '1205'"},
                        "top": {"type": "integer","description": "Max results (default 3, max 5).","minimum": 1,"maximum": 5}
                    },
                    "required": ["stop_id"]
                }
            }
        }
    ]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    first = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=0.3,
    )

    msg = first.choices[0].message

    if msg.tool_calls:
        for call in msg.tool_calls:
            if call.type == "function" and call.function.name == "get_predictions":
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                stop_id = str(args.get("stop_id", "")).strip()
                top = args.get("top", 3)
                if not stop_id:
                    messages.append(msg)
                    break

                data = rts_api.get_predictions(stop_id, top=top)
                preds_raw = data.get("prd", []) or []
                cleaned = []
                for p in preds_raw[: max(1, min(int(top), 5))]:
                    cleaned.append({
                        "route": p.get("rt"),
                        "direction": p.get("rtdir"),
                        "destination": p.get("des"),
                        "minutes": p.get("prdctdn"),
                        "vehicle_id": p.get("vid"),
                        "arrival_time": p.get("prdtm"),
                        "delayed": p.get("dly"),
                    })

                tool_content = json.dumps({"predictions": cleaned}, ensure_ascii=False)
                messages.append(msg)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": "get_predictions",
                    "content": tool_content,
                })

        second = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            temperature=0.2,
        )
        return second.choices[0].message.content.strip()

    return msg.content.strip()

# -----------------------
# Health
# -----------------------
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "rts-backend", "openai": OPENAI_OK})

# -----------------------
# Core endpoints
# -----------------------
@app.route("/api/routes")
def api_routes():
    data = rts_api.get_routes()
    routes_raw = data.get("routes", []) or []
    cleaned = []
    for r in routes_raw:
        cleaned.append({
            "id": r.get("rt") or r.get("id"),
            "name": r.get("rtnm") or r.get("name"),
            "color": r.get("rtclr"),
        })
    return jsonify({"routes": cleaned})

@app.route("/api/directions")
def api_directions():
    route_id = request.args.get("route_id", "")
    data = rts_api.get_directions(route_id)
    dirs_raw = data.get("directions", []) or []

    cleaned = []
    if isinstance(dirs_raw, list):
        for d in dirs_raw:
            if isinstance(d, dict):
                # handle { "dir": "NORTHBOUND" } or { "id":"NORTHBOUND","name":"Northbound" }
                dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid")
                dir_name = d.get("name") or d.get("dir") or d.get("dirName") or d.get("dirname") or dir_id
            elif isinstance(d, str):
                # handle ["NORTHBOUND","SOUTHBOUND"]
                dir_id = d
                dir_name = d.title()
            else:
                dir_id = None
                dir_name = None
            if dir_id:
                cleaned.append({"id": dir_id, "name": dir_name})
    return jsonify({"directions": cleaned})

@app.route("/api/stops")
def api_stops():
    route_id = request.args.get("route_id", "")
    direction_id = request.args.get("direction_id", "")
    data = rts_api.get_stops(route_id, direction_id)
    stops_raw = data.get("stops", []) or []
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
    top = request.args.get("top", type=int)
    data = rts_api.get_predictions(stop_id, top=top)
    preds_raw = data.get("prd", []) or []
    cleaned = []
    for p in preds_raw:
        cleaned.append({
            "route": p.get("rt"),
            "direction": p.get("rtdir"),
            "destination": p.get("des"),
            "minutes": p.get("prdctdn"),
            "vehicle_id": p.get("vid"),
            "arrival_time": p.get("prdtm"),
            "delayed": p.get("dly"),
        })
    return jsonify({"predictions": cleaned})

@app.route("/api/vehicles")
def api_vehicles():
    route_id = request.args.get("route_id", "")
    data = rts_api.get_vehicles(route_id)
    veh_raw = data.get("vehicle", []) or []
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

# -----------------------
# Convenience + Debug
# -----------------------
@app.route("/api/stops_anydir")
def api_stops_anydir():
    """
    Try common direction IDs until we find stops for the route.
    Returns { direction, stops:[...] } or {direction:null, stops:[]}
    """
    route_id = request.args.get("route_id", "")
    found = rts_api.find_first_working_direction_and_stops(route_id)
    if not found:
        return jsonify({"direction": None, "stops": []})
    # normalize stop shape (match /api/stops)
    cleaned = []
    for s in found.get("stops", []):
        cleaned.append({
            "id": s.get("stpid"),
            "name": s.get("stpnm"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
        })
    return jsonify({"direction": found.get("direction"), "stops": cleaned})

@app.route("/api/debug/directions_raw")
def debug_directions_raw():
    route_id = request.args.get("route_id", "")
    return jsonify(rts_api.get_directions_raw(route_id))

@app.route("/api/debug/stops_raw")
def debug_stops_raw():
    route_id = request.args.get("route_id", "")
    direction_id = request.args.get("direction_id", "")
    return jsonify(rts_api.get_stops_raw(route_id, direction_id))

# -------- Agent endpoint --------
@app.route("/api/agent", methods=["POST"])
def api_agent():
    if not OPENAI_OK:
        return jsonify({"error": "OpenAI not configured"}), 500
    payload = request.get_json(silent=True) or {}
    user_text = str(payload.get("message", "")).strip()
    if not user_text:
        return jsonify({"error": "message is required"}), 400
    answer = run_agent(user_text)
    return jsonify({"answer": answer})

# Local debug only
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
