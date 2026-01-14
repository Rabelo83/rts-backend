import re

def digits_only(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")

def normalize_stop_id(s: str):
    if not s:
        return None
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return None
    if len(digits) > 4:
        digits = digits[-4:]
    return digits.zfill(4)

def extract_route_id(text: str):
    t = (text or "").lower()
    m = re.search(r"\b(route|rt|bus)\s*[:#]?\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(2)
    m = re.search(r"\bbus\s*number\s*([0-9]{1,3})\b", t)
    if m:
        return m.group(1)
    return None

def extract_stop_id(text: str):
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

def wants_schedule(text: str) -> bool:
    t = (text or "").lower()
    words = ["schedule","timetable","first bus","first run","last bus","last run","what time","when does","start","end",
             "horario","tabla","primero","ultimo","último","a que hora","a qué hora"]
    return any(w in t for w in words)

def wants_realtime(text: str) -> bool:
    t = (text or "").lower()
    words = ["eta","minutes","min","prediction","predictions","arrive","arrival","next bus","where is","vehicle","location","real-time","realtime",
             "cuantos minutos","cuántos minutos","llega","llegada","en vivo","tiempo real","ubicacion","ubicación"]
    return any(w in t for w in words)

def is_transit_keywords(text: str) -> bool:
    t = (text or "").lower()
    words = ["eta","next bus","bus","route","rt","stop","minutes","min","arrive","arrival","prediction","predictions",
             "schedule","timetable","first bus","last bus","parada","ruta","horario","llega","llegada","cuantos minutos","tiempo real","ubicacion","ubicación"]
    return any(w in t for w in words)

def guess_destination_hint(text: str):
    t = (text or "").lower()
    if "reitz" in t: return "Reitz Union"
    if "oaks" in t: return "Oaks Mall"
    if "downtown" in t: return "Downtown"
    if "hub" in t: return "Hub"
    if "uf" in t or "campus" in t: return "UF Campus"
    return None

def tmsg(lang: str, en: str, es: str) -> str:
    return es if (lang or "").lower().startswith("es") else en
