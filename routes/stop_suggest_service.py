import re
from typing import List, Dict

import rts_api
from utils.text_utils import normalize_stop_id, digits_only


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _guess_destination_hint(text: str) -> str | None:
    t = (text or "").lower()
    if "reitz" in t:
        return "reitz"
    if "hub" in t or "the hub" in t:
        return "hub"
    if "downtown" in t or "centro" in t:
        return "downtown"
    if "oaks" in t or "mall" in t:
        return "oaks"
    if "butler" in t:
        return "butler"
    if "campus" in t or "uf" in t or "universidad" in t:
        return "uf"
    if "rosa" in t and "park" in t:
        return "rosa"
    return None


def _tokenize(text: str) -> List[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return [x for x in t.split() if len(x) >= 3]


def _score_stop_name(stop_name: str, tokens: List[str]) -> int:
    name = (stop_name or "").lower()
    score = 0
    for tok in tokens:
        if tok in name:
            score += 2
        elif len(tok) >= 4 and tok[:4] in name:
            score += 1
    return score


# ------------------------------------------------------------
# Main function used by the agent
# ------------------------------------------------------------
def suggest_stops(route_id: str, text: str, limit: int = 8) -> List[Dict]:
    """
    Suggest likely stop IDs for a given route, based on user text.

    Bustime-only version (NO schedule DB).
    Returns list of:
      { "id": "1192", "name": "Some Stop Name", "source": "bustime" }
    """
    route_id = digits_only(route_id or "")
    if not route_id:
        return []

    hint = _guess_destination_hint(text)
    tokens = _tokenize(text)
    if hint:
        tokens = list(dict.fromkeys(tokens + [hint]))

    bustime_stops = []
    try:
        dirs_data = rts_api.get_directions(route_id) or {}
        dirs_raw = dirs_data.get("directions", []) or []

        dir_ids = []
        for d in dirs_raw:
            # Bustime direction objects vary, so we try common keys
            dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d
            if dir_id is not None:
                dir_ids.append(str(dir_id))

        seen = set()
        for d in dir_ids:
            stops_data = rts_api.get_stops(route_id, d) or {}
            stops_raw = stops_data.get("stops", []) or []
            for s in stops_raw:
                sid = normalize_stop_id(s.get("stpid"))
                nm = (s.get("stpnm") or "").strip()
                if not sid or not nm:
                    continue
                if sid in seen:
                    continue
                seen.add(sid)
                bustime_stops.append({"id": sid, "name": nm, "source": "bustime"})
    except Exception:
        bustime_stops = []

    if not bustime_stops:
        return []

    scored = [(_score_stop_name(s["name"], tokens), s) for s in bustime_stops]
    scored.sort(key=lambda x: x[0], reverse=True)

    # If nothing matched the words, still return valid stop IDs alphabetically
    if scored and scored[0][0] == 0:
        bustime_stops.sort(key=lambda x: x["name"])
        return bustime_stops[:limit]

    return [s for score, s in scored[:limit]]
