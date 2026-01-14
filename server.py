from flask import Flask, jsonify, request
from flask_cors import CORS
import re, os, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import rts_api
from config import API_KEY
from openai import OpenAI
client = OpenAI()

import web_index
import webqa

# Schedule DB module
from db import schedule_db

app = Flask(__name__)
CORS(app)

TZ = ZoneInfo("America/New_York")

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

def extract_route_id(text: str) -> str | None:
    """
    Try hard to find a route number in a message.
    Examples: "route 9", "rt 21", "Route:12"
    """
    t = (text or "").lower()
    m = re.search(r"\b(route|rt)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)
    # fallback: "on 9" is too risky; don't guess
    return None

def extract_stop_id(text: str) -> str | None:
    """
    Find a stop id. Prefer patterns like 'stop 1192' or '#1192'.
    """
    t = (text or "").lower()

    # Preferred: "stop 1234"
    m = re.search(r"\bstop\s*[:#]?\s*([0-9]{3,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # Also allow "#1234"
    m = re.search(r"#\s*([0-9]{3,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # Last resort: any 4-digit number in the text
    m = re.search(r"\b([0-9]{4})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    return None

def is_transit_question(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "bus", "route", "stop", "eta", "arrive", "arrival", "minutes", "next",
        "prediction", "predictions", "vehicle", "where is", "location",
        "schedule", "timetable", "first bus", "last bus", "last run", "first run"
    ]
    return any(k in t for k in keywords)

def wants_schedule(text: str) -> bool:
    t = (text or "").lower()
    schedule_words = ["schedule", "timetable", "first bus", "first run", "last bus", "last run", "what time", "when does", "start", "end"]
    return any(k in t for k in schedule_words)

def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    rt_words = ["eta", "minutes", "prediction", "predictions", "arrive", "arrival", "next bus", "where is", "vehicle", "location", "real-time", "realtime"]
    return any(k in t for k in rt_words)

# ---------- schedule DB querying (direct SQL on schedule.db) ----------
def _open_sched_conn():
    db_path = schedule_db.db_info().get("db_path") or "data/schedule.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def _service_ids_for_date(conn, date_iso: str) -> list[str]:
    """
    Picks service_ids active on a given date, applying calendar_dates exceptions.
    date_iso: "YYYY-MM-DD"
    """
    dt = datetime.fromisoformat(date_iso)
    dow = dt.weekday()  # Mon=0 .. Sun=6
    dow_col = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][dow]

    base = conn.execute(
        f"""
        SELECT service_id
        FROM calendar
        WHERE start_date <= ? AND end_date >= ? AND {dow_col} = 1
        """,
        (date_iso, date_iso),
    ).fetchall()
    service_ids = {r["service_id"] for r in base}

    # Apply exceptions
    ex = conn.execute(
        """
        SELECT service_id, exception_type
        FROM calendar_dates
        WHERE date = ?
        """,
        (date_iso,),
    ).fetchall()

    for r in ex:
        sid = r["service_id"]
        et = int(r["exception_type"])
        if et == 2 and sid in service_ids:
            service_ids.remove(sid)
        elif et == 1:
            service_ids.add(sid)

    return sorted(service_ids)

def _day_label(dt: datetime) -> str:
    if dt.weekday() <= 4:
        return "Weekday"
    if dt.weekday() == 5:
        return "Saturday"
    return "Sunday"

def _time_to_secs(dt: datetime) -> int:
    return dt.hour * 3600 + dt.minute * 60 + dt.second

def schedule_next_departures(stop_id: str, route_id: str | None, when_dt: datetime, limit: int = 3):
    """
    Returns upcoming scheduled departures at a stop (optionally filtered by route).
    """
    date_iso = when_dt.date().isoformat()
    now_secs = _time_to_secs(when_dt)

    with _open_sched_conn() as conn:
        service_ids = _service_ids_for_date(conn, date_iso)
        if not service_ids:
            return {"date": date_iso, "service_ids": [], "rows": []}

        # Try services in order; return first service that yields results
        for sid in service_ids:
            if route_id:
                rows = conn.execute(
                    """
                    SELECT st.route_id, st.stop_id, s.stop_name, t.headsign,
                           st.departure_time, st.departure_secs
                    FROM stop_times st
                    JOIN trips t ON t.trip_id = st.trip_id
                    JOIN stops s ON s.stop_id = st.stop_id
                    WHERE st.stop_id = ?
                      AND st.route_id = ?
                      AND t.service_id = ?
                      AND st.departure_secs >= ?
                    ORDER BY st.departure_secs ASC
                    LIMIT ?
                    """,
                    (stop_id, route_id, sid, now_secs, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT st.route_id, st.stop_id, s.stop_name, t.headsign,
                           st.departure_time, st.departure_secs
                    FROM stop_times st
                    JOIN trips t ON t.trip_id = st.trip_id
                    JOIN stops s ON s.stop_id = st.stop_id
                    WHERE st.stop_id = ?
                      AND t.service_id = ?
                      AND st.departure_secs >= ?
                    ORDER BY st.departure_secs ASC
                    LIMIT ?
                    """,
                    (stop_id, sid, now_secs, limit),
                ).fetchall()

            if rows:
                return {
                    "date": date_iso,
                    "service_id": sid,
                    "rows": [dict(r) for r in rows],
                }

        # No upcoming trips found today on any service_id
        return {"date": date_iso, "service_ids": service_ids, "rows": []}

def schedule_first_last_for_route(route_id: str, when_dt: datetime):
    """
    If user asks 'first bus' / 'last bus' for a route but doesn't provide stop,
    we return earliest and latest scheduled departure *anywhere on the route* for today.
    """
    date_iso = when_dt.date().isoformat()

    with _open_sched_conn() as conn:
        service_ids = _service_ids_for_date(conn, date_iso)
        if not service_ids:
            return None

        for sid in service_ids:
            first_row = conn.execute(
                """
                SELECT st.route_id, st.stop_id, s.stop_name, t.headsign,
                       st.departure_time, st.departure_secs
                FROM stop_times st
                JOIN trips t ON t.trip_id = st.trip_id
                JOIN stops s ON s.stop_id = st.stop_id
                WHERE st.route_id = ?
                  AND t.service_id = ?
                ORDER BY st.departure_secs ASC
                LIMIT 1
                """,
                (route_id, sid),
            ).fetchone()

            last_row = conn.execute(
                """
                SELECT st.route_id, st.stop_id, s.stop_name, t.headsign,
                       st.departure_time, st.departure_secs
                FROM stop_times st
                JOIN trips t ON t.trip_id = st.trip_id
                JOIN stops s ON s.stop_id = st.stop_id
                WHERE st.route_id = ?
                  AND t.service_id = ?
                ORDER BY st.departure_secs DESC
                LIMIT 1
                """,
                (route_id, sid),
            ).fetchone()

            if first_row and last_row:
                return {
                    "date": date_iso,
                    "service_id": sid,
                    "first": dict(first_row),
                    "last": dict(last_row),
                }

    return None

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

# ---------- Bustime passthroughs ----------
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

@app.route("/api/vehicles")
def api_vehicles():
    raw = request.args.get("route_id", "")
    route_id = digits_only(raw)  # auto-clean

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

# ---------- UI helpers ----------
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

# ---------- Schedule API ----------
@app.route("/api/schedule/info")
def api_schedule_info():
    return jsonify(schedule_db.db_info())

@app.route("/api/schedule/routes")
def api_schedule_routes():
    routes = schedule_db.list_routes()
    # fill route names from live data if missing
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
    route_id = (request.args.get("route_id") or "").strip()
    service_id = (request.args.get("service_id") or "").strip()
    stop_id = normalize_stop_id(request.args.get("stop_id", ""))

    if not route_id or not service_id or not stop_id:
        return jsonify({"error": "Missing route_id, service_id, or stop_id"}), 400

    row = schedule_db.last_departure_any(route_id, service_id, stop_id)
    if not row:
        return jsonify({"error": "No schedule found for that route/service/stop"}), 404

    return jsonify({"route_id": route_id, "service_id": service_id, **row})

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

# ---------- Agent (REALTIME first, then SCHEDULE, else OpenAI webqa) ----------
def try_transit_answer(message: str) -> dict | None:
    msg = (message or "").strip()
    if not msg:
        return None

    if not is_transit_question(msg):
        return None

    route_id = extract_route_id(msg)
    stop_id = extract_stop_id(msg)

    # If user wants realtime (or gave stop), try predictions FIRST
    if stop_id and (wants_realtime(msg) or not wants_schedule(msg)):
        try:
            data = rts_api.get_predictions(stop_id)
            preds = data.get("prd", [])

            # Optional filter by route if route_id provided
            if route_id:
                preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

            if preds:
                cleaned = [{
                    "route": p.get("rt"),
                    "direction": p.get("rtdir"),
                    "destination": p.get("des"),
                    "minutes": p.get("prdctdn"),
                    "vehicle_id": p.get("vid"),
                    "arrival_time": p.get("prdtm"),
                    "delayed": p.get("dly"),
                } for p in preds[:3]]

                lines = []
                for p in cleaned:
                    mins = p["minutes"]
                    dest = p["destination"] or ""
                    rt = p["route"] or ""
                    lines.append(f"Route {rt} to {dest}: {mins} min")

                answer = "Real-time (Bustime) predictions:\n- " + "\n- ".join(lines)
                return {
                    "answer": answer,
                    "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
                    "data": {"predictions": cleaned}
                }
        except Exception as e:
            print("realtime_prediction_error:", repr(e))
            # continue to schedule fallback

    # Schedule fallback (Option 1)
    now_dt = datetime.now(TZ)

    # If schedule question and stop is missing, ask for stop (best accuracy)
    if wants_schedule(msg) and not stop_id and not route_id:
        return {
            "answer": "To answer from the schedule, I need at least a Route number (example: Route 9) or a Stop ID (example: Stop 1192).",
            "sources": [{"type": "schedule_db"}],
            "data": {}
        }

    # If we have stop_id, we can give scheduled next departures at that stop
    if stop_id:
        result = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=now_dt, limit=3)

        if result.get("rows"):
            stop_name = result["rows"][0].get("stop_name")
            service_id = result.get("service_id")
            day_label = _day_label(now_dt)

            lines = []
            for r in result["rows"]:
                rt = r.get("route_id")
                t = r.get("departure_time")
                headsign = r.get("headsign") or ""
                lines.append(f"{t} — Route {rt} {headsign}".strip())

            answer = (
                f"No real-time predictions found, so here’s the scheduled service ({day_label}, {service_id}) "
                f"for Stop {stop_id}"
                + (f" ({stop_name})" if stop_name else "")
                + ":\n- " + "\n- ".join(lines)
            )

            return {
                "answer": answer,
                "sources": [{"type": "schedule_db", "date": result.get("date"), "service_id": service_id}],
                "data": {"schedule_next": result}
            }

        # If no upcoming scheduled trips today, try last departure for the route+stop if route provided
        if route_id:
            try:
                # pick a service_id based on today if we can
                date_iso = now_dt.date().isoformat()
                with _open_sched_conn() as conn:
                    sids = _service_ids_for_date(conn, date_iso)
                if sids:
                    sid = sids[0]
                    last = schedule_db.last_departure_any(route_id, sid, stop_id)
                    if last:
                        answer = (
                            f"No real-time predictions found. Scheduled last departure for Route {route_id} "
                            f"({ _day_label(now_dt) }, {sid}) at Stop {stop_id}: {last['last_departure_time']}."
                        )
                        return {
                            "answer": answer,
                            "sources": [{"type": "schedule_db", "date": date_iso, "service_id": sid}],
                            "data": {"last_departure": last}
                        }
            except Exception as e:
                print("schedule_last_departure_error:", repr(e))

        return {
            "answer": "I couldn’t find real-time predictions, and I also couldn’t find upcoming scheduled departures for that stop right now. If you tell me the Route number too (example: Route 9), I can narrow it down.",
            "sources": [{"type": "schedule_db"}],
            "data": {}
        }

    # If we only have route_id (no stop_id), answer first/last bus questions using schedule
    if route_id and wants_schedule(msg):
        fl = schedule_first_last_for_route(route_id, now_dt)
        if fl:
            day_label = _day_label(now_dt)
            sid = fl["service_id"]
            first = fl["first"]
            last = fl["last"]
            answer = (
                f"Scheduled summary for Route {route_id} ({day_label}, {sid}):\n"
                f"- First departure: {first['departure_time']} (Stop {first['stop_id']} — {first.get('stop_name')})\n"
                f"- Last departure: {last['departure_time']} (Stop {last['stop_id']} — {last.get('stop_name')})"
            )
            return {
                "answer": answer,
                "sources": [{"type": "schedule_db", "date": fl["date"], "service_id": sid}],
                "data": fl
            }

        return {
            "answer": f"I found the route number (Route {route_id}), but I couldn’t compute schedule times for today. If you give me a Stop ID too, I can answer more accurately.",
            "sources": [{"type": "schedule_db"}],
            "data": {}
        }

    # Otherwise: ask for missing info
    return {
        "answer": "To answer that, please include a Stop ID (example: Stop 1192). If you also include a Route (example: Route 9), the answer will be more accurate.",
        "sources": [{"type": "realtime_or_schedule"}],
        "data": {}
    }

@app.route("/api/agent", methods=["POST"])
def api_agent():
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "message is required"}), 400

    try:
        # ✅ First try: transit smart-answer (realtime → schedule fallback)
        transit = try_transit_answer(msg)
        if transit:
            return jsonify({"answer": transit["answer"], "sources": transit.get("sources", [])})

        # ✅ Otherwise: keep your existing OpenAI agent behavior
        result = webqa.answer(msg)

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
