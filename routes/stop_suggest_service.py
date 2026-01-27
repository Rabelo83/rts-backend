import re
from typing import List, Dict

import rts_api
from db import schedule_db
from utils.text_utils import normalize_stop_id, digits_only


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _guess_destination_hint(text: str) -> str | None:
    """
    Very small keyword detector (English + Spanish) to help stop searching.
    """
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
    if "campus" in t or "uf" in t or "universidad" in t or "universidad de florida" in t:
        return "uf"

    return None


def _tokenize(text: str) -> List[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    tokens = [x for x in t.split() if len(x) >= 3]
    return tokens


def _score_stop_name(stop_name: str, tokens: List[str]) -> int:
    """
    Simple scoring: +2 if token appears in stop name, +1 if partial match.
    """
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

    IMPORTANT:
    - We try Bustime stops FIRST (because those stop IDs are real numeric stop signs).
    - If Bustime fails or returns empty, we fallback to schedule_db but ONLY keep numeric stop IDs.

    Returns list of:
      { "id": "1192", "name": "Reitz Union / UF Campus", "source": "bustime" }
    """
    route_id = digits_only(route_id or "")
    if not route_id:
        return []

    hint = _guess_destination_hint(text)
    tokens = _tokenize(text)
    if hint:
        tokens = list(dict.fromkeys(tokens + [hint]))  # add hint, keep unique

    # ----------------------------
    # 1) Bustime-first (best IDs)
    # ----------------------------
    bustime_stops = []
    try:
        dirs_data = rts_api.get_directions(route_id) or {}
        dirs_raw = dirs_data.get("directions", []) or []

        dir_ids = []
        for d in dirs_raw:
            dir_id = d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d
            if dir_id is not None:
                dir_ids.append(str(dir_id))

        # Pull stops for each direction and combine
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

    if bustime_stops:
        # Score and return best matches
        scored = []
        for s in bustime_stops:
            scored.append((_score_stop_name(s["name"], tokens), s))

        scored.sort(key=lambda x: x[0], reverse=True)

        # If no good scores, just return the first N alphabetically (still valid IDs)
        if scored and scored[0][0] == 0:
            bustime_stops.sort(key=lambda x: x["name"])
            return bustime_stops[:limit]

        return [s for score, s in scored[:limit]]

    # ----------------------------
    # 2) Fallback: schedule_db (filter numeric IDs only)
    # ----------------------------
    schedule_stops = []
    try:
        # We request a lot, then filter down
        raw = schedule_db.route_stops(route_id, service_id="mon_fri", q=None, limit=500) or []
        for s in raw:
            sid_raw = s.get("stop_id") or s.get("id")
            sid = normalize_stop_id(sid_raw)

            # ONLY allow numeric 4-digit IDs.
            # This prevents weird values like "butler_11_48_plaza_r".
            if not sid:
                continue
            nm = (s.get("stop_name") or s.get("name") or "").strip()
            if not nm:
                continue
            schedule_stops.append({"id": sid, "name": nm, "source": "schedule_db"})
    except Exception:
        schedule_stops = []

    if not schedule_stops:
        return []

    scored = []
    for s in schedule_stops:
        scored.append((_score_stop_name(s["name"], tokens), s))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored and scored[0][0] == 0:
        schedule_stops.sort(key=lambda x: x["name"])
        return schedule_stops[:limit]

    return [s for score, s in scored[:limit]]
