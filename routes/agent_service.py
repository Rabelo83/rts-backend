import os
import re
import json
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import rts_api
from db import gtfs_db

# OpenAI is OPTIONAL (agent still works without it)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

TZ = ZoneInfo("America/New_York")


# ------------------------------------------------------------
# Basic helpers
# ------------------------------------------------------------
def normalize_stop_id(s: str) -> str | None:
    """
    Normalize a stop ID to 4 digits:
      '1' -> '0001'
      '01' -> '0001'
      '001' -> '0001'
      '0001' -> '0001'
      '1192' -> '1192'
    """
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


def extract_route_id_regex(text: str) -> str | None:
    t = (text or "").lower()

    m = re.search(r"\b(route|rt|bus)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)

    m = re.search(r"\bbus\s*number\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(1)

    return None


def extract_stop_id_regex(text: str) -> str | None:
    """
    Stop ID extraction:
      "stop 473" -> 0473
      "#473" -> 0473
      digits-only message is handled separately in try_transit_answer()
    """
    t = (text or "").lower().strip()

    m = re.search(r"\bstop\s*(id)?\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(2))

    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    return None


def wants_schedule(text: str) -> bool:
    t = (text or "").lower()
    schedule_words = [
        "schedule", "sched", "schedual", "schedul", "timetable",
        "first bus", "first run", "last bus", "last run",
        "what time", "when does", "start", "end",
        "weekday", "weekdays", "mon-fri", "mon fri", "m/f", "m-f",
        # Spanish
        "horario", "tabla", "primero", "ultimo", "último", "a que hora", "a qué hora",
        "mañana", "tomorrow",
    ]
    return any(k in t for k in schedule_words)


def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    rt_words = [
        "eta", "minutes", "mins", "min", "prediction", "predictions", "arrive", "arrival",
        "next bus", "where is", "vehicle", "location", "real-time", "realtime",
        # Spanish
        "cuantos minutos", "cuántos minutos", "llega", "llegada", "en vivo", "tiempo real",
        "ubicacion", "ubicación",
    ]
    return any(k in t for k in rt_words)


def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "mins", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "sched", "schedual", "timetable", "first bus", "last bus",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada", "cuantos minutos", "tiempo real",
        "ubicacion", "ubicación", "mañana",
    ]
    return any(k in t for k in keywords)


def guess_destination_hint(text: str) -> str | None:
    t = (text or "").lower()
    if "reitz" in t:
        return "Reitz"
    if "oaks" in t:
        return "Oaks"
    if "downtown" in t:
        return "Downtown"
    if "hub" in t:
        return "Hub"
    if "rosa" in t and "park" in t:
        return "Rosa Parks"
    if "uf" in t or "campus" in t:
        return "UF"
    return None


def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


def detect_language_simple(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ["hola", "horario", "ruta", "parada", "llega", "cuántos", "ubicación", "mañana"]):
        return "es"
    return "en"


def parse_when_dt_from_message(msg: str) -> datetime:
    """
    Supports:
      - "tomorrow"/"mañana" -> +1 day
      - time like "2pm", "2:15pm", "14:00"
    If no time found -> current time.
    """
    now = datetime.now(TZ)
    base = now

    t = (msg or "").lower()
    if "tomorrow" in t or "mañana" in t:
        base = (now + timedelta(days=1)).replace(hour=now.hour, minute=now.minute, second=0, microsecond=0)

    m = re.search(r"\b([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm)?\b", t)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        ap = (m.group(3) or "").lower()

        if ap == "pm" and hh != 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0

        hh = max(0, min(23, hh))
        mm = max(0, min(59, mm))

        base = base.replace(hour=hh, minute=mm, second=0, microsecond=0)

    return base


# ------------------------------------------------------------
# OpenAI intent extraction (optional)
# ------------------------------------------------------------
def llm_extract_intent(message: str) -> dict:
    fallback = {
        "intent": "general",
        "route_id": None,
        "stop_id": None,
        "destination_hint": None,
        "language": detect_language_simple(message),
    }

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return fallback

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "You extract transit intent for Gainesville RTS. "
        "Return ONLY JSON with keys: intent, route_id, stop_id, destination_hint, language. "
        "Rules: "
        "- intent is one of: eta, schedule, vehicle_location, general. "
        "- route_id is route number like '9' (string). "
        "- stop_id is 1-4 digit stop ID if provided; otherwise null. "
        "- destination_hint is a place name if mentioned. "
        "- language is 'es' if Spanish, else 'en'. "
        "- If unsure, intent='general'."
    )

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
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
        return fallback


# ------------------------------------------------------------
# Stop suggestions (Bustime-only by route)
# ------------------------------------------------------------
def _tokenize_for_stop_match(text: str) -> list[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    tokens = [x for x in t.split() if len(x) >= 3]
    drop = {"route", "rt", "stop", "bus", "eta", "at", "to", "from", "the", "and", "for",
            "ruta", "parada", "autobus", "autobús", "bus", "a", "de", "en", "el", "la"}
    return [x for x in tokens if x not in drop]


def _score_stop_name(stop_name: str, tokens: list[str]) -> int:
    name = (stop_name or "").lower()
    score = 0
    for tok in tokens:
        if tok in name:
            score += 2
        elif len(tok) >= 4 and tok[:4] in name:
            score += 1
    return score


def suggest_stops_by_route(route_id: str, message: str, limit: int = 8) -> list[dict]:
    route_id = digits_only(route_id or "")
    if not route_id:
        return []

    tokens = _tokenize_for_stop_match(message)
    hint = guess_destination_hint(message)
    if hint:
        tokens += _tokenize_for_stop_match(hint)
    tokens = list(dict.fromkeys(tokens))

    if not tokens:
        return []

    try:
        dirs_data = rts_api.get_directions(route_id) or {}
        dirs_raw = dirs_data.get("directions", []) or []
    except Exception:
        dirs_raw = []

    dir_ids: list[str] = []
    for d in dirs_raw:
        if isinstance(d, dict):
            dir_id = d.get("dir") or d.get("id") or d.get("direction") or d.get("dirId")
        else:
            dir_id = d
        if dir_id:
            dir_ids.append(str(dir_id))

    if not dir_ids:
        dir_ids = ["NORTHBOUND", "SOUTHBOUND", "EASTBOUND", "WESTBOUND", "INBOUND", "OUTBOUND"]

    seen: set[tuple[str, str]] = set()
    scored: list[tuple[int, dict]] = []

    for dir_id in dir_ids:
        try:
            stops_data = rts_api.get_stops(route_id, dir_id) or {}
            stops_raw = stops_data.get("stops", []) or []
        except Exception:
            stops_raw = []

        for s in stops_raw:
            if not isinstance(s, dict):
                continue
            sid = normalize_stop_id(s.get("stpid") or "")
            nm = (s.get("stpnm") or "").strip()
            if not sid or not nm:
                continue
            key = (sid, nm)
            if key in seen:
                continue
            seen.add(key)

            score = _score_stop_name(nm, tokens)
            if score > 0:
                scored.append((score, {"id": sid, "name": nm}))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]


def fmt_stop_list(lang: str, title: str, candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        sid = c.get("id")
        nm = c.get("name") or ""
        if sid:
            lines.append(f"- Stop {sid}: {nm}".strip())

    if not lines:
        return tmsg(
            lang,
            "I still need the 4-digit Stop ID from the stop sign.",
            "Todavía necesito el Stop ID de 4 dígitos del letrero."
        )

    return tmsg(
        lang,
        f"{title}\nReply with ONE Stop ID:\n" + "\n".join(lines),
        f"{title}\nResponde con UN Stop ID:\n" + "\n".join(lines),
    )


# ------------------------------------------------------------
# Schedule via GTFS
# ------------------------------------------------------------
def schedule_next_departures(stop_id: str, route_id: str | None, when_dt: datetime, limit: int = 3) -> dict:
    """
    stop_id here is the rider-facing 4-digit stop number -> treat as GTFS stop_code.
    If nothing is scheduled at/after the requested time, return the closest
    scheduled departure before that time within a small window.
    """
    window_before = int(os.getenv("SCHEDULE_WINDOW_BEFORE_MIN", "90"))
    window_after = int(os.getenv("SCHEDULE_WINDOW_AFTER_MIN", "180"))

    start_dt = when_dt - timedelta(minutes=window_before)
    end_dt = when_dt + timedelta(minutes=window_after)

    result = gtfs_db.next_departures_window(
        stop_code=stop_id,
        route_short_name=route_id,
        start_dt=start_dt,
        end_dt=end_dt,
        limit=max(6, limit * 3),
    )
    rows = result.get("rows") or []
    if not rows:
        return {"rows": [], "fallback_before": False}

    target_date = when_dt.strftime("%Y%m%d")
    target_sec = when_dt.hour * 3600 + when_dt.minute * 60 + when_dt.second

    after_rows = []
    before_rows = []

    for r in rows:
        svc_date = str(r.get("service_date") or "")
        dep_secs = r.get("dep_secs")
        if dep_secs is None:
            continue

        if svc_date == target_date:
            delta = dep_secs - target_sec
        elif svc_date > target_date:
            delta = (24 * 3600 - target_sec) + dep_secs
        else:
            delta = -(target_sec + (24 * 3600 - dep_secs))

        if delta >= 0:
            after_rows.append((delta, r))
        else:
            before_rows.append((delta, r))

    if after_rows:
        after_rows.sort(key=lambda x: x[0])
        picked = [r for _, r in after_rows[:limit]]
        return {"rows": picked, "fallback_before": False}

    if before_rows:
        # closest before = max delta (least negative)
        before_rows.sort(key=lambda x: x[0], reverse=True)
        return {"rows": [before_rows[0][1]], "fallback_before": True}

    return {"rows": [], "fallback_before": False}


def format_realtime_answer(lang: str, usable_preds: list[dict]) -> str:
    lines = []
    for p in usable_preds[:3]:
        mins = p.get("minutes")
        rt = p.get("route") or ""
        dest = p.get("destination") or ""
        if isinstance(mins, str) and mins.upper() == "DUE":
            lines.append(tmsg(lang, f"Route {rt} to {dest}: DUE", f"Ruta {rt} hacia {dest}: YA"))
        else:
            lines.append(tmsg(lang, f"Route {rt} to {dest}: {mins} min", f"Ruta {rt} hacia {dest}: {mins} min"))

    return tmsg(lang, "Real-time ETA:\n- ", "ETA en tiempo real:\n- ") + "\n- ".join(lines)


def format_schedule_fallback(lang: str, stop_id: str, when_dt: datetime, rows: list[dict]) -> str:
    lines = []
    for r in rows[:3]:
        dep = r.get("departure_time") or ""
        rt = r.get("route_id") or ""
        hs = (r.get("headsign") or "").strip()
        if hs:
            lines.append(f"{dep} — Route {rt} ({hs})")
        else:
            lines.append(f"{dep} — Route {rt}")

    when_label = when_dt.strftime("%a %b %d %I:%M%p")
    return tmsg(
        lang,
        f"No real-time buses within the next 45 minutes for Stop {stop_id}.\nNext scheduled departures (after {when_label}):\n- " + "\n- ".join(lines),
        f"No hay buses en tiempo real dentro de los próximos 45 minutos para la parada {stop_id}.\nPróximas salidas programadas (después de {when_label}):\n- " + "\n- ".join(lines),
    )


# ------------------------------------------------------------
# Core agent logic
# ------------------------------------------------------------
def try_transit_answer(message: str) -> dict | None:
    msg = (message or "").strip()
    if not msg:
        return None

    # Digits-only messages: clarify route vs stop OR auto-ETA for likely stop IDs
    if re.fullmatch(r"\d{1,6}", msg):
        lang = detect_language_simple(msg)

        # 1-2 digits: ambiguous (route vs stop)
        if len(msg) <= 2:
            return {
                "answer": tmsg(
                    lang,
                    f"Did you mean Route {msg} or Stop {msg.zfill(4)}?\nReply: 'route {msg}' or 'stop {msg.zfill(4)}'.",
                    f"¿Te refieres a la Ruta {msg} o la Parada {msg.zfill(4)}?\nResponde: 'ruta {msg}' o 'parada {msg.zfill(4)}'."
                ),
                "sources": [{"type": "clarify_route_vs_stop"}],
            }

        # 3-4 digits: treat as Stop ID and run ETA
        if len(msg) <= 4:
            msg = f"ETA stop {msg.zfill(4)}"
        else:
            return {
                "answer": tmsg(
                    lang,
                    "That number looks too long to be a Stop ID. Please type 'stop ####' or 'route ##'.",
                    "Ese número parece demasiado largo para ser un Stop ID. Escribe 'parada ####' o 'ruta ##'."
                ),
                "sources": [{"type": "clarify_number"}],
            }

    if not is_transit_keywords(msg):
        return None

    extracted = llm_extract_intent(msg)
    lang = extracted.get("language", "en")
    intent = extracted.get("intent", "general")
    route_id = extracted.get("route_id")
    stop_id = extracted.get("stop_id")
    destination_hint = (extracted.get("destination_hint") or "").strip()

    # Regex fallback if LLM didn't extract
    if intent == "general":
        if not route_id:
            route_id = extract_route_id_regex(msg)
        if not stop_id:
            stop_id = extract_stop_id_regex(msg)
        if not destination_hint:
            destination_hint = guess_destination_hint(msg) or ""

    prefer_schedule = (intent == "schedule") or (wants_schedule(msg) and not wants_realtime(msg))

    # If stop_id missing:
    if not stop_id:
        if route_id:
            candidates = suggest_stops_by_route(route_id, (destination_hint + " " + msg).strip(), limit=8)

            if len(candidates) == 1:
                stop_id = candidates[0]["id"]
            elif len(candidates) > 1:
                return {
                    "answer": fmt_stop_list(
                        lang,
                        f"I can calculate ETA, but I need the boarding Stop ID. These Route {route_id} stops match your message:",
                        candidates
                    ),
                    "sources": [{"type": "stop_suggestions_bustime", "route_id": route_id}],
                }

        if not stop_id:
            return {
                "answer": tmsg(
                    lang,
                    "To check ETA, I need either a 4-digit Stop ID or a route number + place (example: 'ETA Route 1 at Reitz').",
                    "Para ver el ETA, necesito el Stop ID de 4 dígitos o una ruta + lugar (ej: 'ETA Ruta 1 en Reitz')."
                ),
                "sources": [{"type": "need_stop_or_route"}],
            }

    # Schedule questions (GTFS)
    if prefer_schedule:
        when_dt = parse_when_dt_from_message(msg)
        result = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=when_dt, limit=3)
        rows = result.get("rows") or []

        if rows:
            lines = []
            for r in rows[:3]:
                dep = r.get("departure_time") or ""
                rt = r.get("route_id") or (route_id or "")
                hs = (r.get("headsign") or "").strip()
                if hs:
                    lines.append(f"{dep} — Route {rt} ({hs})")
                else:
                    lines.append(f"{dep} — Route {rt}")

            if result.get("fallback_before"):
                return {
                    "answer": tmsg(
                        lang,
                        f"No departures at/after your requested time. Closest scheduled departure before then:\n- " + "\n- ".join(lines),
                        f"No hay salidas a esa hora o después. La salida programada más cercana antes es:\n- " + "\n- ".join(lines),
                    ),
                    "sources": [{"type": "gtfs_schedule_before", "stop_id": stop_id, "route_id": route_id}],
                }

            return {
                "answer": tmsg(
                    lang,
                    f"Scheduled departures for Stop {stop_id}:\n- " + "\n- ".join(lines),
                    f"Salidas programadas para la parada {stop_id}:\n- " + "\n- ".join(lines),
                ),
                "sources": [{"type": "gtfs_schedule", "stop_id": stop_id, "route_id": route_id}],
            }

        return {
            "answer": tmsg(
                lang,
                f"I couldn’t find scheduled departures for Stop {stop_id} at that time. Try another stop or route.",
                f"No encontré salidas programadas para la parada {stop_id} a esa hora. Prueba otra parada o ruta."
            ),
            "sources": [{"type": "gtfs_schedule_none", "stop_id": stop_id, "route_id": route_id}],
        }

    # Real-time predictions (Bustime)
    predictions = []
    try:
        data = rts_api.get_predictions(stop_id)
        preds = data.get("prd", []) or []

        if route_id:
            preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

        for p in preds[:10]:
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

    # Keep <=45 min or DUE  (Option B uses schedule if none in this window)
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
        return {
            "answer": format_realtime_answer(lang, usable),
            "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
        }

    # --- FALLBACK TO GTFS SCHEDULE (Option B) ---
    when_dt = datetime.now(TZ)
    sched = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=when_dt, limit=3)
    rows = sched.get("rows") or []
    if rows:
        return {
            "answer": format_schedule_fallback(lang, stop_id, when_dt, rows),
            "sources": [{"type": "realtime_none_fallback_schedule", "stop_id": stop_id, "route_id": route_id}],
        }

    return {
        "answer": tmsg(
            lang,
            f"No real-time ETAs (<=45 min) found for Stop {stop_id}. I also couldn’t find a scheduled departure for that stop right now.",
            f"No hay ETAs en tiempo real (<=45 min) para la parada {stop_id}. Tampoco encontré una salida programada para esa parada ahora."
        ),
        "sources": [{"type": "realtime_none", "stop_id": stop_id, "route_id": route_id}],
    }


def handle_agent_message(message: str) -> dict:
    transit = try_transit_answer(message)
    if transit:
        return {
            "answer": transit.get("answer", ""),
            "sources": transit.get("sources", []),
        }

    return {
        "answer": "I can help with RTS ETAs and schedules. Try: 'ETA Route 1 at Reitz' or type a Stop ID like '0473'.",
        "sources": [{"type": "fallback"}],
    }
