from flask import Flask, jsonify, request
from flask_cors import CORS
import os, re, sqlite3, json, traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import rts_api
from config import API_KEY

from openai import OpenAI
client = OpenAI(api_key=API_KEY) if API_KEY else OpenAI()

import web_index
import webqa

# Schedule DB module (db/schedule_db.py)
from db import schedule_db

app = Flask(__name__)
CORS(app)

TZ = ZoneInfo("America/New_York")

# ------------------------------------------------------------
# Basic helpers
# ------------------------------------------------------------
def normalize_stop_id(s: str) -> str | None:
    """Normalize a stop ID to 4 digits, e.g. '1192' -> '1192', '92' -> '0092'."""
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

def extract_route_id(text: str) -> str | None:     """     Try hard to find a route number in a message.     Recognizes: "route 9", "rt 21", "bus 9", "bus #9", "route:12"     """     t = (text or "").lower()      # route/rt/bus patterns     m = re.search(r"\b(route|rt|bus)\s*[:#]?\s*([0-9]{1,3})\b", t)     if m:         return m.group(2)      # also allow "bus number 9"     m = re.search(r"\bbus\s*number\s*([0-9]{1,3})\b", t)     if m:         return m.group(1)      return None
    """Extract a route number from free text. Examples: 'route 9', 'rt:21'."""
    t = (text or "").lower()
    m = re.search(r"\b(route|rt)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)
    return None

def extract_stop_id(text: str) -> str | None:
    """Extract a stop ID from free text. Prefers patterns like 'stop 1192'."""
    t = (text or "").lower()

    m = re.search(r"\bstop\s*[:#]?\s*([0-9]{3,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    m = re.search(r"#\s*([0-9]{3,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # Last resort: any 4-digit number
    m = re.search(r"\b([0-9]{4})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    return None

def wants_schedule(text: str) -> bool:
    t = (text or "").lower()
    schedule_words = [
        "schedule", "timetable", "first bus", "first run", "last bus", "last run",
        "what time", "when does", "start", "end",
        # Spanish
        "horario", "tabla", "primero", "ultimo", "último", "a que hora", "a qué hora"
    ]
    return any(k in t for k in schedule_words)

def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    rt_words = [
        "eta", "minutes", "min", "prediction", "predictions", "arrive", "arrival",
        "next bus", "where is", "vehicle", "location", "real-time", "realtime",
        # Spanish
        "cuantos minutos", "cuántos minutos", "llega", "llegada", "en vivo", "tiempo real", "ubicacion", "ubicación"
    ]
    return any(k in t for k in rt_words)

def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "timetable", "first bus", "last bus",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada", "cuantos minutos", "tiempo real", "ubicacion", "ubicación"
    ]
    return any(k in t for k in keywords)

def guess_destination_hint(text: str) -> str | None:
    t = (text or "").lower()
    if "reitz" in t:
        return "Reitz Union"
    if "oaks" in t:
        return "Oaks Mall"
    if "downtown" in t:
        return "Downtown"
    if "hub" in t:
        return "Hub"
    if "uf" in t or "campus" in t:
        return "UF Campus"
    return None

def tmsg(lang: str, en: str, es: str) -> str:
    """Return English or Spanish string depending on lang."""
    return es if (lang or "").lower().startswith("es") else en

# ------------------------------------------------------------
# Schedule DB querying (direct SQL on schedule.db)
# ------------------------------------------------------------
def _open_sched_conn():
    info = schedule_db.db_info()
    db_path = info.get("db_path") or os.environ.get("DB_PATH", "data/schedule.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def _service_ids_for_date(conn, date_iso: str) -> list[str]:
    """Pick service_ids active on a date, applying calendar_dates exceptions."""
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
    """Upcoming scheduled departures at a stop (optionally filtered by route)."""
    date_iso = when_dt.date().isoformat()
    now_secs = _time_to_secs(when_dt)

    with _open_sched_conn() as conn:
        service_ids = _service_ids_for_date(conn, date_iso)
        if not service_ids:
            return {"date": date_iso, "service_ids": [], "rows": []}

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
                return {"date": date_iso, "service_id": sid, "rows": [dict(r) for r in rows]}

        return {"date": date_iso, "service_ids": service_ids, "rows": []}

def schedule_first_last_for_route(route_id: str, when_dt: datetime):
    """Earliest and latest scheduled departures anywhere on a route for today."""
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
                return {"date": date_iso, "service_id": sid, "first": dict(first_row), "last": dict(last_row)}

    return None

# ------------------------------------------------------------
# Health
# ------------------------------------------------------------
@app.route("/")
def health():
    has_index = os.path.exists(web_index.INDEX_PATH)
    sched_info = schedule_db.db_info()
    return jsonify({
        "status": "ok",
        "service": "rts-backend",
        "openai": bool(API_KEY),
        "web_index": has_index,
        "schedule_db": {
            "exists": bool(sched_info.get("exists")),
            "db_path": sched_info.get("db_path"),
            "tables": sched_info.get("tables", []),
        }
    })

# ------------------------------------------------------------
# Bustime passthroughs
# ------------------------------------------------------------
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
    route_id = digits_only(raw)  # auto-clean like "9 (try any route id)" -> "9"
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

# ------------------------------------------------------------
# Small UI helpers
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# Schedule API (direct from schedule.db)
# ------------------------------------------------------------
@app.route("/api/schedule/info")
def api_schedule_info():
    return jsonify(schedule_db.db_info())

@app.route("/api/schedule/routes")
def api_schedule_routes():
    routes = schedule_db.list_routes()
    # Fill route names from live data if missing
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
    service_id = (request.args.get("service_id") or "mon_fri").strip()
    q = (request.args.get("q") or "").strip() or None
    limit = int(request.args.get("limit") or "200")
    if not route_id:
        return jsonify({"error": "Missing route_id"}), 400
    return jsonify({
        "route_id": route_id,
        "service_id": service_id,
        "stops": schedule_db.route_stops(route_id, service_id=service_id, q=q, limit=limit)
    })

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

# ------------------------------------------------------------
# Web index control (unchanged)
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# Agent helpers (LLM extraction + stop suggestions)
# ------------------------------------------------------------
def llm_extract_intent(text: str) -> dict:
    """
    Use OpenAI to extract intent + IDs. If OpenAI fails, return safe defaults.
    """
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "You extract transit intent for Gainesville RTS. "
        "Return ONLY JSON with keys: intent, route_id, stop_id, destination_hint, language. "
        "Rules: "
        "- intent is one of: eta, schedule, vehicle_location, general. "
        "- route_id is route number like '9' (string). "
        "- stop_id is 4-digit stop ID if provided; otherwise null. "
        "- destination_hint is a place name like 'Reitz Union' if mentioned. "
        "- language is 'es' if the user writes Spanish, else 'en'. "
        "- If unsure, intent='general'."
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        obj = json.loads(raw)

        intent = (obj.get("intent") or "general").strip()
        route_id = digits_only(obj.get("route_id") or "") or None
        stop_id = normalize_stop_id(obj.get("stop_id") or "") if obj.get("stop_id") else None
        destination_hint = (obj.get("destination_hint") or "").strip() or None
        language = (obj.get("language") or "en").strip().lower()
        if language not in ("en", "es"):
            language = "en"

        return {
            "intent": intent,
            "route_id": route_id,
            "stop_id": stop_id,
            "destination_hint": destination_hint,
            "language": language,
        }
    except Exception as e:
        print("llm_extract_intent_error:", repr(e))
        print(traceback.format_exc())
        return {"intent": "general", "route_id": None, "stop_id": None, "destination_hint": None, "language": "en"}

def suggest_stops(route_id: str, text: str, limit: int = 8) -> list[dict]:
    """
    Suggest likely stop IDs for a given route, based on user text.
    Uses schedule.db (route_stops), so it works even if Bustime is missing data.
    """
    hint = (guess_destination_hint(text) or "").strip()
    q = hint if hint else None

    try:
        stops = schedule_db.route_stops(route_id, service_id="mon_fri", q=q, limit=max(50, limit * 10))
    except Exception:
        stops = []

    # Light scoring if no q was used
    if not q:
        t = (text or "").lower()
        scored = []
        for s in stops:
            name = (s.get("stop_name") or "").lower()
            score = 0
            for token in ["reitz", "hub", "downtown", "oaks", "butler", "campus", "uf"]:
                if token in t and token in name:
                    score += 2
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        stops = [s for _, s in scored] or stops

    out = []
    for s in stops[:limit]:
        out.append({"id": s.get("stop_id"), "name": s.get("stop_name")})
    return out

def fmt_stop_list(lang: str, route_id: str, candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        sid = c.get("id")
        nm = c.get("name") or ""
        if sid:
            lines.append(f"- Stop {sid}: {nm}".strip())

    if not lines:
        return tmsg(
            lang,
            f"I found Route {route_id}, but I still need a Stop ID from the stop sign.",
            f"Encontré la Ruta {route_id}, pero todavía necesito el Stop ID del letrero."
        )

    return tmsg(
        lang,
        f"I can’t calculate ETA without the boarding Stop ID. Here are stops on Route {route_id} that match your message.\nReply with ONE Stop ID:\n" + "\n".join(lines),
        f"No puedo calcular el ETA sin el Stop ID de la parada. Estas paradas en la Ruta {route_id} coinciden con tu mensaje.\nResponde con UN Stop ID:\n" + "\n".join(lines),
    )

# ------------------------------------------------------------
# Agent: REALTIME (<=45 min) first, then SCHEDULE, else webqa
# ------------------------------------------------------------
def try_transit_answer(message: str) -> dict | None:
    msg = (message or "").strip()
    if not msg:
        return None

    if not is_transit_keywords(msg):
        return None

    extracted = llm_extract_intent(msg)
    lang = extracted.get("language", "en")
    intent = extracted.get("intent", "general")
    route_id = extracted.get("route_id")
    stop_id = extracted.get("stop_id")
    destination_hint = (extracted.get("destination_hint") or "").strip()

    # If LLM didn't extract, fallback to regex
    if intent == "general":
        if not route_id:
            route_id = extract_route_id(msg)
        if not stop_id:
            stop_id = extract_stop_id(msg)
        if not destination_hint:
            destination_hint = guess_destination_hint(msg) or ""

    # If missing stop_id, suggest stops when route_id is known
    if not stop_id:
        if route_id:
            candidates = suggest_stops(route_id, (destination_hint + " " + msg).strip(), limit=8)
            if candidates:
                return {
                    "answer": fmt_stop_list(lang, route_id, candidates),
                    "sources": [{"type": "stop_suggestions", "route_id": route_id}],
                }

        return {
            "answer": tmsg(
                lang,
                "To check the next bus time, I need the Stop ID (the 4-digit number on the stop sign). If you tell me your location/landmark (Reitz Union, UF, Downtown, Oaks Mall), I can suggest the correct stop.",
                "Para verificar el próximo bus, necesito el Stop ID (el número de 4 dígitos en el letrero). Si me dices tu ubicación o un lugar cercano (Reitz Union, UF, Downtown, Oaks Mall), puedo sugerirte la parada correcta."
            ),
            "sources": [{"type": "need_stop_id"}],
        }

    # 1) Realtime predictions (<=45 min)
    predictions = []
    try:
        data = rts_api.get_predictions(stop_id)
        preds = data.get("prd", [])

        if route_id:
            preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

        for p in preds[:8]:
            predictions.append({
                "route": p.get("rt"),
                "destination": p.get("des"),
                "minutes": p.get("prdctdn"),
                "arrival_time": p.get("prdtm"),
                "vehicle_id": p.get("vid"),
                "delayed": p.get("dly"),
            })
    except Exception as e:
        print("predictions_error:", repr(e))
        print(traceback.format_exc())

    usable = []
    for p in predictions:
        m = p.get("minutes")
        if m is None:
            continue
        if isinstance(m, str) and m.upper() == "DUE":
            usable.append(p)
            continue
        try:
            mi = int(m)
            if mi <= 45:
                usable.append(p)
        except Exception:
            pass

    if usable:
        lines = []
        for p in usable[:3]:
            mins = p.get("minutes")
            rt = p.get("route") or ""
            dest = p.get("destination") or ""
            if str(mins).upper() == "DUE":
                lines.append(tmsg(lang, f"Route {rt} to {dest}: DUE", f"Ruta {rt} hacia {dest}: YA"))
            else:
                lines.append(tmsg(lang, f"Route {rt} to {dest}: {mins} min", f"Ruta {rt} hacia {dest}: {mins} min"))

        return {
            "answer": tmsg(lang, "Real-time ETA:\n- ", "ETA en tiempo real:\n- ") + "\n- ".join(lines),
            "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
        }

    # 2) Schedule fallback
    now_dt = datetime.now(TZ)
    result = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=now_dt, limit=3)

    if result.get("rows"):
        stop_name = result["rows"][0].get("stop_name")
        service_id = result.get("service_id")
        day_label = _day_label(now_dt)

        lines = []
        for r in result["rows"]:
            rt = r.get("route_id")
            dep = r.get("departure_time")
            headsign = (r.get("headsign") or "").strip()
            if headsign:
                lines.append(f"{dep} — Route {rt} ({headsign})")
            else:
                lines.append(f"{dep} — Route {rt}")

        return {
            "answer": (
                tmsg(
                    lang,
                    f"No real-time ETA available (or it’s over 45 minutes). Next scheduled times for Stop {stop_id}"
                    + (f" ({stop_name})" if stop_name else "")
                    + f" — {day_label} ({service_id}):\n- ",
                    f"No hay ETA en tiempo real (o es mayor de 45 min). Próximos horarios programados para Stop {stop_id}"
                    + (f" ({stop_name})" if stop_name else "")
                    + f" — {day_label} ({service_id}):\n- "
                )
                + "\n- ".join(lines)
            ),
            "sources": [{"type": "schedule_db", "stop_id": stop_id, "route_id": route_id, "service_id": service_id}],
        }

    return {
        "answer": tmsg(
            lang,
            f"I couldn't find real-time ETAs (<=45 min) or scheduled departures right now for Stop {stop_id}. Try another stop or tell me your location.",
            f"No pude encontrar ETAs (<=45 min) ni horarios programados en este momento para Stop {stop_id}. Prueba otra parada o dime tu ubicación."
        ),
        "sources": [{"type": "none_found", "stop_id": stop_id, "route_id": route_id}],
    }

@app.route("/api/agent", methods=["POST"])
def api_agent():
    payload = request.get_json(silent=True) or {}
    msg = (payload.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "message is required"}), 400

    try:
        transit = try_transit_answer(msg)
        if transit:
            return jsonify({"answer": transit.get("answer", ""), "sources": transit.get("sources", [])})

        # Otherwise: keep your existing OpenAI webqa behavior
        result = webqa.answer(msg)

        if isinstance(result, tuple):
            answer = str(result[0]) if len(result) > 0 else ""
            sources = list(result[1]) if (len(result) > 1 and isinstance(result[1], (list, tuple))) else []
            return jsonify({"answer": answer, "sources": sources})

        if isinstance(result, dict):
            answer = str(result.get("answer") or result.get("text") or "")
            src = result.get("sources") or result.get("citations") or []
            sources = list(src) if isinstance(src, (list, tuple)) else []
            return jsonify({"answer": answer, "sources": sources})

        return jsonify({"answer": str(result), "sources": []})

    except Exception as e:
        print("agent_error:", repr(e))
        print(traceback.format_exc())
        return jsonify({"error": "agent_failed", "detail": str(e)}), 500
