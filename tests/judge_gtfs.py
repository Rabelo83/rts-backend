"""
Level 1c — GTFS-Grounded Verifier
===================================
Extracts factual claims from an agent response (routes, stops, departure times,
negative-service assertions) and verifies each one directly against the GTFS
SQLite database.

This closes the gap the LLM judge cannot cover: factual accuracy of times,
headsigns, and route-stop relationships.

Usage (standalone):
  python tests/judge_gtfs.py --query "When does route 15 leave stop 221?" \\
                              --response "Route 15 departs at 6:00 AM."

Usage (as a module from run_and_judge.py):
  from tests.judge_gtfs import gtfs_verify
  result = gtfs_verify(query, response)
  # result: {"verdict": "PASS"|"FAIL"|"UNVERIFIABLE", "checks": [...], "reason": "..."}

Integration with run_and_judge.py:
  Pass --gtfs-verify flag to run_and_judge.py (see that file).
"""

import argparse
import re
import sqlite3
from pathlib import Path

# ── GTFS DB path (mirrors schedule_service.py) ───────────────────────────────
_ROOT    = Path(__file__).resolve().parents[1]
GTFS_DB  = _ROOT / "Backend Basics" / "db" / "rts_gtfs.sqlite"


# ── Regex patterns ────────────────────────────────────────────────────────────
_RE_TIME    = re.compile(r'\b(\d{1,2}:\d{2})\s*(AM|PM|am|pm)\b')
_RE_ROUTE   = re.compile(r'[Rr]ou?t[ae]\s*(\d+)')           # "Route 15", "Ruta 10"
_RE_STOP    = re.compile(r'[Ss]top\s+(\d+)')
_RE_DOES_NOT_SERVE = re.compile(
    r"(does not serve|doesn't serve|not stop|never stop"
    r"|no trips|no service|doesn't stop|does not stop)",
    re.IGNORECASE,
)

# ── GTFS helpers ──────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    if not GTFS_DB.exists():
        raise FileNotFoundError(f"GTFS DB not found at {GTFS_DB}")
    return sqlite3.connect(GTFS_DB)


def _to_gtfs_time(time_str: str, meridiem: str) -> str:
    """
    Convert '6:00' + 'AM' → '06:00:00'
    Convert '3:45' + 'PM' → '15:45:00'
    GTFS times can exceed 24:00 for overnight trips — we only match 00-23 here.
    """
    h, m = map(int, time_str.split(":"))
    if meridiem.upper() == "PM" and h != 12:
        h += 12
    elif meridiem.upper() == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}:00"


def route_exists(route_num: str) -> bool:
    """Check if route_short_name exists in routes table."""
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM routes WHERE route_short_name = ? LIMIT 1",
            (str(route_num),)
        ).fetchone()
    return row is not None


def stop_exists(stop_id: str) -> bool:
    """Check if stop_id (or zero-padded variant) exists in stops table."""
    padded = stop_id.zfill(7)
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM stops WHERE stop_id = ? OR stop_id_padded = ? LIMIT 1",
            (stop_id, padded)
        ).fetchone()
    return row is not None


def route_serves_stop(route_num: str, stop_id: str) -> bool:
    """
    Return True if any GTFS trip for route_short_name visits stop_id.
    Checks both raw and zero-padded stop_id.
    """
    padded = stop_id.zfill(7)
    with _conn() as c:
        row = c.execute(
            """
            SELECT 1
            FROM stop_times st
            JOIN trips t ON t.trip_id = st.trip_id
            JOIN routes r ON r.route_id = t.route_id
            WHERE r.route_short_name = ?
              AND (st.stop_id = ? OR st.stop_id = ?)
            LIMIT 1
            """,
            (str(route_num), stop_id, padded)
        ).fetchone()
    return row is not None


def departure_exists(route_num: str, stop_id: str,
                     gtfs_time: str, tolerance_min: int = 10) -> bool:
    """
    Return True if GTFS has a departure for route_num at stop_id
    within ±tolerance_min minutes of gtfs_time.
    Handles GTFS times > 24:00 by converting to minutes-since-midnight.
    """
    padded = stop_id.zfill(7)
    h, m, _ = gtfs_time.split(":")
    target_minutes = int(h) * 60 + int(m)
    low  = target_minutes - tolerance_min
    high = target_minutes + tolerance_min

    with _conn() as c:
        rows = c.execute(
            """
            SELECT st.departure_time
            FROM stop_times st
            JOIN trips t ON t.trip_id = st.trip_id
            JOIN routes r ON r.route_id = t.route_id
            WHERE r.route_short_name = ?
              AND (st.stop_id = ? OR st.stop_id = ?)
            """,
            (str(route_num), stop_id, padded)
        ).fetchall()

    for (dep_time,) in rows:
        try:
            dh, dm, _ = dep_time.split(":")
            dep_minutes = int(dh) * 60 + int(dm)
            if low <= dep_minutes <= high:
                return True
        except Exception:
            continue
    return False


# ── Claim extraction ──────────────────────────────────────────────────────────

def extract_claims(query: str, response: str) -> dict:
    """
    Extract verifiable claims from the agent response.
    Returns:
    {
        "routes":     ["15", "1", ...],
        "stops":      ["221", "1492", ...],
        "times":      [("6:00", "AM"), ...],
        "gtfs_times": ["06:00:00", ...],
        "negative_service": bool,   # response says route does NOT serve stop
    }
    """
    routes = list(dict.fromkeys(
        m.group(1) for m in _RE_ROUTE.finditer(response + " " + query)
    ))
    stops = list(dict.fromkeys(
        m.group(1) for m in _RE_STOP.finditer(response + " " + query)
    ))
    time_matches = _RE_TIME.findall(response)
    gtfs_times = []
    for t, mer in time_matches:
        try:
            gtfs_times.append(_to_gtfs_time(t, mer))
        except Exception:
            pass

    negative = bool(_RE_DOES_NOT_SERVE.search(response))

    return {
        "routes":          routes,
        "stops":           stops,
        "times":           time_matches,
        "gtfs_times":      gtfs_times,
        "negative_service": negative,
    }


# ── Main verifier ─────────────────────────────────────────────────────────────

def gtfs_verify(query: str, response: str,
                tolerance_min: int = 10) -> dict:
    """
    Verify factual claims in the agent response against GTFS.

    Returns:
    {
        "verdict":  "PASS" | "FAIL" | "UNVERIFIABLE",
        "reason":   "one-line summary",
        "checks":   [{"claim": "...", "result": "verified|contradicted|unverifiable", "detail": "..."}]
    }

    PASS         — every verifiable claim checked out
    FAIL         — at least one claim is contradicted by GTFS
    UNVERIFIABLE — not enough structured facts to check (e.g. only a greeting)
    """
    checks = []

    try:
        claims = extract_claims(query, response)
    except FileNotFoundError as e:
        return {"verdict": "UNVERIFIABLE", "reason": str(e), "checks": []}

    routes = claims["routes"]
    stops  = claims["stops"]
    gtfs_times = claims["gtfs_times"]
    negative   = claims["negative_service"]

    # ── Check 1: routes exist ────────────────────────────────────────────────
    for r in routes:
        exists = route_exists(r)
        checks.append({
            "claim":  f"Route {r} exists",
            "result": "verified" if exists else "contradicted",
            "detail": f"route_short_name={r} {'found' if exists else 'NOT found'} in GTFS",
        })

    # ── Check 2: stops exist ─────────────────────────────────────────────────
    for s in stops:
        exists = stop_exists(s)
        checks.append({
            "claim":  f"Stop {s} exists",
            "result": "verified" if exists else "contradicted",
            "detail": f"stop_id={s} {'found' if exists else 'NOT found'} in GTFS",
        })

    # ── Check 3: route-stop relationship ─────────────────────────────────────
    if routes and stops:
        for r in routes:
            for s in stops:
                serves = route_serves_stop(r, s)
                if negative:
                    # Agent said route does NOT serve stop — verify that claim
                    correct = not serves
                    checks.append({
                        "claim":  f"Route {r} does NOT serve stop {s}",
                        "result": "verified" if correct else "contradicted",
                        "detail": (
                            f"GTFS confirms Route {r} has no trips at stop {s}"
                            if correct
                            else f"GTFS shows Route {r} DOES have trips at stop {s} — agent claim is wrong"
                        ),
                    })
                else:
                    # Agent implied route serves stop — verify
                    checks.append({
                        "claim":  f"Route {r} serves stop {s}",
                        "result": "verified" if serves else "contradicted",
                        "detail": (
                            f"GTFS confirms Route {r} visits stop {s}"
                            if serves
                            else f"GTFS has NO trips for Route {r} at stop {s}"
                        ),
                    })

    # ── Check 4: departure times ─────────────────────────────────────────────
    if routes and stops and gtfs_times and not negative:
        for r in routes:
            for s in stops:
                for gt, (raw_t, mer) in zip(gtfs_times, claims["times"]):
                    found = departure_exists(r, s, gt, tolerance_min)
                    checks.append({
                        "claim":  f"Route {r} departs stop {s} at {raw_t} {mer}",
                        "result": "verified" if found else "contradicted",
                        "detail": (
                            f"GTFS has a departure within ±{tolerance_min}min of {gt}"
                            if found
                            else f"No GTFS departure for Route {r} at stop {s} near {gt} (±{tolerance_min}min)"
                        ),
                    })

    # ── Verdict ───────────────────────────────────────────────────────────────
    if not checks:
        return {
            "verdict": "UNVERIFIABLE",
            "reason":  "No structured facts (routes/stops/times) extracted from response",
            "checks":  [],
        }

    contradicted = [c for c in checks if c["result"] == "contradicted"]
    verified     = [c for c in checks if c["result"] == "verified"]

    if contradicted:
        reasons = "; ".join(c["detail"] for c in contradicted[:2])
        return {"verdict": "FAIL", "reason": reasons, "checks": checks}

    if verified:
        return {
            "verdict": "PASS",
            "reason":  f"{len(verified)} claim(s) verified against GTFS",
            "checks":  checks,
        }

    return {"verdict": "UNVERIFIABLE", "reason": "All checks were unverifiable", "checks": checks}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GTFS-grounded response verifier")
    parser.add_argument("--query",    required=True, help="Original user query")
    parser.add_argument("--response", required=True, help="Agent response to verify")
    parser.add_argument("--tolerance", type=int, default=10,
                        help="Minutes tolerance for time matching (default 10)")
    args = parser.parse_args()

    result = gtfs_verify(args.query, args.response, args.tolerance)

    print(f"\nVerdict  : {result['verdict']}")
    print(f"Reason   : {result['reason']}")
    if result["checks"]:
        print(f"\nChecks ({len(result['checks'])}):")
        for c in result["checks"]:
            icon = {"verified": "OK", "contradicted": "FAIL", "unverifiable": "?"}.get(c["result"], "?")
            print(f"  [{icon}] {c['claim']}")
            print(f"       {c['detail']}")
    print()


if __name__ == "__main__":
    main()
