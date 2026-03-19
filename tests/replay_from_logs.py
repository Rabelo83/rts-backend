"""
Level 2 QA: Replay real user queries from analytics.sqlite against the live v3 agent.
Finds regressions that hand-written scenarios never anticipated.

Usage:
  python tests/replay_from_logs.py                  # last 50 queries, prod
  python tests/replay_from_logs.py --last 100       # last 100 queries
  python tests/replay_from_logs.py --env local      # against local Flask
  python tests/replay_from_logs.py --fails-only     # only print FAIL/WARN
  python tests/replay_from_logs.py --session <id>   # replay one session
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────
PROD_URL  = "https://rts-backend-7ru5.onrender.com"
LOCAL_URL = "http://127.0.0.1:5000"

RESULTS_DIR  = Path(__file__).parent / "results"
_ROOT        = Path(__file__).resolve().parents[1]
_DATA_DIR    = Path(os.environ.get("DATA_DIR", str(_ROOT / "data")))
ANALYTICS_DB = _DATA_DIR / "analytics.sqlite"

# Signals that suggest a bad response — not exhaustive, but catches the worst cases
_FAIL_SIGNALS = [
    "i wasn't able",
    "i'm unable",
    "i cannot",
    "i don't have that information",
    "error connecting",
    "please try again",
]
_WARN_SIGNALS = [
    "i apologize",
    "i made an error",
    "route_not_at_stop",
    "i'm not sure",
    "i don't know which stop",
]


# ── Data loading ─────────────────────────────────────────────────────────────
def load_queries(last_n: int, session_id: str | None) -> list[dict]:
    if not ANALYTICS_DB.exists():
        print(f"  [!] Analytics DB not found at {ANALYTICS_DB}")
        print("      Run against prod with DATA_DIR set, or copy the DB locally.")
        return []
    conn = sqlite3.connect(ANALYTICS_DB)
    conn.row_factory = sqlite3.Row
    try:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM analytics WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM analytics ORDER BY id DESC LIMIT ?", (last_n,)
            ).fetchall()
            rows = list(reversed(rows))
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Replay ────────────────────────────────────────────────────────────────────
def replay(base_url: str, message: str) -> dict:
    try:
        t0 = time.time()
        resp = requests.post(
            f"{base_url}/api/agent/v3",
            json={"message": message},
            timeout=30,
        )
        ms = int((time.time() - t0) * 1000)
        if resp.ok:
            data = resp.json()
            return {"status": "ok", "answer": data.get("answer", ""), "ms": ms, "meta": data.get("meta", {})}
        return {"status": "http_error", "code": resp.status_code, "ms": ms}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "ms": 0}


# ── Scoring ───────────────────────────────────────────────────────────────────
def score(original: dict, rep: dict) -> tuple[str, str]:
    """Returns (verdict, reason)."""
    if rep["status"] != "ok":
        return "FAIL", f"request failed: {rep['status']} {rep.get('code', rep.get('error', ''))}"

    if rep["meta"].get("error"):
        return "FAIL", f"agent error: {rep['meta']['error']}"

    answer_lc = (rep.get("answer") or "").lower()

    for sig in _FAIL_SIGNALS:
        if sig in answer_lc:
            return "FAIL", f"fail signal: '{sig}'"

    for sig in _WARN_SIGNALS:
        if sig in answer_lc:
            return "WARN", f"warn signal: '{sig}'"

    # Original was logged as success=0 — flag if replay also looks weak
    if original.get("success") == 0 and len(rep.get("answer", "")) < 80:
        return "WARN", "original was failure + short reply"

    return "PASS", "ok"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Replay real user queries as regression tests")
    parser.add_argument("--env",        choices=["local", "prod"], default="prod")
    parser.add_argument("--last",       type=int,  default=50,   help="Number of recent queries (default 50)")
    parser.add_argument("--session",    type=str,  default=None, help="Replay specific session_id only")
    parser.add_argument("--fails-only", action="store_true",     help="Only print FAIL/WARN rows")
    parser.add_argument("--delay",      type=float, default=0.4, help="Seconds between requests (default 0.4)")
    args = parser.parse_args()

    base_url = LOCAL_URL if args.env == "local" else PROD_URL
    print(f"\nRTS Replay — {args.env.upper()} ({base_url})")
    print(f"Loading last {args.last} queries from analytics.sqlite…\n")

    queries = load_queries(args.last, args.session)
    if not queries:
        return

    print(f"Loaded {len(queries)} queries.\n{'─'*60}")

    results = []
    counts  = {"PASS": 0, "WARN": 0, "FAIL": 0}
    symbols = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}

    for i, row in enumerate(queries, 1):
        msg = (row.get("message") or "").strip()
        if not msg:
            continue

        prefix = f"[{i:>3}/{len(queries)}]"
        short  = repr(msg[:65])
        print(f"{prefix} {short:<68}", end=" ", flush=True)

        rep     = replay(base_url, msg)
        verdict, reason = score(row, rep)
        counts[verdict] += 1

        print(f"{symbols[verdict]} {verdict:4}  {rep['ms']:>5}ms")

        if not args.fails_only or verdict != "PASS":
            if verdict != "PASS":
                print(f"         reason  : {reason}")
                ans = (rep.get("answer") or "")[:140]
                print(f"         answer  : {ans!r}")
                orig_intent = row.get("intent") or row.get("route") or ""
                if orig_intent:
                    print(f"         original: intent={orig_intent}")

        results.append({
            "original_ts":      row.get("ts_utc"),
            "session_id":       row.get("session_id"),
            "message":          msg,
            "original_success": row.get("success"),
            "original_intent":  row.get("intent"),
            "original_route":   row.get("route"),
            "replay_status":    rep["status"],
            "replay_answer":    (rep.get("answer") or "")[:400],
            "replay_ms":        rep.get("ms", 0),
            "verdict":          verdict,
            "reason":           reason,
        })

        time.sleep(args.delay)

    # ── Save results ──────────────────────────────────────────────
    RESULTS_DIR.mkdir(exist_ok=True)
    ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"replay_{ts}.json"
    out_path.write_text(json.dumps({
        "run_at":   datetime.now(timezone.utc).isoformat(),
        "env":      args.env,
        "base_url": base_url,
        "total":    len(results),
        **counts,
        "results":  results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Summary ───────────────────────────────────────────────────
    total = len(results)
    pct   = round(counts["PASS"] / total * 100) if total else 0
    print(f"\n{'═'*60}")
    print(f"  PASS {counts['PASS']:>4}  WARN {counts['WARN']:>4}  FAIL {counts['FAIL']:>4}  ({pct}% pass rate)")
    print(f"  Saved → {out_path.name}")

    if counts["FAIL"] or counts["WARN"]:
        print(f"\n  Issues ({counts['FAIL']} fail + {counts['WARN']} warn):")
        for r in results:
            if r["verdict"] in ("FAIL", "WARN"):
                print(f"    [{r['verdict']}] {r['message'][:70]!r}")
                print(f"           {r['reason']}")
    print()


if __name__ == "__main__":
    main()
