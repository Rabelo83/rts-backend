#!/usr/bin/env python3
"""
RTS Agent — Run scenarios + inline LLM-as-judge (Level 1 QA)
=============================================================
Runs every scenario in scenarios_v2.json, then immediately asks GPT-4o-mini
for a real PASS/FAIL verdict — no keyword signals, no copy-paste to ChatGPT.
Optional --gtfs-verify flag adds a second layer: GTFS-grounded factual checks.

Usage:
  python tests/run_and_judge.py                        # prod v3 (default)
  python tests/run_and_judge.py --env local            # localhost:5000
  python tests/run_and_judge.py --env prod_v4          # test v4 (GPT)
  python tests/run_and_judge.py --ids S01,S07,M01      # specific scenarios
  python tests/run_and_judge.py --retry-fails          # re-run last failures
  python tests/run_and_judge.py --no-judge             # skip GPT, heuristic only
  python tests/run_and_judge.py --judge-fails-only     # GPT only on heuristic fails
  python tests/run_and_judge.py --gtfs-verify          # add GTFS factual layer

Output:
  tests/results/judged_YYYYMMDD_HHMMSS.json
"""

import argparse
import datetime
import glob
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed.  Run: pip install requests")
    sys.exit(1)

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: 'openai' not installed.  Run: pip install openai")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT   = SCRIPT_DIR.parent
RESULTS_DIR = SCRIPT_DIR / "results"
DEFAULT_SCENARIOS = SCRIPT_DIR / "scenarios_v2.json"
_LOCAL_ENDPOINT   = "local:testclient"

# Ensure repo root is on sys.path so 'from app import create_app' works
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load .env.local (local dev keys — never committed)
_env_local = REPO_ROOT / ".env.local"
if _env_local.exists():
    with open(_env_local, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

ENDPOINTS = {
    "prod":    "https://rts-backend-7ru5.onrender.com/api/agent/v3",
    "prod_v2": "https://rts-backend-7ru5.onrender.com/api/agent/v2",
    "prod_v3": "https://rts-backend-7ru5.onrender.com/api/agent/v3",
    "prod_v4": "https://rts-backend-7ru5.onrender.com/api/agent/v4",
    "local":   _LOCAL_ENDPOINT,
}
_LOCAL_AGENT_VERSION = "v3"

REQUEST_TIMEOUT    = 35    # seconds per agent request
INTER_REQUEST_DELAY = 3.5  # seconds between agent calls
MAX_RETRIES        = 2
RATE_LIMIT_PAUSE   = 70

_LOCAL_CLIENT = None


# ── Agent call ────────────────────────────────────────────────────────────────

def _get_local_client():
    global _LOCAL_CLIENT
    if _LOCAL_CLIENT is None:
        from app import create_app
        _LOCAL_CLIENT = create_app().test_client()
    return _LOCAL_CLIENT


def post_message(endpoint: str, msg: str, session_id: str | None = None) -> tuple:
    """Returns (data_dict | None, elapsed_ms, error_str | None)."""
    payload = {"message": msg}
    if session_id:
        payload["session_id"] = session_id

    if endpoint == _LOCAL_ENDPOINT:
        client = _get_local_client()
        t0 = time.perf_counter()
        try:
            resp = client.post(f"/api/agent/{_LOCAL_AGENT_VERSION}", json=payload)
        except Exception as exc:
            return None, int((time.perf_counter() - t0) * 1000), f"local error: {exc}"
        elapsed = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            return None, elapsed, f"HTTP {resp.status_code}"
        data = resp.get_json(silent=True)
        return (data, elapsed, None) if data else (None, elapsed, "invalid JSON")

    t0, last_err, rate_limited = time.perf_counter(), None, False
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            time.sleep(3)
        try:
            r = requests.post(endpoint, json=payload, timeout=REQUEST_TIMEOUT)
            elapsed = int((time.perf_counter() - t0) * 1000)
            if r.status_code == 429:
                if not rate_limited:
                    wait = int(r.headers.get("Retry-After", RATE_LIMIT_PAUSE))
                    print(f"\n  [429] Rate limit — pausing {wait}s…")
                    time.sleep(wait)
                    rate_limited = True
                    continue
                return None, elapsed, "HTTP 429 rate limit"
            if r.status_code in (500, 502, 503) and attempt < MAX_RETRIES:
                last_err = f"HTTP {r.status_code} (retrying)"
                continue
            r.raise_for_status()
            return r.json(), elapsed, None
        except requests.Timeout:
            last_err = "timeout"
        except Exception as exc:
            last_err = str(exc)
            break

    return None, int((time.perf_counter() - t0) * 1000), last_err or "unknown error"


# ── Heuristic quick-check (still useful as a pre-filter) ─────────────────────

_FAIL_SIGNALS = [
    "i wasn't able", "i'm unable", "i cannot access", "i don't have access",
    "error connecting", "please try again later",
]

def heuristic(text: str) -> str:
    """Returns 'likely_fail' if obvious error signal found, else 'ok'."""
    lower = (text or "").lower()
    for sig in _FAIL_SIGNALS:
        if sig in lower:
            return "likely_fail"
    return "ok"


# ── GPT-4o-mini judge ─────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """\
You are a strict QA evaluator for a Gainesville RTS transit chatbot.
Given an expected behavior description and the agent's actual response, decide:
- PASS: the response fully and correctly satisfies the expected behavior
- FAIL: the response is wrong, incomplete, hallucinates data, or refuses when it shouldn't

Rules:
- Responses may be in English or Spanish — judge the content, not the language
- Slight wording differences are fine; factual errors or missing key info = FAIL
- If the response asks a clarifying question that matches the expected flow = PASS
- If the response makes up times, stops, or routes not in the tool results = FAIL
- Do NOT verify specific calendar dates (e.g. whether "March 20" is really tomorrow).
  The agent runs in real-time and its dates are correct. Only check structure and behavior.
- Do NOT verify or contradict service types (Weekday, Saturday, Sunday, Reduced Service).
  The agent reads live GTFS data; if it says today is Reduced Service, trust that it is correct.
- Do NOT penalize a response for mentioning OR not mentioning reduced service unless
  the expected behavior explicitly requires a specific service type.
- Do NOT compare departure times against any schedule knowledge you have. Only verify
  that the agent used the correct behavior (called the right tool, named the right day type,
  included the required information structure).

Return JSON only, no prose:
{"verdict": "PASS" or "FAIL", "reason": "one sentence max"}
"""

_judge_client: OpenAI | None = None

def _get_judge_client() -> OpenAI:
    global _judge_client
    if _judge_client is None:
        api_key = os.getenv("OPENAI_API_KEY_V4") or os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            print("ERROR: OPENAI_API_KEY not set — cannot run judge.")
            print("  Set it in .env.local or as an env var, then re-run.")
            sys.exit(1)
        # Explicitly set base_url to real OpenAI — overrides OPENAI_BASE_URL env var
        # which may point to a local Ollama instance
        _judge_client = OpenAI(
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            timeout=30,
        )
    return _judge_client


def llm_judge(expected_behavior: str, response: str | None, tool_calls: int | None,
              turns: list | None = None) -> dict:
    """
    Call GPT-4o-mini to judge a single scenario.
    Returns {"verdict": "PASS"|"FAIL", "reason": "..."}
    """
    if not response and not turns:
        return {"verdict": "FAIL", "reason": "no response from agent"}

    if turns:
        # Multi-turn: show full conversation
        convo = "\n".join(
            f"  Turn {t['turn']} Q: {t['query']}\n"
            f"  Turn {t['turn']} A: {(t.get('response') or '[no response]')[:300]}"
            for t in turns
        )
        user_content = (
            f"Expected behavior: {expected_behavior}\n\n"
            f"Conversation:\n{convo}"
        )
    else:
        user_content = (
            f"Expected behavior: {expected_behavior}\n\n"
            f"Agent response: {(response or '')[:600]}\n"
            f"Tool calls made: {tool_calls if tool_calls is not None else 'unknown'}"
        )

    try:
        client = _get_judge_client()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        verdict = data.get("verdict", "FAIL").upper()
        if verdict not in ("PASS", "FAIL"):
            verdict = "FAIL"
        return {"verdict": verdict, "reason": data.get("reason", "")}
    except Exception as exc:
        return {"verdict": "FAIL", "reason": f"judge error: {exc}"}


# ── Scenario runners ──────────────────────────────────────────────────────────

def run_single(endpoint: str, scenario: dict, use_judge: bool) -> dict:
    sid = scenario["id"]
    query = scenario["query"]
    expected = scenario.get("expected_behavior", "")

    print(f"  [{sid}] {query[:68]}", end="", flush=True)

    data, elapsed, err = post_message(endpoint, query)

    if err:
        print(f"  ERROR ({err})")
        result = {
            "id": sid, "type": "single",
            "category": scenario.get("category", ""),
            "description": scenario.get("description", ""),
            "query": query, "expected_behavior": expected,
            "response": None, "tool_calls_made": None,
            "response_time_ms": elapsed, "status": "error", "error": err,
            "heuristic": "likely_fail",
            "verdict": "FAIL", "reason": f"agent error: {err}",
        }
        print(f"  [FAIL]")
        return result

    response_text = (data or {}).get("answer", "")
    meta = (data or {}).get("meta") or {}
    tool_calls = meta.get("tool_calls_made")
    h = heuristic(response_text)

    # Judge
    if use_judge:
        verdict_data = llm_judge(expected, response_text, tool_calls)
    else:
        verdict_data = {"verdict": "PASS" if h == "ok" else "FAIL",
                        "reason": "heuristic only"}

    symbol = "PASS" if verdict_data["verdict"] == "PASS" else "FAIL"
    print(f"  [{symbol}]  ({elapsed}ms, {tool_calls or 0} tools)")
    if verdict_data["verdict"] == "FAIL":
        print(f"         reason: {verdict_data['reason']}")

    return {
        "id": sid, "type": "single",
        "category": scenario.get("category", ""),
        "description": scenario.get("description", ""),
        "query": query, "expected_behavior": expected,
        "response": response_text,
        "tool_calls_made": tool_calls,
        "language_detected": meta.get("language"),
        "response_time_ms": elapsed,
        "status": "completed", "error": None,
        "heuristic": h,
        **verdict_data,
    }


def run_multi(endpoint: str, scenario: dict, use_judge: bool) -> dict:
    sid = scenario["id"]
    turns_queries = scenario.get("turns", [])
    expected = scenario.get("expected_behavior", "")

    print(f"  [{sid}] {scenario.get('description', '')[:68]}")

    session_id = None
    turn_results = []
    had_error = False

    for i, query in enumerate(turns_queries):
        print(f"    Turn {i+1}: {query[:60]}", end="", flush=True)
        data, elapsed, err = post_message(endpoint, query, session_id=session_id)

        if i == 0 and data:
            session_id = data.get("session_id")

        response_text = (data or {}).get("answer", "") if data else ""
        meta = (data or {}).get("meta") or {}

        print(f"  ({elapsed}ms)")

        turn_results.append({
            "turn": i + 1, "query": query,
            "response": response_text if not err else None,
            "tool_calls_made": meta.get("tool_calls_made"),
            "language_detected": meta.get("language"),
            "response_time_ms": elapsed,
            "status": "error" if err else "completed",
            "error": err,
        })

        if err:
            had_error = True
            break

        time.sleep(INTER_REQUEST_DELAY)

    # Judge the full multi-turn
    if use_judge:
        verdict_data = llm_judge(expected, None, None, turns=turn_results)
    else:
        any_empty = any(not (t.get("response") or "") for t in turn_results)
        verdict_data = {"verdict": "FAIL" if had_error or any_empty else "PASS",
                        "reason": "heuristic only"}

    symbol = "PASS" if verdict_data["verdict"] == "PASS" else "FAIL"
    print(f"    => [{symbol}]  {verdict_data['reason']}")

    return {
        "id": sid, "type": "multi",
        "category": scenario.get("category", ""),
        "description": scenario.get("description", ""),
        "expected_behavior": expected,
        "turns": turn_results,
        "status": "error" if had_error else "completed",
        "heuristic": "likely_fail" if had_error else "ok",
        **verdict_data,
    }


# ── Scenario loader ───────────────────────────────────────────────────────────

def load_scenarios(path: Path, ids_filter: str | None) -> list:
    with open(path, encoding="utf-8") as f:
        scenarios = json.load(f)
    if ids_filter:
        ids = {s.strip() for s in ids_filter.split(",")}
        scenarios = [s for s in scenarios if s["id"] in ids]
    return scenarios


def get_failing_ids_from_last_run() -> tuple:
    files = sorted(RESULTS_DIR.glob("judged_*.json")) + sorted(RESULTS_DIR.glob("run_*.json"))
    if not files:
        return None, None
    latest = files[-1]
    with open(latest, encoding="utf-8") as f:
        data = json.load(f)
    failing = [
        r["id"] for r in data.get("results", [])
        if r.get("verdict") == "FAIL" or r.get("status") == "error"
    ]
    return (",".join(failing) if failing else None), latest


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RTS Agent — Run + Judge test scenarios")
    parser.add_argument("--env", choices=list(ENDPOINTS), default="prod",
                        help="Target environment (default: prod = v3)")
    parser.add_argument("--agent", choices=["v2", "v3", "v4"], default="v3",
                        help="Agent version for local env (default: v3)")
    parser.add_argument("--ids",  default=None,
                        help="Comma-separated scenario IDs (e.g. S01,S07,M01)")
    parser.add_argument("--file", default=str(DEFAULT_SCENARIOS),
                        help="Path to scenarios JSON (default: scenarios_v2.json)")
    parser.add_argument("--retry-fails", action="store_true",
                        help="Re-run scenarios that FAIL'd in the most recent run")
    parser.add_argument("--no-judge", action="store_true",
                        help="Skip GPT judge — heuristic only (faster, less accurate)")
    parser.add_argument("--judge-fails-only", action="store_true",
                        help="Only run GPT judge on heuristic-fail scenarios")
    parser.add_argument("--gtfs-verify", action="store_true",
                        help="Add GTFS-grounded factual verification layer (requires local GTFS DB)")
    parser.add_argument("--ollama", action="store_true",
                        help="Force agent to use local Ollama model (sets OPENAI_MODEL_V4=OPENAI_MODEL, implies --env local --agent v4)")
    args = parser.parse_args()

    if args.ollama:
        args.env   = "local"
        args.agent = "v4"
        os.environ["OPENAI_MODEL_V4"] = os.environ.get("OPENAI_MODEL", "qwen3:8b")

    global _LOCAL_AGENT_VERSION
    _LOCAL_AGENT_VERSION = args.agent

    ids_filter = args.ids
    prior_file = None

    if args.retry_fails:
        failing_ids, prior_file = get_failing_ids_from_last_run()
        if prior_file is None:
            print("No previous results file found — running all scenarios.")
        elif failing_ids is None:
            print(f"All scenarios passed in {prior_file.name} — nothing to retry.")
            return
        else:
            ids_filter = failing_ids
            print(f"Retrying failures from: {prior_file.name}")
            print(f"Failing IDs: {failing_ids}")

    endpoint = ENDPOINTS[args.env]
    scenarios = load_scenarios(Path(args.file), ids_filter)
    use_judge   = not args.no_judge
    use_gtfs    = args.gtfs_verify
    gtfs_verify = None
    if use_gtfs:
        try:
            import sys as _sys
            _sys.path.insert(0, str(SCRIPT_DIR))
            from judge_gtfs import gtfs_verify as _gtfs_verify
            gtfs_verify = _gtfs_verify
            print("  GTFS verifier loaded.")
        except Exception as exc:
            print(f"  [!] GTFS verifier unavailable: {exc}  (--gtfs-verify ignored)")
            use_gtfs = False

    print(f"\nRTS Agent — Run + Judge")
    print(f"Endpoint : {endpoint}")
    print(f"Scenarios: {len(scenarios)}"
          + (" (retry-fails)" if args.retry_fails else ""))
    print(f"Judge    : {'GPT-4o-mini inline' if use_judge else 'disabled (heuristic only)'}")
    print(f"GTFS     : {'enabled' if use_gtfs else 'disabled'}")
    print("-" * 72 + "\n")

    results = []
    for s in scenarios:
        if s.get("type") == "multi":
            r = run_multi(endpoint, s, use_judge)
        else:
            if args.judge_fails_only:
                r = run_single(endpoint, s, use_judge=False)
                if r["heuristic"] == "likely_fail" and r["status"] != "error":
                    vd = llm_judge(r["expected_behavior"], r["response"], r["tool_calls_made"])
                    r.update(vd)
                    symbol = "PASS" if vd["verdict"] == "PASS" else "FAIL"
                    print(f"         judge : [{symbol}] — {vd['reason']}")
            else:
                r = run_single(endpoint, s, use_judge)

        # Optional GTFS factual layer — runs on top of LLM verdict
        if use_gtfs and r.get("status") != "error" and r.get("response"):
            query    = s.get("query") or " ".join(s.get("turns", []))
            gv       = gtfs_verify(query, r["response"])
            r["gtfs_verdict"] = gv["verdict"]
            r["gtfs_reason"]  = gv["reason"]
            r["gtfs_checks"]  = gv["checks"]
            # Escalate to FAIL if GTFS contradicts a verified claim
            if gv["verdict"] == "FAIL" and r.get("verdict") == "PASS":
                r["verdict"] = "FAIL"
                r["reason"]  = f"GTFS contradiction: {gv['reason']}"
                print(f"         gtfs  : [FAIL] — {gv['reason']}")
            elif gv["verdict"] == "PASS":
                print(f"         gtfs  : [PASS] {len(gv['checks'])} claim(s) verified")

        results.append(r)
        time.sleep(INTER_REQUEST_DELAY)

    # ── Summary ───────────────────────────────────────────────────────────────
    total  = len(results)
    n_pass = sum(1 for r in results if r.get("verdict") == "PASS")
    n_fail = total - n_pass
    pct    = round(n_pass / total * 100) if total else 0

    print("\n" + "=" * 72)
    print(f"  PASS {n_pass:>3}  FAIL {n_fail:>3}  ({pct}% pass rate)  — {total} scenarios")

    if n_fail:
        print(f"\n  Failures:")
        for r in results:
            if r.get("verdict") == "FAIL":
                sid  = r["id"]
                desc = (r.get("description") or r.get("query", ""))[:60]
                why  = r.get("reason", "")
                desc = desc.encode("ascii", "replace").decode("ascii")
                why  = why.encode("ascii", "replace").decode("ascii")
                print(f"    [{sid}] {desc}")
                print(f"           {why}")

    # ── Save ──────────────────────────────────────────────────────────────────
    RESULTS_DIR.mkdir(exist_ok=True)
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"judged_{ts}.json"
    out_path.write_text(json.dumps({
        "run_id":    f"judged_{ts}",
        "endpoint":  endpoint,
        "env":       args.env,
        "timestamp": ts,
        "judged_by": "gpt-4o-mini" if use_judge else "heuristic",
        "retry_of":  prior_file.name if prior_file else None,
        "total": total, "PASS": n_pass, "FAIL": n_fail,
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  Saved: {out_path.name}")
    print(f"\n  Re-run failures: python tests/run_and_judge.py --retry-fails")
    print()


if __name__ == "__main__":
    main()
