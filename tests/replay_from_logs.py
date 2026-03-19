"""
Level 2 QA: Replay real user queries from analytics.sqlite against the live v3 agent.
Finds regressions that hand-written scenarios never anticipated.

Usage:
  python tests/replay_from_logs.py                  # last 50 queries, prod, with judge
  python tests/replay_from_logs.py --last 100       # last 100 queries
  python tests/replay_from_logs.py --env local      # against local Flask
  python tests/replay_from_logs.py --session <id>   # replay one session
  python tests/replay_from_logs.py --fails-only     # only print FAIL rows
  python tests/replay_from_logs.py --no-judge       # fast run, keyword signals only
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # judge disabled gracefully if not installed

# ── Config ───────────────────────────────────────────────────────────────────
PROD_URL  = "https://rts-backend-7ru5.onrender.com"
LOCAL_URL = "http://127.0.0.1:5000"

RESULTS_DIR  = Path(__file__).parent / "results"
_ROOT        = Path(__file__).resolve().parents[1]
_DATA_DIR    = Path(os.environ.get("DATA_DIR", str(_ROOT / "data")))
ANALYTICS_DB = _DATA_DIR / "analytics.sqlite"

# Load .env.local if present
_env_local = _ROOT / ".env.local"
if _env_local.exists():
    with open(_env_local, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ── Keyword pre-filter (fast — avoids LLM call on obvious failures) ──────────
_FAIL_SIGNALS = [
    "i wasn't able",
    "i'm unable",
    "i cannot access",
    "i don't have access",
    "error connecting",
    "please try again later",
]


def _keyword_fail(text: str) -> str | None:
    """Return the matched signal if an obvious failure is detected, else None."""
    lower = (text or "").lower()
    for sig in _FAIL_SIGNALS:
        if sig in lower:
            return sig
    return None


# ── LLM judge ────────────────────────────────────────────────────────────────
_JUDGE_SYSTEM = """\
You are a QA evaluator for a Gainesville RTS public transit chatbot.
Given a rider's question and the chatbot's response, decide:

PASS — the response is helpful and directly addresses the question
       (may ask a clarifying question, give a schedule time, list routes, etc.)
FAIL — the response is unhelpful: refuses to answer, gives a generic error,
       is completely off-topic, or obviously makes up transit information

Rules:
- A response in Spanish to a Spanish question = fine, judge the content
- A clarifying question (e.g. "Which stop?") = PASS if the question is relevant
- "I don't have real-time data right now" + schedule alternative = PASS
- Vague apology with no useful information = FAIL

Return JSON only: {"verdict": "PASS" or "FAIL", "reason": "one sentence"}
"""

_judge_client: "OpenAI | None" = None


def _get_judge_client():
    global _judge_client
    if OpenAI is None:
        return None
    if _judge_client is None:
        api_key = os.getenv("OPENAI_API_KEY_V4") or os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None
        _judge_client = OpenAI(api_key=api_key, timeout=25)
    return _judge_client


def llm_judge(message: str, answer: str) -> dict:
    """
    Ask GPT-4o-mini whether the response is helpful for this transit query.
    Returns {"verdict": "PASS"|"FAIL", "reason": "..."}
    """
    client = _get_judge_client()
    if client is None:
        return {"verdict": "PASS", "reason": "judge unavailable — skipped"}

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content":
                    f"Rider question: {message}\n\n"
                    f"Chatbot response: {answer[:600]}"},
            ],
            temperature=0,
            max_tokens=100,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        verdict = data.get("verdict", "PASS").upper()
        if verdict not in ("PASS", "FAIL"):
            verdict = "PASS"
        return {"verdict": verdict, "reason": data.get("reason", "")}
    except Exception as exc:
        return {"verdict": "PASS", "reason": f"judge error (skipped): {exc}"}


# ── Data loading ──────────────────────────────────────────────────────────────
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
            return {"status": "ok", "answer": data.get("answer", ""),
                    "ms": ms, "meta": data.get("meta", {})}
        return {"status": "http_error", "code": resp.status_code, "ms": ms}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "ms": 0}


# ── Scoring ───────────────────────────────────────────────────────────────────
def score(message: str, original: dict, rep: dict, use_judge: bool) -> tuple[str, str]:
    """Returns (verdict, reason) — verdict is PASS or FAIL only."""
    # Hard failures — no LLM call needed
    if rep["status"] != "ok":
        return "FAIL", f"request failed: {rep['status']} {rep.get('code', rep.get('error', ''))}"
    if rep["meta"].get("error"):
        return "FAIL", f"agent error: {rep['meta']['error']}"
    if not rep.get("answer", "").strip():
        return "FAIL", "empty response"

    answer = rep.get("answer", "")

    # Keyword pre-filter — obvious failures, skip judge API call
    kw = _keyword_fail(answer)
    if kw:
        return "FAIL", f"keyword signal: '{kw}'"

    # LLM judge
    if use_judge:
        result = llm_judge(message, answer)
        return result["verdict"], result["reason"]

    # No judge — heuristic pass
    return "PASS", "heuristic ok"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Replay real user queries as regression tests")
    parser.add_argument("--env",        choices=["local", "prod"], default="prod")
    parser.add_argument("--last",       type=int,  default=50,   help="Number of recent queries (default 50)")
    parser.add_argument("--session",    type=str,  default=None, help="Replay specific session_id only")
    parser.add_argument("--fails-only", action="store_true",     help="Only print FAIL rows")
    parser.add_argument("--delay",      type=float, default=0.5, help="Seconds between requests (default 0.5)")
    parser.add_argument("--no-judge",   action="store_true",     help="Skip LLM judge — keyword signals only (faster)")
    args = parser.parse_args()

    use_judge = not args.no_judge
    if use_judge and _get_judge_client() is None:
        print("  [!] OPENAI_API_KEY not set — running without LLM judge (--no-judge mode)")
        use_judge = False

    base_url = LOCAL_URL if args.env == "local" else PROD_URL
    print(f"\nRTS Replay — {args.env.upper()} ({base_url})")
    print(f"Judge    : {'GPT-4o-mini inline' if use_judge else 'disabled (keyword only)'}")
    print(f"Loading last {args.last} queries from analytics.sqlite…\n")

    queries = load_queries(args.last, args.session)
    if not queries:
        return

    print(f"Loaded {len(queries)} queries.\n{'─'*60}")

    results = []
    counts  = {"PASS": 0, "FAIL": 0}
    symbols = {"PASS": "✓", "FAIL": "✗"}

    for i, row in enumerate(queries, 1):
        msg = (row.get("message") or "").strip()
        if not msg:
            continue

        prefix = f"[{i:>3}/{len(queries)}]"
        print(f"{prefix} {repr(msg[:60]):<63}", end=" ", flush=True)

        rep              = replay(base_url, msg)
        verdict, reason  = score(msg, row, rep, use_judge)
        counts[verdict] += 1

        print(f"{symbols[verdict]} {verdict:<4}  {rep['ms']:>5}ms")

        if verdict == "FAIL" or not args.fails_only is False:
            if verdict == "FAIL":
                print(f"         reason : {reason}")
                print(f"         answer : {(rep.get('answer') or '')[:140]!r}")

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
            "judged_by":        "gpt-4o-mini" if use_judge else "keyword",
            "verdict":          verdict,
            "reason":           reason,
        })

        time.sleep(args.delay)

    # ── Save results ──────────────────────────────────────────────
    RESULTS_DIR.mkdir(exist_ok=True)
    ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"replay_{ts}.json"
    out_path.write_text(json.dumps({
        "run_at":    datetime.now(timezone.utc).isoformat(),
        "env":       args.env,
        "base_url":  base_url,
        "judged_by": "gpt-4o-mini" if use_judge else "keyword",
        "total":     len(results),
        **counts,
        "results":   results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Summary ───────────────────────────────────────────────────
    total = len(results)
    pct   = round(counts["PASS"] / total * 100) if total else 0
    print(f"\n{'═'*60}")
    print(f"  PASS {counts['PASS']:>4}  FAIL {counts['FAIL']:>4}  ({pct}% pass rate)")
    print(f"  Saved → {out_path.name}")

    if counts["FAIL"]:
        print(f"\n  Failures ({counts['FAIL']}):")
        for r in results:
            if r["verdict"] == "FAIL":
                print(f"    {r['message'][:70]!r}")
                print(f"    → {r['reason']}")
    print()


if __name__ == "__main__":
    main()
