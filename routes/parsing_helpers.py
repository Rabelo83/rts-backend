"""
Pure text/regex utility functions for the RTS transit assistant.
No external service dependencies — safe to import anywhere.
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

# ── Stop / Route extraction ──────────────────────────────────────────────────

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


def extract_any_stop_candidate(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"\b([0-9]{3,4})\b", text)
    if m:
        return normalize_stop_id(m.group(1))
    return None


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

    # Heuristic: "arrive at 1612" or "at 1612" in a transit query
    m = re.search(r"\b(at|arrive at|arrival at)\s+([0-9]{3,6})\b", t)
    if m:
        return normalize_stop_id(m.group(2))

    return None


# ── Intent keyword detection ─────────────────────────────────────────────────

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


def has_explicit_timeframe(text: str) -> bool:
    t = (text or '').lower()
    # explicit times like 2pm, 2:30 pm
    if re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", t):
        return True
    if "noon" in t or "midnight" in t:
        return True
    # explicit dates like 2026-01-31 or 01/31/2026
    if re.search(r"20\d{2}-\d{2}-\d{2}", t) or re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", t):
        return True
    # time hints
    time_words = [
        'after', 'before', 'around', 'by',
        'today', 'tomorrow', 'tonight',
        'morning', 'afternoon', 'evening',
        'weekday', 'weekdays', 'weekend',
        'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
        # Spanish (ASCII only)
        'hoy', 'manana', 'tarde', 'noche',
        'lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo',
    ]
    return any(k in t for k in time_words)


def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "eta", "next bus", "next", "bus", "route", "rt", "stop",
        "minutes", "mins", "min", "arrive", "arrival", "prediction", "predictions",
        "schedule", "sched", "schedual", "timetable", "first bus", "last bus",
        "when", "leaving", "depart", "departure", "heading",
        # Spanish
        "parada", "ruta", "horario", "llega", "llegada", "cuantos minutos", "tiempo real",
        "ubicacion", "ubicación", "mañana", "cuando", "cuándo", "sale", "salida",
    ]
    return any(k in t for k in keywords)


# ── Destination / origin hints ───────────────────────────────────────────────

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


def extract_origin_place(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"(from|leaving|at)\s+(.+?)(?:\s+on|\s+at|\s+around|\?|$)", text, re.IGNORECASE)
    if m:
        cand = m.group(2).strip()
        if re.search(r"\d", cand) or re.search(r"\b(am|pm)\b", cand.lower()):
            return None
        return cand
    return None


# ── Language / i18n ──────────────────────────────────────────────────────────

def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en


def detect_language_simple(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ["hola", "horario", "ruta", "parada", "llega", "cuántos", "ubicación", "mañana"]):
        return "es"
    return "en"


# ── Place name normalization ─────────────────────────────────────────────────

PLACE_TOKEN_RE = re.compile(r"[^a-z0-9]+")
PLACE_SYNONYMS = {
    "rosa parks downtown station": {
        "rosa parks",
        "rosa parks downtown station",
        "downtown station",
        "rosa parks station",
        "rosa parks transfer",
        "rosa parks transfer station",
        "downtown transfer station",
    },
}


def _normalize_place(text: str | None) -> str:
    if not text:
        return ""
    norm = PLACE_TOKEN_RE.sub(" ", text.lower()).strip()
    if not norm:
        return ""
    for canonical, variants in PLACE_SYNONYMS.items():
        for variant in variants:
            vnorm = PLACE_TOKEN_RE.sub(" ", variant.lower()).strip()
            if not vnorm:
                continue
            if vnorm in norm or norm in vnorm:
                return canonical
    return norm


def _filter_headsigns_by_origin(
    headsigns: list[str],
    origin_hint: str | None,
    stop_name: str | None = None
) -> tuple[list[str], bool]:
    origin_norm = _normalize_place(origin_hint) or _normalize_place(stop_name)
    if not origin_norm:
        return headsigns, False
    trimmed = []
    for h in headsigns:
        base = re.sub(r"^(to|toward|towards)\s+", "", h or "", flags=re.IGNORECASE)
        norm = _normalize_place(base)
        if norm and norm == origin_norm:
            continue
        trimmed.append(h)
    if trimmed:
        return trimmed, True
    return headsigns, False


# ── Follow-up / next detection ───────────────────────────────────────────────

def _explicit_date_or_weekday(text: str) -> bool:
    t = (text or "").lower()
    if re.search(r"20\d{2}-\d{2}-\d{2}", t) or re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", t):
        return True
    if any(w in t for w in ("today", "tomorrow", "tonight")):
        return True
    if any(w in t for w in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
        return True
    return False


def _is_next_request(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in (
        "next",
        "next one",
        "the next one",
        "next bus",
        "next route",
        "next departure",
        "soonest",
        "next?",
    )


def _is_followup_after(text: str) -> bool:
    """Detect natural-language 'after that' follow-ups that mean 'next departure after the last shown'.

    Returns False when the user provides an explicit time ("what about after 6pm?" is a
    new query with a specific time, NOT a vague 'show me the next one' follow-up).
    """
    t = (text or "").strip().lower()
    # If an explicit time is present (e.g. "6pm", "3:30am"), it's a new time query — not a follow-up
    if re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", t):
        return False
    patterns = [
        "after that", "the one after", "next after", "what about after",
        "one after that", "after this one", "what comes after",
    ]
    return any(p in t for p in patterns)


def _extract_last_departure_time(assistant_text: str) -> str | None:
    """
    Extract the last departure time mentioned in an assistant schedule response.
    e.g. '- 3:30 PM (To NW 13th St)' → '3:30pm'
    Returns a string like '3:30pm' suitable for injecting into the next msg_ctx.
    """
    if not assistant_text:
        return None
    matches = re.findall(r"\b(\d{1,2}:\d{2})\s*(AM|PM)\b", assistant_text, re.IGNORECASE)
    if matches:
        h, ap = matches[-1]
        return f"{h}{ap.lower()}"
    return None


def _advance_time_one_minute(time_str: str) -> str:
    """
    Advance a time string by one minute.
    '5:16pm' → '5:17pm'
    Prevents GTFS >= query from re-showing the same departure when the user asks
    for 'the one after that'.
    """
    m = re.match(r"^(\d{1,2}):(\d{2})(am|pm)$", (time_str or "").lower().replace(" ", ""))
    if not m:
        return time_str
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    # Convert to 24h
    if ap == "pm" and h != 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    total = h * 60 + mi + 1
    h, mi = (total // 60) % 24, total % 60
    ap = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{mi:02d}{ap}"


def _has_next_intent(text: str) -> bool:
    t = (text or "").lower()
    if "first" in t or "last" in t:
        return False
    return any(kw in t for kw in ("next", "soonest", "upcoming", "leaving", "depart"))


def _has_time_of_day(text: str) -> bool:
    """
    Return True only when an explicit time-of-day is present — NOT just a date.

    Catches:  '3pm', '3:30pm', 'noon', 'midnight', 'morning', 'afternoon',
              'evening', 'tonight', 'now' (word-boundary).
    Skips:    'tomorrow', 'today', 'weekday', 'monday' … (date-only words).
    Used to decide whether a schedule query needs a time-frame prompt.
    """
    t = (text or "").lower()
    if re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", t):
        return True
    if re.search(r"\bnoon\b", t) or "midnight" in t:
        return True
    if re.search(r"\bnow\b", t):
        return True
    return any(w in t for w in ("morning", "afternoon", "evening", "tonight"))


def _normalize_time_tokens(text: str) -> str:
    if not text:
        return text
    t = text.lower()
    t = re.sub(r"\bnoon time\b", "noon", t)
    t = re.sub(r"\bmidnight time\b", "midnight", t)
    # normalize odd separators like "12..00pm" -> "12:00pm" (allow 1-digit minutes)
    def _pad_minutes(match):
        hh = match.group(1)
        mm = match.group(2) or "0"
        ap = match.group(3)
        if len(mm) == 1:
            mm = mm.zfill(2)
        return f"{hh}:{mm} {ap}"
    t = re.sub(r"\b(\d{1,2})\D{1,3}(\d{1,2})\s*(am|pm)\b", _pad_minutes, t)
    return t


def _has_strong_context(text: str) -> bool:
    if not text:
        return False
    has_route = bool(extract_route_id_regex(text))
    has_stop = bool(extract_stop_id_regex(text))
    has_time = has_explicit_timeframe(text)
    has_place_keywords = bool(re.search(r"\b(from|at|near|leaving|stop)\b", text.lower()))

    if has_route or has_stop:
        return True
    if guess_destination_hint(text) and (has_route or has_stop or has_time or has_place_keywords):
        return True
    if has_time:
        return True
    if has_place_keywords:
        return True
    return False


# ── Time parsing / formatting ─────────────────────────────────────────────────

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

    m = re.search(r"\b([0-9]{1,2})(?::([0-9]{1,2}))?\s*(am|pm)?\b", t)
    if m:
        hh = int(m.group(1))
        mm_raw = m.group(2) or "0"
        if len(mm_raw) == 1:
            mm_raw = mm_raw.zfill(2)
        mm = int(mm_raw)
        ap = (m.group(3) or "").lower()

        if ap == "pm" and hh != 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0

        hh = max(0, min(23, hh))
        mm = max(0, min(59, mm))

        base = base.replace(hour=hh, minute=mm, second=0, microsecond=0)

    return base


def format_time_12h(hhmmss: str) -> str:
    if not hhmmss:
        return hhmmss
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", hhmmss.strip())
    if not m:
        return hhmmss
    hh = int(m.group(1))
    mm = int(m.group(2))
    ap = "AM" if hh < 12 else "PM"
    h12 = hh % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{mm:02d} {ap}"


def normalize_times_in_text(text: str) -> str:
    if not text:
        return text
    def repl(m):
        return format_time_12h(m.group(0))
    return re.sub(r"\b\d{1,2}:\d{2}:\d{2}\b", repl, text)
