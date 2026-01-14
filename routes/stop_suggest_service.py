import rts_api

TOKENS = ["reitz", "hub", "downtown", "oaks", "butler", "campus", "uf"]

def _fetch_all_stops_for_route(route_id: str) -> list[dict]:
    dirs = rts_api.get_directions(route_id).get("directions", [])
    dir_ids = [(d.get("id") or d.get("dir") or d.get("dirId") or d.get("dirid") or d) for d in dirs]

    out = []
    seen = set()
    for d in dir_ids:
        st = rts_api.get_stops(route_id, d).get("stops", []) or []
        for s in st:
            sid = s.get("stpid")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            out.append({"id": sid, "name": s.get("stpnm") or "", "lat": s.get("lat"), "lon": s.get("lon")})
    return out

def suggest_stops_for_route(route_id: str, text: str, limit: int = 8) -> list[dict]:
    stops = _fetch_all_stops_for_route(route_id)
    t = (text or "").lower()

    scored = []
    for s in stops:
        name = (s.get("name") or "").lower()
        score = 0
        for tok in TOKENS:
            if tok in t and tok in name:
                score += 3
        # small boost if user typed part of stop name
        if len(t) >= 3 and any(word in name for word in t.split() if len(word) >= 3):
            score += 1
        if score > 0:
            scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = [s for _, s in scored][:limit]

    # fallback: if no matches, return a few stops (still helpful)
    if not best:
        best = stops[:limit]

    return [{"id": b["id"], "name": b["name"]} for b in best]

def find_best_stop_for_destination(route_id: str, destination_hint: str) -> dict | None:
    if not destination_hint:
        return None
    stops = _fetch_all_stops_for_route(route_id)
    hint = destination_hint.lower()

    matches = []
    for s in stops:
        name = (s.get("name") or "").lower()
        if hint in name:
            matches.append(s)

    return matches[0] if matches else None

def get_stop_name(route_id: str, stop_id: str) -> str | None:
    stops = _fetch_all_stops_for_route(route_id)
    for s in stops:
        if str(s.get("id")) == str(stop_id):
            return s.get("name")
    return None
