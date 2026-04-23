import re

from routes.parsing_helpers import extract_route_id_regex, extract_stop_id_regex, normalize_stop_id


def session_context(session_ctx: dict | None) -> dict:
    if not isinstance(session_ctx, dict):
        return {}
    ctx = session_ctx.get("context")
    if isinstance(ctx, dict):
        return ctx
    return session_ctx


def is_stop_id_followup(msg: str) -> bool:
    text = (msg or "").strip().lower()
    if not text:
        return False

    if not any(token in text for token in ("stop id", "stopid", "id de parada", "id de la parada")):
        return False

    # If the user explicitly names a new place after "for/of/de/para", let the
    # normal agent flow resolve that new place instead of reusing prior context.
    explicit_place = re.search(r"\b(for|of|de|para)\s+([a-z0-9].+)$", text)
    if explicit_place:
        tail = explicit_place.group(2).strip()
        if tail and tail not in {"that", "this", "it", "that one", "this one", "esa", "ese", "eso", "esta"}:
            return False

    patterns = (
        r"^\s*(what(?:'s| is| will be)?\s+(?:the\s+)?)?stop id\??\s*$",
        r"^\s*what(?:'s| is| will be)?\s+(?:the\s+)?stop id\b.*$",
        r"^\s*cu[aá]l\s+es\s+(?:el\s+)?id\s+de\s+(?:la\s+)?parada\b.*$",
        r"^\s*(?:el\s+)?id\s+de\s+(?:la\s+)?parada\??\s*$",
    )
    return any(re.match(pattern, text) for pattern in patterns)


def maybe_answer_stop_id_followup(msg: str, session_ctx: dict | None, lang: str) -> dict | None:
    ctx = session_context(session_ctx)
    stop_id = ctx.get("last_stop_id")
    stop_name = ctx.get("last_stop_name")
    if not stop_id or not stop_name or not is_stop_id_followup(msg):
        return None

    if (lang or "").lower().startswith("es"):
        answer = f"El Stop ID de {stop_name} es {stop_id}."
    else:
        answer = f"The stop ID for {stop_name} is {stop_id}."

    return {
        "answer": answer,
        "buttons": [],
        "meta": {
            "language": lang,
            "stop_id": stop_id,
            "stop_name": stop_name,
            "route": ctx.get("last_route_id"),
            "context_followup": "stop_id",
            "context_updates": {
                "last_stop_id": stop_id,
                "last_stop_name": stop_name,
                "last_route_id": ctx.get("last_route_id"),
            },
        },
    }


def _last_assistant_message(history: list[dict] | None) -> str:
    for turn in reversed(history or []):
        if (turn.get("role") or "").lower() == "assistant":
            return turn.get("content") or ""
    return ""


def maybe_rewrite_route_stop_followup(
    msg: str,
    history: list[dict] | None,
    session_ctx: dict | None,
) -> str | None:
    """
    Preserve a known route when the user replies with only a stop ID after the
    assistant asked about stops in a route-specific thread.
    """
    text = (msg or "").strip()
    if not text or extract_route_id_regex(text):
        return None

    raw_stop_id = None
    stop_match = re.search(r"\bstop\s*(?:id)?\s*[:#]?\s*([0-9]{1,6})\b", text, re.IGNORECASE)
    if stop_match:
        raw_stop_id = stop_match.group(1)
    elif re.fullmatch(r"\d{3,4}", text):
        raw_stop_id = text

    stop_id = extract_stop_id_regex(text)
    if not stop_id and raw_stop_id:
        stop_id = normalize_stop_id(raw_stop_id)
    if not stop_id or not raw_stop_id:
        return None

    ctx = session_context(session_ctx)
    route_id = str(ctx.get("last_route_id") or "").strip()
    if not route_id:
        return None

    last_assistant = _last_assistant_message(history).lower()
    if not last_assistant:
        return None

    mentions_same_route = bool(
        re.search(rf"route\s+{re.escape(route_id)}\b", last_assistant, re.IGNORECASE)
    )
    is_stop_prompt = any(
        phrase in last_assistant
        for phrase in (
            "which stop",
            "what stop",
            "different stop",
            "pick one",
            "choose one",
            "stop id",
            "landmark",
        )
    )
    if not (mentions_same_route and is_stop_prompt):
        return None

    return f"route {route_id} stop {raw_stop_id}"


def _display_stop_id(stop_id: str | None) -> str | None:
    if not stop_id:
        return None
    stop_id = str(stop_id).strip()
    if not stop_id:
        return None
    return stop_id.lstrip("0") or "0"


def add_stop_id_to_answer(answer: str, tool_results: list[dict], lang: str) -> str:
    text = (answer or "").strip()
    if not text:
        return text

    updates = extract_context_updates(tool_results)
    stop_id = _display_stop_id(updates.get("last_stop_id"))
    if not stop_id:
        return text

    lower = text.lower()
    if "stop id" in lower:
        return text
    if re.search(rf"\bstop\s+{re.escape(stop_id)}\b", lower, re.IGNORECASE):
        return text
    if re.search(rf"\({re.escape(stop_id)}\)", text):
        return text

    suffix = f" Stop ID: {stop_id}."
    return f"{text}{suffix}"


def extract_context_updates(tool_results: list[dict]) -> dict:
    updates: dict[str, str] = {}

    def _set(key: str, value) -> None:
        if value is not None and value != "":
            updates[key] = str(value)

    def _capture_direction(result: dict) -> None:
        departures = result.get("departures") or []
        if departures:
            headsigns = {
                dep.get("headsign")
                for dep in departures
                if isinstance(dep, dict) and dep.get("headsign")
            }
            if len(headsigns) == 1:
                _set("last_direction", next(iter(headsigns)))

        directions = result.get("directions") or []
        if len(directions) == 1 and isinstance(directions[0], dict):
            _set("last_direction", directions[0].get("headsign"))

    for tr in tool_results or []:
        if not isinstance(tr, dict):
            continue
        name = tr.get("tool")
        result = tr.get("result") or {}
        if not isinstance(result, dict):
            continue

        stop_id = None
        stop_name = None

        if name == "search_stops" and result.get("status") == "found":
            stop_id = result.get("stop_id")
            stop_name = result.get("stop_name")
        elif name == "get_realtime_predictions" and result.get("stop_id"):
            stop_id = result.get("stop_id")
            stop_name = result.get("stop_name")
        elif name == "get_schedule" and result.get("stop"):
            stop_id = result.get("stop_id")
            stop_name = result.get("stop")
        elif name == "get_vehicle_location" and result.get("status") == "ok":
            vehicles = result.get("vehicles") or []
            if len(vehicles) == 1 and isinstance(vehicles[0], dict):
                stop_id = vehicles[0].get("next_stop_id")
                stop_name = vehicles[0].get("next_stop_name")
                _set("last_direction", vehicles[0].get("destination"))

        if stop_id and stop_name:
            updates["last_stop_id"] = str(stop_id)
            updates["last_stop_name"] = str(stop_name)

        route_id = result.get("route")
        if not route_id and name == "search_routes" and result.get("status") == "ok":
            routes = result.get("routes") or []
            if len(routes) == 1 and isinstance(routes[0], dict):
                route_id = routes[0].get("route_id")
        if route_id:
            updates["last_route_id"] = str(route_id)

        _set("last_date", result.get("date"))
        _set("last_origin", result.get("origin"))
        if name == "search_stops":
            _set("last_destination", result.get("name"))
        else:
            _set("last_destination", result.get("destination"))
        _set("last_service_type", result.get("service_type") or result.get("service_label"))
        _set("last_tool", name)
        _capture_direction(result)

    return {k: v for k, v in updates.items() if v}
