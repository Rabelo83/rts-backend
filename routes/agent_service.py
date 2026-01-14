import os, json, traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from openai import OpenAI
from config import API_KEY

import rts_api
import webqa

from services.schedule_service import (
    schedule_next_departures_by_bustime_stop,
    schedule_window_by_destination,
)
from services.stop_suggest_service import (
    suggest_stops_for_route,
    find_best_stop_for_destination,
)
from utils.text_utils import (
    digits_only,
    normalize_stop_id,
    extract_route_id,
    extract_stop_id,
    is_transit_keywords,
    wants_schedule,
    wants_realtime,
    guess_destination_hint,
    tmsg,
)
from utils.time_utils import parse_target_datetime

TZ = ZoneInfo("America/New_York")
client = OpenAI(api_key=API_KEY) if API_KEY else OpenAI()

def llm_extract_intent(text: str) -> dict:
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "You extract transit intent for Gainesville RTS. "
        "Return ONLY JSON with keys: intent, route_id, stop_id, destination_hint, language, when. "
        "Rules: "
        "- intent is one of: eta, schedule, vehicle_location, general. "
        "- route_id is route number like '9' (string) if present, else null. "
        "- stop_id is 4-digit stop ID if present, else null. "
        "- destination_hint is place name like 'Reitz Union' if present, else null. "
        "- language is 'es' if Spanish, else 'en'. "
        "- when: short time like 'tomorrow 10am' if user mentions, else null."
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
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
        when = (obj.get("when") or "").strip() or None

        if language not in ("en", "es"):
            language = "en"

        return {
            "intent": intent,
            "route_id": route_id,
            "stop_id": stop_id,
            "destination_hint": destination_hint,
            "language": language,
            "when": when,
        }
    except Exception as e:
        print("llm_extract_intent_error:", repr(e))
        print(traceback.format_exc())
        return {"intent": "general", "route_id": None, "stop_id": None, "destination_hint": None, "language": "en", "when": None}

def answer_agent(message: str) -> dict:
    msg = (message or "").strip()
    if not msg:
        return {"answer": "Please send a message.", "sources": []}

    # If not transit, use your web QA
    if not is_transit_keywords(msg):
        ans = webqa.answer(msg)
        if isinstance(ans, tuple):
            return {"answer": str(ans[0]), "sources": list(ans[1]) if len(ans) > 1 else []}
        if isinstance(ans, dict):
            return {"answer": str(ans.get("answer") or ans.get("text") or ""), "sources": ans.get("sources") or []}
        return {"answer": str(ans), "sources": []}

    extracted = llm_extract_intent(msg)
    lang = extracted.get("language", "en")
    intent = extracted.get("intent", "general")
    route_id = extracted.get("route_id") or extract_route_id(msg)
    stop_id = extracted.get("stop_id") or extract_stop_id(msg)
    destination_hint = extracted.get("destination_hint") or guess_destination_hint(msg)
    when_text = extracted.get("when")

    # Determine target time (supports: tomorrow 10am, around 10, etc.)
    target_dt = parse_target_datetime(msg, when_text=when_text, tz=TZ)

    # Prefer schedule if user clearly asked schedule (and not realtime)
    prefer_schedule = (intent == "schedule") or (wants_schedule(msg) and not wants_realtime(msg))

    # If user asked schedule by destination (tomorrow 10am at Reitz) but no stop ID:
    if prefer_schedule and route_id and not stop_id and destination_hint:
        return schedule_window_by_destination(
            route_id=route_id,
            destination_hint=destination_hint,
            when_dt=target_dt,
            lang=lang,
        )

    # If missing stop_id, suggest REAL Bustime stops (4-digit)
    if not stop_id:
        if route_id:
            candidates = suggest_stops_for_route(route_id, msg, limit=8)
            if candidates:
                lines = "\n".join([f"- Stop {c['id']}: {c['name']}" for c in candidates])
                return {
                    "answer": tmsg(
                        lang,
                        f"I can help — I just need the Stop ID where you will board.\nHere are stops on Route {route_id} that match your message:\n{lines}\n\nReply with ONE Stop ID.",
                        f"Puedo ayudarte — solo necesito el Stop ID donde vas a abordar.\nEstas paradas de la Ruta {route_id} coinciden con tu mensaje:\n{lines}\n\nResponde con UN Stop ID."
                    ),
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

    # REALTIME first unless prefer_schedule
    if not prefer_schedule:
        try:
            data = rts_api.get_predictions(stop_id)
            preds = data.get("prd", [])

            if route_id:
                preds = [p for p in preds if str(p.get("rt")) == str(route_id)]

            usable = []
            for p in preds:
                mins = p.get("prdctdn")
                if mins is None:
                    continue
                if isinstance(mins, str) and mins.upper() == "DUE":
                    usable.append(p)
                    continue
                try:
                    mi = int(mins)
                    if mi <= 45:
                        usable.append(p)
                except Exception:
                    pass

            if usable:
                lines = []
                for p in usable[:3]:
                    rt = p.get("rt") or ""
                    dest = p.get("des") or ""
                    mins = p.get("prdctdn")
                    if str(mins).upper() == "DUE":
                        lines.append(tmsg(lang, f"Route {rt} to {dest}: DUE", f"Ruta {rt} hacia {dest}: YA"))
                    else:
                        lines.append(tmsg(lang, f"Route {rt} to {dest}: {mins} min", f"Ruta {rt} hacia {dest}: {mins} min"))

                return {
                    "answer": tmsg(lang, "Real-time ETA:\n- ", "ETA en tiempo real:\n- ") + "\n- ".join(lines),
                    "sources": [{"type": "realtime", "stop_id": stop_id, "route_id": route_id}],
                }
        except Exception as e:
            print("predictions_error:", repr(e))
            print(traceback.format_exc())

    # Schedule fallback using stop-name bridge (Bustime stop -> schedule stop_name -> schedule rows)
    return schedule_next_departures_by_bustime_stop(
        route_id=route_id,
        bustime_stop_id=stop_id,
        when_dt=target_dt,
        lang=lang,
    )
