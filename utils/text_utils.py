import re

def normalize_stop_id(s: str) -> str | None:
    """
    Normalize a stop ID to 4 digits.
    Accepts: 1, 01, 001, 0001, 1192, "Stop 1192", "#1192", etc.
    """
    if not s:
        return None
    digits = re.sub(r"[^0-9]", "", str(s))
    if not digits:
        return None
    if len(digits) > 4:
        digits = digits[-4:]
    return digits.zfill(4)

def digits_only(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")

def extract_route_id(text: str) -> str | None:
    """
    Recognizes:
    "route 9", "rt 21", "bus 9", "bus #9", "route:12", "bus number 9"
    """
    t = (text or "").lower()

    m = re.search(r"\b(route|rt|bus)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)

    m = re.search(r"\bbus\s*number\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(1)

    return None

def extract_stop_id(text: str) -> str | None:
    """
    Extract a stop ID from free text.
    Supports short forms: "stop 1", "stop 01", "#1", etc.
    """
    t = (text or "").lower()

    m = re.search(r"\bstop\s*[:#]?\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    m = re.search(r"#\s*([0-9]{1,6})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    # Last resort: only 4-digit numbers
    m = re.search(r"\b([0-9]{4})\b", t)
    if m:
        return normalize_stop_id(m.group(1))

    return None
