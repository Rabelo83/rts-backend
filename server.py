from flask import Flask, jsonify, request
from flask_cors import CORS
import re, os, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import traceback

import rts_api
from config import API_KEY
from openai import OpenAI

# IMPORTANT: Use your API_KEY explicitly
client = OpenAI(api_key=API_KEY)

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
    Legacy fallback only (we now rely on the LLM first).
    """
    t = (text or "").lower()
    m = re.search(r"\b(route|rt|bus|line)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)
    return None

def extract_stop_id(text: str) -> str | None:
    """
    Legacy fallback only (we now rely on the LLM first).
    """
    t = (text or "").lower()

    m = re.search(r"\bstop\s*[:#]?\s*([0-9]{3,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    m = re.search(r"#\s*([0-9]{3,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    m = re.search(r"\b([0-9]{4})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    return None

def tmsg(lang: str, en: str, es: str) -> str:
    return es if lang == "es" else en

# ---------- Stop suggestions (route -> stops any direction) ----------
def stops_for_route_anydir(route_id: str) -> list[dict]:
    """
    Fetch stops for a route without requiring the user to pick a direction.
    Tries directions in order; returns first direction with stops.
    """
    try:
        dirs = rts_api.get_directions(route_id).get("directions", [])
        dir_ids = [(d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d) for d in dirs]
        for d in dir_ids:
            st = rts_api.get_stops(route_id, d).get("stops", [])
            if st:
                return [{"id": s.get("stpid"), "name": s.get("stpnm")} for s in st]
    except Exception as e:
        print("stops_for_route_anydir_error:", repr(e))
        print(traceback.format_exc())
    return []

def suggest_stops(route_id: str, query_text: str, limit: int = 8) -> list[dict]:
    """
    Suggest stop IDs for a route based on keywords (reitz, hub, downtown, UF, etc).
    Returns list of {id, name}.
    """
    q = (query_text or "").lower().strip()
    stops = stops_for_route_anydir(route_id)
    if not stops:
        return []

    # Tokens from query
    tokens = [t for t in re.split(r"[^a-z0-9]+", q) if len(t) >= 3]

    scored = []
    for s in stops:
        name = (s.get("name") or "").lower()
        score = 0
        for t in tokens:
            if t in name:
                score += 2

        # extra boosts for common intents
        if "reitz" in name and ("reitz" in q or "union" in q):
            score += 5
        if "hub" in name and "hub" in q:
            score += 4
        if score > 0:
            scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]

def fmt_stop_list(lang: str, route_id: str, candidates: list[dict]) -> str:
    if not candidates:
        return ""
    header = tmsg(
        lang,
        f"I can help — I just need the Stop ID where you will board. Here are matching stops on Route {route_id}. Reply with ONE Stop ID:",
        f"Puedo ayudarte — solo necesito el Stop ID donde vas a abordar. Aquí hay paradas que coinciden en la Ruta {route_id}. Responde con UN Stop ID:"
    )
    lines = []
    for c in candidates[:8]:
        sid = c.get("id")
        name = c.get("name") or ""
        if sid:
            lines.append(f"- Stop {sid}: {name}")
    return header + "\n" + "\n".join(lines)

# ---------- LLM intent/entity extraction ----------
def _safe_json_loads(s: str) -> dict:
    try:
        return json.loads(s)
    except Exception:
        return {}

def llm_extract_intent(message: str) -> dict:
    """
    LLM-first extraction. NEVER guesses IDs.
    Returns:
      language: en/es
      intent: eta/schedule/vehicle_location/general
      route_id: digits or null
      stop_id: 4-digit string or null
      destination_hint: string or null
      needs: list
    """
    user_text = (message or "").strip()
    if not user_text:
        return {"language":"en","intent":"general","route_id":None,"stop_id":None,"destination_hint":None,"needs":[]}

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "You are a transit assistant for Gainesville RTS.\n"
        "Output ONLY valid JSON. No extra text.\n"
        "\n"
        "Rules:\n"
        "1) Never invent/guess a stop ID or route ID.\n"
        "2) If user didn't provide stop ID, stop_id must be null.\n"
        "3) If user didn't provide route number, route_id must be null.\n"
        "4) Detect language: English='en', Spanish='es'.\n"
        "5) intent must be one of: 'eta','schedule','vehicle_location','general'.\n"
        "   - eta: next bus, minutes, predictions, arriving.\n"
        "   - schedule: timetable, first/last bus, scheduled time.\n"
        "   - vehicle_location: where is the bus, bus location.\n"
        "6) destination_hint is a short place phrase if mentioned (ex: 'Reitz Union'). Else null.\n"
        "7) needs: list what is REQUIRED to answer accurately.\n"
        "\n"
        "Schema:\n"
        "{"
        "\"language\":\"en|es\","
        "\"intent\":\"eta|schedule|vehicle_location|general\","
        "\"route_id\":string|null,"
        "\"stop_id\":string|null,"
        "\"destination_hint\":string|null,"
        "\"needs\":string[]"
        "}"
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role":"system","content":system},
                {"role":"user","content":user_text},
            ],
            # IMPORTANT: forces JSON output
            response_format={"type":"json_object"},
            temperature=0
        )
        content = resp.choices[0].message.content or "{}"
        data = _safe_json_loads(content)

        # cleanup (still not guessing)
        if isinstance(data.get("route_id"), str):
            rid = digits_only(data["route_id"])
            data["route_id"] = rid if rid else None

        if isinstance(data.get("stop_id"), str):
            sid = normalize_stop_id(data["stop_id"])
            data["stop_id"] = sid if sid else None

        data.setdefault("language", "en")
        data.setdefault("intent", "general")
        data.setdefault("route_id", None)
        data.setdefault("stop_id", None)
        data.setdefault("destination_hint", None)
        data.setdefault("needs", [])

        if data["language"] not in ("en","es"):
            data["language"] = "en"
        if data["intent"] not in ("eta","schedule","vehicle_location","general"):
            data["intent"] = "general"
        if not isinstance(data["needs"], list):
            data["needs"] = []

        return data

    except Exception as e:
        print("llm_extract_error:", repr(e))
        print(traceback.format_exc())
        # fallback minimal
        lower = user_text.lower()
        lang = "es" if re.search(r"\b(hola|ruta|parada|horario|autob[uú]s|por favor)\b", lower) else "en"
        return {"language":lang,"intent":"general","route_id":None,"stop_id":None,"destination_hint":None,"needs":[]}

# ---------- schedule DB querying (direct SQL on schedule.db) ----------
def _open_sched_conn():
    db_path = schedule_db.db_info().get("db_path") or "data/schedule.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def _service_ids_for_date(conn, date_iso: str) -> list[str]:
    dt = datetime.fromisoformat(date_iso)
    dow = dt.weekday()
    dow_col = ["mon","tue","wed","thu","fri","sat","sun"][dow]

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

# ---------- Agent (LLM-first intent -> realtime -> schedule -> webqa) ----------
def try_transit_answer(message: str) -> dict | None:
    msg = (message or "").strip()
    if not msg:
        return None

    # 1) LLM-first intent/entity extraction
    extracted = llm_extract_intent(msg)
    lang = extracted.get("language", "en")
    intent = extracted.get("intent", "general")
    route_id = extracted.get("route_id")
    stop_id = extracted.get("stop_id")
    destination_hint = (extracted.get("destination_hint") or "").strip()

    # Not a transit message -> let webqa handle it
    if intent == "general":
        return None

    # 2) If stop_id missing, ask naturally (and suggest stops if route provided)
    if not stop_id:
        if route_id:
            query_text = (destination_hint + " " + msg).strip()
            candidates = suggest_stops(route_id, query_text, limit=8)
            if candidates:
                return {"answer": fmt_stop_list(lang, route_id, candidates), "sources": [{"type":"stop_suggestions","route_id":route_id}]}

        return {
            "answer": tmsg(
                lang,
                "To check the next bus time, I need the Stop ID (the 4-digit number on the stop sign). If you tell me your location/landmark (Reitz Union, UF, Downtown, Oaks Mall), I can suggest the correct stop.",
                "Para verificar el próximo bus, necesito el Stop ID (el número de 4 dígitos en el letrero). Si me dices tu ubicación o un lugar cercano (Reitz Union, UF, Downtown, Oaks Mall), puedo sugerirte la parada correcta."
            ),
            "sources": [{"type":"need_stop_id"}]
        }

    # 3) Real-time predictions FIRST (only use if <=45 min or DUE)
    predictions = []
    try:
        data = rts_api.get_predictions(stop_id)
        preds = data.get("prd", [])

        if route_id:
            preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

        for p in preds[:5]:
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
            "sources": [{"type":"realtime","stop_id":stop_id,"route_id":route_id}],
            "data": {"predictions": usable[:3]}
        }

    # 4) Schedule fallback (next departures)
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
                    f"No real-time ETA available (or it's over 45 minutes). Next scheduled times for Stop {stop_id}"
                    + (f" ({stop_name})" if stop_name else "")
                    + f" — {day_label} ({service_id}):\n- ",
                    f"No hay ETA en tiempo real (o es mayor de 45 min). Próximos horarios programados para Stop {stop_id}"
                    + (f" ({stop_name})" if stop_name else "")
                    + f" — {day_label} ({service_id}):\n- "
                )
                + "\n- ".join(lines)
            ),
            "sources": [{"type":"schedule_db","stop_id":stop_id,"route_id":route_id,"service_id":service_id}],
            "data": {"schedule_next": result}
        }

    # 5) If schedule also empty:
    if route_id and intent == "schedule":
        fl = schedule_first_last_for_route(route_id, now_dt)
        if fl:
            day_label = _day_label(now_dt)
            sid = fl["service_id"]
            first = fl["first"]
            last = fl["last"]
            return {
                "answer": tmsg(
                    lang,
                    f"Scheduled summary for Route {route_id} ({day_label}, {sid}):\n"
                    f"- First departure: {first['departure_time']} (Stop {first['stop_id']} — {first.get('stop_name')})\n"
                    f"- Last departure: {last['departure_time']} (Stop {last['stop_id']} — {last.get('stop_name')})",
                    f"Resumen del horario para Ruta {route_id} ({day_label}, {sid}):\n"
                    f"- Primera salida: {first['departure_time']} (Stop {first['stop_id']} — {first.get('stop_name')})\n"
                    f"- Última salida: {last['departure_time']} (Stop {last['stop_id']} — {last.get('stop_name')})"
                ),
                "sources": [{"type":"schedule_db","route_id":route_id,"service_id":sid}],
                "data": fl
            }

    return {
        "answer": tmsg(
            lang,
            f"I couldn't find real-time ETAs or scheduled departures right now for Stop {stop_id}. Try another stop or tell me your location.",
            f"No pude encontrar ETAs ni horarios programados en este momento para Stop {stop_id}. Prueba otra parada o dime tu ubicación."
        ),
        "sources": [{"type":"none_found","stop_id":stop_id,"route_id":route_id}]
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
            return jsonify({"answer": transit["answer"], "sources": transit.get("sources", [])})

        # fallback to your webqa agent
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
        print(traceback.format_exc())
        return jsonify({"error": "agent_failed", "detail": str(e)}), 500
