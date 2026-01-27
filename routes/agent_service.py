import os
import re
import json
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import rts_api

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
    """
    Try to find route number in message.
    Recognizes: "route 9", "rt 21", "bus 9", "bus #9", "route:12", "bus number 9"
    """
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
    Extract stop ID from free text.
    Supports:
      "stop 1" / "stop id 1" / "stop #1" -> 0001
      "#1192" -> 1192
      Plain number ONLY if the entire message is digits:
        - 3-4 digits -> stop id (pad to 4)
        - 1-2 digits -> we DO NOT assume stop id (could be route)
    """
    t = (text or "").lower().strip()

    # Explicit stop patterns
    m = re.search(r"\bstop\s*(id)?\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(2))

    # Hashtag pattern (people paste #473 etc.)
    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # If the entire message is just digits, only accept if len>=3
    if re.fullmatch(r"[0-9]{1,6}", t):
        if len(t) >= 3:
            return normalize_stop_id(t)
        return None

    return None


def wants_schedule(text: str) -> bool:
    """
    Detect schedule intent, including common typos.
    """
    t = (text or "").lower()
    schedule_words = [
        "schedule", "sched", "schedual", "schedul", "timetable",
        "first bus", "first run", "last bus", "last run",
        "what time", "when does", "start", "end",
        "weekday", "weekdays", "mon-fri", "mon fri", "m/f", "m-f",
        # Spanish
        "horario", "tabla", "primero", "ultimo", "último", "a que hora", "a qué hora",
    ]
    return any(k in t for k in schedule_words)


def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    rt_words = [
        "eta", "minutes", "mins", "min", "prediction", "predictions", "arrive", "arrival",
        "next bus", "where is", "vehicle", "location", "real-time", "realtime",
        # Spanish
        "cuantos minutos", "cuántos minutos", "llega", "llegada", "en vivo", "tiempo real", "ubicacion", "ubicación",
    ]
    return any(k in t for k in rt_words)


def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "bus", "route", "rt", "stop",
        "minutes", "mins", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "sched", "schedual", "timetable", "first bus", "last bus",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada", "cuantos minutos", "tiempo real", "ubicacion", "ubicación",
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
    if "rosa" in t and "park" in t:
        return "Rosa Parks"
    if "uf" in t or "campus" in t:
        return "UF Campus"
    return None


def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


def detect_language_simple(text: str) -> str:
    # simple heuristic
    t = (text or "").lower()
    if any(w in t for w in ["hola", "horario", "ruta", "parada", "llega", "cuántos", "ubicación"]):
        return "es"
    return "en"


def parse_when_dt_from_message(msg: str) -> datetime:
    """
    Minimal time parsing:
      - "tomorrow" -> +1 day
      - time like "2pm", "2:15pm", "14:00"
    If no time found -> current time.
    """
    now = datetime.now(TZ)
    base = now

    t = (msg or "").lower()
    if "tomorrow" in t or "mañana" in t:
        base = (now + timedelta(days=1)).replace(hour=now.hour, minute=now.minute, second=0, microsecond=0)

    # time: 2pm / 2:30pm / 14:00
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
    """
    If OPENAI_API_KEY is present and valid, use OpenAI to extract:
      intent, route_id, stop_id, destination_hint, language
    Otherwise return safe fallback.
    """
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
        "- destination_hint is a place name like 'Reitz Union' if mentioned. "
        "- language is 'es' if the user writes Spanish, else 'en'. "
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
# Stop suggestions (Bustime-only)
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
    """
    Bustime-only stop suggestions for a route (search stop names returned by Bustime).
    Returns list of {id, name}.
    """
    route_id = digits_only(route_id or "")
    if not route_id:
        return []

    tokens = _tokenize_for_stop_match(message)
    hint = guess_destination_hint(message)
    if hint:
        tokens += _tokenize_for_stop_match(hint)
    tokens = list(dict.fromkeys(tokens))  # unique

    if not tokens:
        return []

    # Get directions, then stops for each direction
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

    # Fallback directions if API returns none
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


def suggest_stops_by_name(message: str, limit: int = 8) -> list[dict]:
    """
    Bustime-only name search WITHOUT a route is expensive (would require scanning many routes).
    So we return [] and let the agent ask for the route or Stop ID.
    """
    return []


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
# Schedule queries (TEMP placeholder since PDF/DB removed)
# ------------------------------------------------------------
def schedule_next_departures(stop_id: str, route_id: str | None, when_dt: datetime, limit: int = 3) -> dict:
    # You removed the PDF/SQLite path. We'll wire schedules to your website mirror in the next step.
    return {"rows": []}


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
            # too many digits for a normal stop id
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

    # Decide schedule vs realtime
    prefer_schedule = (intent == "schedule") or (wants_schedule(msg) and not wants_realtime(msg))

    # If stop_id missing:
    if not stop_id:
        if route_id:
            candidates = suggest_stops_by_route(route_id, (destination_hint + " " + msg).strip(), limit=8)

            # If we got exactly one strong match, auto-use it
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

        # No stop_id and no route_id (or no matches) -> ask for route or stop id
        if not stop_id:
            return {
                "answer": tmsg(
                    lang,
                    "To check ETA, I need either a 4-digit Stop ID or a route number + place (example: 'ETA Route 1 at Reitz').",
                    "Para ver el ETA, necesito el Stop ID de 4 dígitos o una ruta + lugar (ej: 'ETA Ruta 1 en Reitz')."
                ),
                "sources": [{"type": "need_stop_or_route"}],
            }

    # If schedule requested, currently placeholder
    if prefer_schedule:
        when_dt = parse_when_dt_from_message(msg)
        _ = schedule_next_departures(stop_id=stop_id, route_id=route_id, when_dt=when_dt, limit=3)

        return {
            "answer": tmsg(
                lang,
                "Schedules are being migrated to the RTS website mirror. For now, I can provide real-time ETAs. Ask: 'ETA Route 1 at Reitz' or 'ETA stop 0473'.",
                "Los horarios se están migrando al sitio espejo de RTS. Por ahora, puedo dar ETAs en tiempo real. Pregunta: 'ETA Ruta 1 en Reitz' o 'ETA parada 0473'."
            ),
            "sources": [{"type": "schedule_migrating"}],
        }

    # Otherwise REALTIME predictions
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

    # Keep <=45 min or DUE
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

    return {
        "answer": tmsg(
            lang,
            f"No real-time ETAs (<=45 min) found for Stop {stop_id}. Try another stop or ask for schedule.",
            f"No hay ETAs en tiempo real (<=45 min) para Stop {stop_id}. Prueba otra parada o pide el horario."
        ),
        "sources": [{"type": "realtime_none", "stop_id": stop_id, "route_id": route_id}],
    }


def handle_agent_message(message: str) -> dict:
    """
    This is the function your routes/agent_api.py imports.
    It MUST exist, or Render will crash on import.
    """
    transit = try_transit_answer(message)
    if transit:
        return {
            "answer": transit.get("answer", ""),
            "sources": transit.get("sources", []),
        }

    # Non-transit fallback (simple)
    return {
        "answer": "I can help with RTS real-time ETAs. Try: 'ETA Route 38 stop 1192' or 'ETA Route 1 at Reitz' or just type a Stop ID like '0473'.",
        "sources": [{"type": "fallback"}],
    }
