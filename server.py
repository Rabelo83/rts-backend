from flask import Flask, jsonify, request
from flask_cors import CORS
import re, os

import rts_api
from config import API_KEY
from openai import OpenAI
client = OpenAI()

import web_index
import webqa

# Schedule DB
from db import schedule_db

app = Flask(__name__)
CORS(app)

# ---------- helpers ----------
def normalize_stop_id(s: str) -> str | None:
    if not s:
        return None
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return None
    if len(digits) > 4:
        digits = digits[-4:]
    return digits.zfill(4)

def digits_only(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")

# ---------- health ----------
@app.route("/")
def health():
    has_index = os.path.exists(web_index.INDEX_PATH)
    sched_info = schedule_db.db_info()
    return jsonify({
        "status": "ok",
        "service": "rts-backend",
        "openai": True,
        "web_index": has_index,
        "schedule_db": {
            "exists": bool(sched_info.get("exists")),
            "db_path": sched_info.get("db_path"),
            "tables": sched_info.get("tables", []),
        }
    })

# ---------- Bustime passthroughs (unchanged logic) ----------
@app.route("/api/routes")
def api_routes():
    data = rts_api.get_routes()
    routes_raw = data.get("routes", [])
    cleaned = [{"id": r.get("rt"), "name": r.get("rtnm"), "color": r.get("rtclr")} for r in routes_raw]
    return jsonify({"routes": cleaned})

@app.route("/api/directions")
def api_directions():
    route_id = request.args.get("route_id", "")
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
    route_id = request.args.get("route_id", "")
    direction_id = request.args.get("direction_id", "")
    data = rts_api.get_stops(route_id, direction_id)
    stops_raw = data.get("stops", [])
    cleaned = [{
        "id": s.get("stpid"),
        "name": s.get("stpnm"),
        "lat": s.get("lat"),
        "lon": s.get("lon")
    } for s in stops_raw]
    return jsonify({"stops": cleaned})

@app.route("/api/predictions")
def api_predictions():
    stop4 = normalize_stop_id(request.args.get("stop_id", ""))
    if not stop4:
        return jsonify({"error": "invalid stop_id"}), 400

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

# ---------- Vehicles (real-time locations) ----------
@app.route("/api/vehicles")
def api_vehicles():
    raw = request.args.get("route_id", "")
    route_id = digits_only(raw)  # ✅ auto-clean: digits only

    if not route_id:
        return jsonify({"error": "route_id is required"}), 400

    data = rts_api.get_vehicles(route_id)
    vehicles_raw = data.get("vehicle", []) or data.get("vehicles", []) or []

    cleaned = []
    for v in vehicles_raw:
        cleaned.append({
            "vehicle_id": v.get("vid"),
            "lat": v.get("lat"),
            "lon": v.get("lon"),
            "heading": v.get("hdg"),
            "speed": v.get("spd"),
            "route": v.get("rt"),
            "destination": v.get("des"),
            "delayed": v.get("dly"),
            "timestamp": v.get("tmstmp"),
        })

    return jsonify({"route_id": route_id, "vehicles": cleaned})

# ---------- small helpers for UI ----------
@app.route("/api/validate_stop")
def api_validate_stop():
    s = request.args.get("stop_id", "")
    stop4 = normalize_stop_id(s)
    return jsonify({"ok": bool(stop4), "stop_id4": stop4})

@app.route("/api/stops_anydir")
def api_stops_anydir():
    route_id = request.args.get("route_id", "")
    dirs = rts_api.get_directions(route_id).get("directions", [])
    dir_ids = [(d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d) for d in dirs]
    for d in dir_ids:
        st = rts_api.get_stops(route_id, d).get("stops", [])
        if st:
            cleaned = [{"id": s.get("stpid"), "name": s.get("stpnm"), "lat": s.get("lat"), "lon": s.get("lon")} for s in st]
            return jsonify({"route_id": route_id, "direction": d, "stops": cleaned})
    return jsonify({"route_id": route_id, "direction": None, "stops": []})

# ---------- Schedule API (from PDF → SQLite) ----------
@app.route("/api/schedule/info")
def api_schedule_info():
    return jsonify(schedule_db.db_info())

@app.route("/api/schedule/routes")
def api_schedule_routes():
    routes = schedule_db.list_routes()

    # ✅ Fill missing schedule names from live RTS routes
    try:
        live = rts_api.get_routes()
        live_routes = live.get("routes", [])
        name_map = {r.get("rt"): r.get("rtnm") for r in live_routes if r.get("rt")}
        for item in routes:
            if not item.get("route_name"):
                item["route_name"] = name_map.get(item.get("route_id"))
    except Exception as e:
        print("schedule_routes_name_fill_error:", repr(e))

    return jsonify({"routes": routes})

@app.route("/api/schedule/find_stops")
def api_schedule_find_stops():
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", "25"))
    return jsonify({"stops": schedule_db.find_stops(q, limit=limit)})

@app.route("/api/schedule/route_stops")
def api_schedule_route_stops():
    route_id = (request.args.get("route_id") or "").strip()
    if not route_id:
        return jsonify({"error": "Missing route_id"}), 400
    return jsonify({"route_id": route_id, "stops": schedule_db.route_stops(route_id)})

@app.route("/api/schedule/last_departure")
def api_schedule_last_departure():
    """
    Example:
      /api/schedule/last_departure?route_id=12&service_id=WKD&stop_id=1234
    """
    route_id = (request.args.get("route_id") or "").strip()
    service_id = (request.args.get("service_id") or "").strip()
    stop_id = normalize_stop_id(request.args.get("stop_id", ""))

    if not route_id or not service_id or not stop_id:
        return jsonify({"error": "Missing route_id, service_id, or stop_id"}), 400

    row = schedule_db.last_departure_any(route_id, service_id, stop_id)
    if not row:
        return jsonify({"error": "No schedule found for that route/service/stop"}), 404

    return jsonify({
        "route_id": route_id,
        "service_id": service_id,
        **row
    })

# ---------- Web index control ----------
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
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "q required"}), 400
    hits = web_index.search(q, k=int(request.args.get("k", "5")))
    return jsonify({"hits": hits})

@app.route("/api/web/ask", methods=["POST"])
def api_web_ask():
    body = request.get_json(silent=True) or {}
    q = (body.get("question") or "").strip()
    if not q:
        return jsonify({"error": "question is required"}), 400
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
            answer = str(result[0]) if len(result) > 0 else ""
            if len(result) > 1 and isinstance(result[1], (list, tuple)):
                sources = list(result[1])

        elif isinstance(result, dict):
            answer = str(result.get("answer") or result.get("text") or "")
            src = result.get("sources") or result.get("citations") or []
            if isinstance(src, (list, tuple)):
                sources = list(src)

        else:
            answer = str(result)

        return jsonify({"answer": answer, "sources": sources})

    except Exception as e:
        print("agent_error:", repr(e))
        return jsonify({"error": "agent_failed", "detail": str(e)}), 500
