import re


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


def extract_context_updates(tool_results: list[dict]) -> dict:
    updates: dict[str, str] = {}

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

        if stop_id and stop_name:
            updates["last_stop_id"] = str(stop_id)
            updates["last_stop_name"] = str(stop_name)

        route_id = result.get("route")
        if route_id:
            updates["last_route_id"] = str(route_id)

    return {k: v for k, v in updates.items() if v}
