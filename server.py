# server.py
from flask import Flask, jsonify, request
from flask_cors import CORS
import os, json

import rts_api  # your Clever/BusTime helper

# --- OpenAI setup ---
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    OPENAI_OK = True
except Exception:
    client = None
    OPENAI_OK = False

app = Flask(__name__)
CORS(app)  # allow browser JS from your Hostinger/Render site to call this API


# -----------------------
# Utility: agent runner
# -----------------------
def run_agent(user_text: str) -> str:
    """
    Chat agent that can either answer directly or call a tool to fetch live predictions.
    """
    if not OPENAI_OK:
        return "OpenAI is not configured on the server."

    system_prompt = (
        "You are RTS Assistant for Gainesville’s Regional Transit System.\n"
        "- Be concise and bilingual if user speaks Spanish; otherwise reply in English.\n"
        "- When the user asks for ETAs/arrivals, use the `get_predictions` tool with a stop_id.\n"
        "- If the message lacks a stop_id, politely ask for it (and explain how to find it in the app).\n"
        "- When you DO have predictions, return a short list like ‘Route X to DEST in N min’ (max 3-5)."
    )

    # Define the tool schema we expose to the model
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_predictions",
                "description": "Get live arrivals for a stop_id from RTS BusTime.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "stop_id": {
                            "type": "string",
                            "description": "Numeric or string stop identifier, e.g. '1205'."
                        },
                        "top": {
                            "type": "integer",
                            "description": "Max number of arrivals to include (default 3, max 5).",
                            "minimum": 1,
                            "maximum": 5
                        }
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

    # First call: let the model decide whether to call a tool
    first = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=0.3,
    )

    msg = first.choices[0].message

    # If the model requested a tool, execute it and send results back
    if msg.tool_calls:
        for call in msg.tool_calls:
            if call.type == "function" and call.function.name == "get_predictions":
                # Parse arguments the model asked us to use
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                stop_id = str(args.get("stop_id", "")).strip()
                top = args.get("top", 3)
                if not stop_id:
                    # If somehow the tool call had no stop_id, let the model handle asking
                    messages.append(msg)
                    break

                # Call your Python helper directly (no extra HTTP)
                data = rts_api.get_predictions(stop_id, top=top)

                # Normalize predictions for the model
                preds_raw = data.get("prd", [])
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
                messages.append(msg)  # the assistant message with tool call
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": "get_predictions",
                    "content": tool_content,
                })

        # Second call: model sees the tool result and writes the final answer
        second = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            temperature=0.2,
        )
        return second.choices[0].message.content.strip()

    # No tool call, just return the direct answer
    return msg.content.strip()


# -----------------------
# Existing endpoints
# -----------------------
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "rts-backend", "openai": OPENAI_OK})


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


@app.route("/api/directions")
def api_directions():
    route_id = request.args.get("route_id", "")
    data = rts_api.get_directions(route_id)
    dirs_raw = data.get("directions", [])
    cleaned = []
    for d in dirs_raw:
        dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid")
        dir_name = d.get("name") or d.get("dir") or d.get("dirName") or d.get("dirname")
        cleaned.append({"id": dir_id, "name": dir_name})
    return jsonify({"directions": cleaned})


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


@app.route("/api/predictions")
def api_predictions():
    stop_id = request.args.get("stop_id", "")
    top = request.args.get("top", type=int)
    data = rts_api.get_predictions(stop_id, top=top)
    preds_raw = data.get("prd", [])
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
