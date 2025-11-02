from flask import Flask, jsonify, request
from flask_cors import CORS
import re, os

import rts_api
from config import API_KEY
from openai import OpenAI
client = OpenAI()

import web_index
import webqa

app = Flask(__name__)
CORS(app)

# ---------- helpers ----------
def normalize_stop_id(s: str) -> str | None:
    if not s: return None
    digits = re.sub(r"[^0-9]","", s)
    if not digits: return None
    if len(digits) > 4: digits = digits[-4:]
    return digits.zfill(4)

# ---------- health ----------
@app.route("/")
def health():
    has_index = os.path.exists(web_index.INDEX_PATH)
    return jsonify({"status":"ok","service":"rts-backend","openai": True,"web_index": has_index})

# ---------- Bustime passthroughs (unchanged logic) ----------
@app.route("/api/routes")
def api_routes():
    data = rts_api.get_routes()
    routes_raw = data.get("routes", [])
    cleaned = [{"id": r.get("rt"), "name": r.get("rtnm"), "color": r.get("rtclr")} for r in routes_raw]
    return jsonify({"routes": cleaned})

@app.route("/api/directions")
def api_directions():
    route_id = request.args.get("route_id","")
    data = rts_api.get_directions(route_id)
    dirs_raw = data.get("directions", [])
    cleaned = []
    for d in dirs_raw:
        dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d
        dir_name = d.get("name") or d.get("dir") or d.get("dirName") or d.get("dirname") or dir_id
        cleaned.append({"id": dir_id, "name": dir_name})
    return jsonify({"directions": cleaned})

@app.route("/api/stops")
def api_stops():
    route_id = request.args.get("route_id","")
    direction_id = request.args.get("direction_id","")
    data = rts_api.get_stops(route_id, direction_id)
    stops_raw = data.get("stops", [])
    cleaned = [{
        "id": s.get("stpid"),
        "name": s.get("stpnm"),
        "lat": s.get("lat"), "lon": s.get("lon")
    } for s in stops_raw]
    return jsonify({"stops": cleaned})

@app.route("/api/predictions")
def api_predictions():
    stop4 = normalize_stop_id(request.args.get("stop_id",""))
    if not stop4: return jsonify({"error":"invalid stop_id"}), 400
    data = rts_api.get_predictions(stop4)
    preds = data.get("prd", [])
    cleaned = [{
        "route": p.get("rt"),
        "direction": p.get("rtdir"),
        "destination": p.get("des"),
        "minutes": p.get("prdctdn"),
        "vehicle_id": p.get("vid"),
        "arrival_time": p.get("prdtm"),
        "delayed": p.get("dly"),
    } for p in preds]
    return jsonify({"predictions": cleaned, "stop_id": stop4})

# ---------- small helpers for UI ----------
@app.route("/api/validate_stop")
def api_validate_stop():
    s = request.args.get("stop_id","")
    stop4 = normalize_stop_id(s)
    return jsonify({"ok": bool(stop4), "stop_id4": stop4})

@app.route("/api/stops_anydir")
def api_stops_anydir():
    route_id = request.args.get("route_id","")
    dirs = rts_api.get_directions(route_id).get("directions", [])
    dir_ids = [(d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d) for d in dirs]
    for d in dir_ids:
        st = rts_api.get_stops(route_id, d).get("stops", [])
        if st:
            cleaned = [{"id": s.get("stpid"), "name": s.get("stpnm"), "lat": s.get("lat"), "lon": s.get("lon")} for s in st]
            return jsonify({"route_id": route_id, "direction": d, "stops": cleaned})
    return jsonify({"route_id": route_id, "direction": None, "stops": []})

# ---------- NEW: Web index control ----------
@app.route("/api/web/ingest", methods=["POST"])
def api_web_ingest():
    body = request.get_json(silent=True) or {}
    base = (body.get("base_url") or web_index.DEFAULT_BASE).strip()
    max_pages = int(body.get("max_pages") or web_index.MAX_PAGES_DEFAULT)
    result = web_index.crawl_and_index(base, max_pages=max_pages)
    return jsonify(result)

@app.route("/api/web/ingest_folder", methods=["POST"])
def api_web_ingest_folder():
    body = request.get_json(silent=True) or {}
    folder = (body.get("folder") or "am2ar_mirror").strip()
    result = web_index.ingest_folder(folder)
    return jsonify(result)

@app.route("/api/web/search", methods=["GET"])
def api_web_search():
    q = request.args.get("q","")
    if not q: return jsonify({"error":"q required"}), 400
    hits = web_index.search(q, k=int(request.args.get("k","5")))
    return jsonify({"hits": hits})

@app.route("/api/web/ask", methods=["POST"])
def api_web_ask():
    body = request.get_json(silent=True) or {}
    q = (body.get("question") or "").strip()
    if not q: return jsonify({"error":"question is required"}), 400
    ans, src = webqa.answer(q)
    return jsonify({"answer": ans, "sources": src})

# ---------- Agent router ----------
@app.route("/api/agent", methods=["POST"])
def api_agent():
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "message is required"}), 400

    try:
        result = webqa.answer(msg)

        # Normalize different return shapes:
        answer = ""
        sources = []

        if isinstance(result, tuple):
            # Take first as answer; second (if present) as sources list
            answer = str(result[0]) if len(result) > 0 else ""
            if len(result) > 1 and isinstance(result[1], (list, tuple)):
                sources = list(result[1])

        elif isinstance(result, dict):
            # Common dict shape
            answer = str(result.get("answer") or result.get("text") or "")
            src = result.get("sources") or result.get("citations") or []
            if isinstance(src, (list, tuple)):
                sources = list(src)

        else:
            # Fallback: just stringify it
            answer = str(result)

        return jsonify({"answer": answer, "sources": sources})

    except Exception as e:
        # Log minimal error to stdout; return structured JSON
        print("agent_error:", repr(e))
        return jsonify({"error": "agent_failed", "detail": str(e)}), 500
