# server.py
from flask import Flask, jsonify, request
from flask_cors import CORS
import os, re

import rts_api  # your Clever/BusTime wrapper

# ---------- Stop ID helpers (4-digit rule) ----------
def to_stop4(value: str | int | None) -> str | None:
    """
    Normalize any incoming 'stop id' to a 4-digit numeric string.
    Examples:
      '1' -> '0001'
      '23' -> '0023'
      '0023' -> '0023'
      'AB-123' -> '0123'
      '12345' -> None (too many digits)
      'xx' -> None (no digits)
    """
    if value is None:
        return None
    digits = ''.join(ch for ch in str(value) if ch.isdigit())
    if 1 <= len(digits) <= 4:
        return digits.zfill(4)
    return None

def is_stop4(s: str | None) -> bool:
    return isinstance(s, str) and len(s) == 4 and s.isdigit()

def extract_stop4_from_text(text: str) -> str | None:
    """
    Find the first 1–4 digit number in free text and normalize to 4-digit.
    """
    m = re.search(r'\b(\d{1,4})\b', text or '')
    if not m:
        return None
    return to_stop4(m.group(1))

# ---------- App ----------
app = Flask(__name__)
CORS(app)

# Optional OpenAI (for general Q&A fallback in /api/agent)
client = None
try:
    if os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        client = OpenAI()
except Exception:
    client = None

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "rts-backend", "openai": bool(client)})

# ------------ Core data endpoints ------------
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
        # handle various shapes from different BusTime deployments
        dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d.get("name") or d.get("dirname")
        dir_name = d.get("name") or d.get("dir") or d.get("dirName") or d.get("dirname") or dir_id
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
        sid4 = to_stop4(s.get("stpid"))
        if not is_stop4(sid4):
            # filter out anything that can't be a 4-digit stop
            continue
        cleaned.append({
            "id": sid4,
            "name": s.get("stpnm"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
        })
    return jsonify({"stops": cleaned})

@app.route("/api/predictions")
def api_predictions():
    raw = request.args.get("stop_id", "")
    sid4 = to_stop4(raw)
    if not is_stop4(sid4):
        return jsonify({"error": "invalid_stop_id", "detail": "Provide a 4-digit numeric stop id (e.g., 0001, 0234)."}), 400

    top_param = request.args.get("top")
    try:
        top = int(top_param) if top_param is not None else None
    except ValueError:
        top = None

    data = rts_api.get_predictions(sid4, top=top)
    preds_raw = data.get("prd", [])
    cleaned = []
    for p in preds_raw:
        cleaned.append({
            "route": p.get("rt"),
            "direction": p.get("rtdir"),
            "destination": p.get("des"),
            "minutes": p.get("prdctdn"),    # "DUE" or "5"
            "vehicle_id": p.get("vid"),
            "arrival_time": p.get("prdtm"), # timestamp string
            "delayed": p.get("dly"),
            "stop_id": sid4,
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

# ------------ Convenience utilities ------------
@app.route("/api/validate_stop")
def api_validate_stop():
    raw = request.args.get("stop_id", "")
    sid4 = to_stop4(raw)
    return jsonify({"ok": is_stop4(sid4), "stop_id4": sid4})

@app.route("/api/stops_anydir")
def api_stops_anydir():
    """Pick the first available direction for a route, then list stops. Always JSON."""
    route_id = request.args.get("route_id", "")
    try:
        dirs_data = rts_api.get_directions(route_id)
        dirs_raw = dirs_data.get("directions", [])
        if not dirs_raw:
            return jsonify({"route_id": route_id, "direction": None, "stops": []})
        # prefer 'id' or 'dir'; fallback to value
        dir_id = None
        first = dirs_raw[0]
        dir_id = first.get("id") or first.get("dir") or first.get("dirId") or first.get("dirid") or first.get("name") or first.get("dirname")
        if not dir_id:
            # if API returns strings
            if isinstance(first, str):
                dir_id = first
        if not dir_id:
            return jsonify({"route_id": route_id, "direction": None, "stops": []})

        stops_data = rts_api.get_stops(route_id, dir_id)
        stops_raw = stops_data.get("stops", [])
        cleaned = []
        for s in stops_raw:
            sid4 = to_stop4(s.get("stpid"))
            if not is_stop4(sid4):
                continue
            cleaned.append({
                "id": sid4,
                "name": s.get("stpnm"),
                "lat": s.get("lat"),
                "lon": s.get("lon"),
            })
        return jsonify({"route_id": route_id, "direction": dir_id, "stops": cleaned})
    except Exception as e:
        # ensure JSON even on error
        return jsonify({"route_id": route_id, "error": str(e)}), 500

@app.route("/api/debug/directions_raw")
def api_debug_directions_raw():
    route_id = request.args.get("route_id", "")
    return jsonify(rts_api.get_directions(route_id))

# ------------ Simple Agent ------------
SYSTEM_PROMPT = (
    "You are the RTS Gainesville assistant. Be brief and helpful. "
    "If user mentions a stop ID, it must be 4 digits (e.g., 0001). "
    "If a stop seems invalid, ask them for the 4-digit stop id."
)

def format_predictions_answer(preds: list[dict], stop_id4: str, top_n: int = 3) -> str:
    if not preds:
        return f"No upcoming arrivals at stop {stop_id4} right now."
    lines = []
    for p in preds[:top_n]:
        mins = p.get("minutes")
        mins_txt = "due" if str(mins).upper() == "DUE" else f"in {mins} min"
        lines.append(f"Route {p.get('route')} → {p.get('destination')} ({p.get('direction')}), {mins_txt}.")
    return f"Next arrivals at stop {stop_id4}:\n" + "\n".join(lines)

@app.route("/api/agent", methods=["POST"])
def api_agent():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    # If the user message includes something that looks like a stop id, try the live predictions tool
    sid4 = extract_stop4_from_text(message)
    if is_stop4(sid4):
        try:
            # Parse a 'top N' hint if the user gave one
            m = re.search(r'\btop\s+(\d{1,2})\b', message, re.I)
            top = int(m.group(1)) if m else 3
            data = rts_api.get_predictions(sid4, top=top)
            preds = data.get("prd", [])
            return jsonify({"answer": format_predictions_answer(preds, sid4, top_n=top)})
        except Exception:
            # If the tool fails, fall back to a helpful message
            return jsonify({"answer": f"I couldn’t get live arrivals for stop {sid4}. Please try again shortly or confirm the 4-digit stop id."})

    # Otherwise: general Q&A via OpenAI if available
    if client:
        try:
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": message},
                ],
                temperature=0.2,
            )
            text = resp.choices[0].message.content.strip()
            return jsonify({"answer": text})
        except Exception:
            # If OpenAI has an issue, degrade gracefully
            pass

    # Final fallback if no OpenAI
    return jsonify({"answer": "RTS is Gainesville’s public transit. For live ETAs, send the 4-digit stop ID (e.g., 0001)."})
