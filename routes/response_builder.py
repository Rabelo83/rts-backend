"""
Response formatting helpers for the RTS transit assistant.
Produces human-readable strings from raw schedule/prediction data.
"""
from routes.parsing_helpers import tmsg, format_time_12h


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


def format_realtime_answer(lang: str, usable_preds: list[dict]) -> str:
    lines = []
    for p in usable_preds[:6]:
        mins = p.get("minutes")
        rt = p.get("route") or ""
        dest = p.get("destination") or ""
        if isinstance(mins, str) and mins.upper() == "DUE":
            lines.append(tmsg(lang, f"Route {rt} (heading to {dest}): DUE", f"Ruta {rt} (hacia {dest}): YA"))
        else:
            lines.append(tmsg(lang, f"Route {rt} (heading to {dest}): {mins} min", f"Ruta {rt} (hacia {dest}): {mins} min"))

    return tmsg(lang, "Real-time ETA:\n- ", "ETA en tiempo real:\n- ") + "\n- ".join(lines)


def build_direction_prompt(options: str, lang: str, ctx_info: dict | None = None) -> str:
    """Build a direction-clarification prompt deterministically (no LLM call needed)."""
    ctx = ctx_info or {}
    route = ctx.get("route")
    stop = ctx.get("stop")
    parts = []
    if route:
        parts.append(f"Route {route}")
    if stop:
        parts.append(f"Stop {stop}")
    ctx_str = ", ".join(parts)
    if ctx_str:
        en = f"For {ctx_str} — which direction are you headed: {options}?"
        es = f"Para {ctx_str} — ¿hacia qué dirección vas: {options}?"
    else:
        en = f"Which direction are you headed toward: {options}?"
        es = f"¿Hacia cuál dirección vas: {options}?"
    return tmsg(lang, en, es)


def build_exception_note(lang: str, date_str: str, exception_info: dict | None) -> str:
    if not exception_info:
        return ""
    added = exception_info.get("added") or []
    removed = exception_info.get("removed") or []
    if not added and not removed:
        return ""
    return tmsg(
        lang,
        f"Note: Service exceptions apply on {date_str}.",
        f"Nota: Hay excepciones de servicio en {date_str}.",
    )
