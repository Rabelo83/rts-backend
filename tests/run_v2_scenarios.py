#!/usr/bin/env python3
"""
RTS Agent v2 — Scenario Test Runner
=====================================
Hits the live /api/agent/v2 endpoint with every scenario in scenarios_v2.json,
logs all responses, and saves a JSON results file for GPT analysis.

Usage:
  python tests/run_v2_scenarios.py                      # prod endpoint
  python tests/run_v2_scenarios.py --env local          # localhost:5000
  python tests/run_v2_scenarios.py --ids S01,S07,M01   # specific scenarios only
  python tests/run_v2_scenarios.py --file my_extra.json # use a different scenarios file
  python tests/run_v2_scenarios.py --retry-fails        # re-run only scenarios that failed last time

Output:
  tests/results/run_YYYYMMDD_HHMMSS.json

Next step:
  1. Open tests/gpt_analysis_prompt.md
  2. Copy ALL of it into a fresh ChatGPT conversation
  3. Paste the contents of the results file below it
  4. Ask: "Analyze each scenario and return verdict JSON + summary"
  5. Paste GPT's task suggestions here in VS Code → Claude implements fixes
"""

import argparse
import datetime
import glob
import json
import os
import sys
import time
import uuid

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed.")
    print("Run:  pip install requests")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Load .env.local if present (local dev keys — never committed)
_env_local = os.path.join(REPO_ROOT, ".env.local")
if os.path.exists(_env_local):
    with open(_env_local, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
DEFAULT_SCENARIOS_FILE = os.path.join(SCRIPT_DIR, "scenarios_v2.json")
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")
_LOCAL_ENDPOINT = "local:testclient"

ENDPOINTS = {
    "prod":   "https://rts-backend-7ru5.onrender.com/api/agent/v2",
    "prod_v3":"https://rts-backend-7ru5.onrender.com/api/agent/v3",
    "local":  _LOCAL_ENDPOINT,
}
# Agent version used for local test client (v2 or v3)
_LOCAL_AGENT_VERSION = "v2"

REQUEST_TIMEOUT = 35        # seconds per request
INTER_REQUEST_DELAY = 4.0   # seconds between requests — ~30 req in 2 min, well under 30/hr
MAX_RETRIES = 2             # retry on transient 500/502/503 errors
RATE_LIMIT_PAUSE = 70       # seconds to wait after a 429 before retrying once

_LOCAL_CLIENT = None


def _get_local_client():
    global _LOCAL_CLIENT
    if _LOCAL_CLIENT is None:
        from app import create_app

        _LOCAL_CLIENT = create_app().test_client()
    return _LOCAL_CLIENT

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_scenarios(path, ids_filter=None):
    with open(path, encoding="utf-8") as f:
        scenarios = json.load(f)
    if ids_filter:
        ids = {s.strip() for s in ids_filter.split(",")}
        scenarios = [s for s in scenarios if s["id"] in ids]
    return scenarios


def get_failing_ids_from_last_run():
    """
    Find the most recent results file and return a tuple of:
      (comma-separated failing IDs string, path to the results file used)
    Returns (None, None) if no results files exist or all passed.
    """
    pattern = os.path.join(RESULTS_DIR, "run_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        return None, None
    latest = files[-1]
    with open(latest, encoding="utf-8") as f:
        data = json.load(f)
    failing = [
        r["id"] for r in data.get("results", [])
        if r.get("quick_check") != "likely_pass" or r.get("status") == "error"
    ]
    if not failing:
        return None, latest
    return ",".join(failing), latest


def post_message(endpoint, msg, session_id=None):
    """
    POST one message to /api/agent/v2.
    Retries up to MAX_RETRIES times on transient 502/503 errors.
    Returns (response_dict_or_None, elapsed_ms, error_str_or_None).
    """
    payload = {"message": msg}
    if session_id:
        payload["session_id"] = session_id

    if endpoint == _LOCAL_ENDPOINT:
        client = _get_local_client()
        t0 = time.perf_counter()
        try:
            resp = client.post(f"/api/agent/{_LOCAL_AGENT_VERSION}", json=payload)
        except Exception as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return None, elapsed, f"local client error: {exc}"
        elapsed = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            return None, elapsed, f"HTTP {resp.status_code}: {resp.data[:200]!r}"
        data = resp.get_json(silent=True)
        if data is None:
            return None, elapsed, "local client returned invalid JSON"
        return data, elapsed, None

    last_err = None
    t0 = time.perf_counter()
    rate_limited = False
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            time.sleep(3)  # brief pause before retry
        try:
            r = requests.post(endpoint, json=payload, timeout=REQUEST_TIMEOUT)
            elapsed = int((time.perf_counter() - t0) * 1000)
            if r.status_code == 429:
                if not rate_limited:
                    # First 429: pause and retry once
                    wait = int(r.headers.get("Retry-After", RATE_LIMIT_PAUSE))
                    print(f"\n  [429] Rate limit hit — pausing {wait}s then retrying...")
                    time.sleep(wait)
                    rate_limited = True
                    continue
                last_err = "HTTP 429: rate limit exceeded"
                break
            if r.status_code in (500, 502, 503) and attempt < MAX_RETRIES:
                last_err = f"HTTP {r.status_code} (retrying)"
                continue
            r.raise_for_status()
            return r.json(), elapsed, None
        except requests.Timeout:
            last_err = "timeout"
            if attempt < MAX_RETRIES:
                continue
            return None, REQUEST_TIMEOUT * 1000, last_err
        except requests.HTTPError:
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            break
        except Exception as e:
            last_err = str(e)
            break

    elapsed = int((time.perf_counter() - t0) * 1000)
    return None, elapsed, last_err


def quick_check(text, pass_signals, fail_signals):
    """
    Heuristic signal check for a sanity flag.
    Returns: 'likely_pass' | 'likely_fail' | 'unknown'
    This is NOT a definitive judgment — GPT does the real analysis.
    """
    if not text:
        return "likely_fail"
    lower = text.lower()
    for sig in (fail_signals or []):
        if sig.lower() in lower:
            return "likely_fail"
    hits = sum(1 for sig in (pass_signals or []) if sig.lower() in lower)
    if hits >= 1:
        return "likely_pass"
    return "unknown"


# ── Single-turn scenario ──────────────────────────────────────────────────────

def run_single(endpoint, scenario):
    query = scenario["query"]
    print(f"  [{scenario['id']}] {query[:68]}", end="", flush=True)

    data, elapsed_ms, err = post_message(endpoint, query)

    if err:
        print(f" ERROR ({err})")
        return {
            "id": scenario["id"],
            "type": "single",
            "category": scenario.get("category", ""),
            "description": scenario.get("description", ""),
            "query": query,
            "expected_behavior": scenario.get("expected_behavior", ""),
            "pass_signals": scenario.get("pass_signals", []),
            "fail_signals": scenario.get("fail_signals", []),
            "response": None,
            "tool_calls_made": None,
            "language_detected": None,
            "response_time_ms": elapsed_ms,
            "status": "error",
            "error": err,
            "quick_check": "likely_fail",
        }

    response_text = (data or {}).get("answer", "")
    meta = (data or {}).get("meta") or {}
    qc = quick_check(response_text, scenario.get("pass_signals"), scenario.get("fail_signals"))
    icon = {"likely_pass": "OK", "likely_fail": "FAIL"}.get(qc, "?")
    print(f" {icon} {qc} ({elapsed_ms}ms, {meta.get('tool_calls_made', 0)} tool calls)")

    return {
        "id": scenario["id"],
        "type": "single",
        "category": scenario.get("category", ""),
        "description": scenario.get("description", ""),
        "query": query,
        "expected_behavior": scenario.get("expected_behavior", ""),
        "pass_signals": scenario.get("pass_signals", []),
        "fail_signals": scenario.get("fail_signals", []),
        "response": response_text,
        "tool_calls_made": meta.get("tool_calls_made"),
        "language_detected": meta.get("language"),
        "response_time_ms": elapsed_ms,
        "status": "completed",
        "error": None,
        "quick_check": qc,
    }


# ── Multi-turn scenario ───────────────────────────────────────────────────────

def run_multi(endpoint, scenario):
    turns = scenario.get("turns", [])
    ps_per_turn = scenario.get("pass_signals_per_turn", [[] for _ in turns])
    fs_per_turn = scenario.get("fail_signals_per_turn", [[] for _ in turns])

    print(f"  [{scenario['id']}] {scenario.get('description', '')[:68]}")

    session_id = None
    turn_results = []
    had_error = False

    for i, query in enumerate(turns):
        print(f"    Turn {i+1}: {query[:58]}", end="", flush=True)

        data, elapsed_ms, err = post_message(endpoint, query, session_id=session_id)

        # Capture session_id from first response to chain subsequent turns
        if i == 0 and data:
            session_id = data.get("session_id")

        response_text = (data or {}).get("answer", "") if data else ""
        meta = (data or {}).get("meta") or {}
        ps = ps_per_turn[i] if i < len(ps_per_turn) else []
        fs = fs_per_turn[i] if i < len(fs_per_turn) else []
        qc = quick_check(response_text, ps, fs) if not err else "likely_fail"
        icon = {"likely_pass": "OK", "likely_fail": "FAIL"}.get(qc, "?")
        print(f" {icon} ({elapsed_ms}ms)")

        turn_results.append({
            "turn": i + 1,
            "query": query,
            "response": response_text if not err else None,
            "tool_calls_made": meta.get("tool_calls_made"),
            "language_detected": meta.get("language"),
            "response_time_ms": elapsed_ms,
            "status": "error" if err else "completed",
            "error": err,
            "quick_check": qc,
        })

        if err:
            had_error = True
            break

        time.sleep(INTER_REQUEST_DELAY)

    all_pass = all(t["quick_check"] == "likely_pass" for t in turn_results)
    any_fail = any(t["quick_check"] == "likely_fail" for t in turn_results)
    overall_qc = "likely_pass" if all_pass else ("likely_fail" if any_fail else "unknown")

    return {
        "id": scenario["id"],
        "type": "multi",
        "category": scenario.get("category", ""),
        "description": scenario.get("description", ""),
        "expected_behavior": scenario.get("expected_behavior", ""),
        "turns": turn_results,
        "status": "error" if had_error else "completed",
        "quick_check": overall_qc,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RTS Agent v2 Scenario Test Runner")
    parser.add_argument("--env", choices=["prod", "prod_v3", "local"], default="prod",
                        help="Target environment (default: prod)")
    parser.add_argument("--agent", choices=["v2", "v3"], default="v2",
                        help="Agent version for local env (default: v2)")
    parser.add_argument("--ids", default=None,
                        help="Comma-separated scenario IDs to run (e.g. S01,S07,M01)")
    parser.add_argument("--file", default=DEFAULT_SCENARIOS_FILE,
                        help=f"Path to scenarios JSON file (default: {DEFAULT_SCENARIOS_FILE})")
    parser.add_argument("--retry-fails", action="store_true",
                        help="Re-run only scenarios that failed or errored in the most recent results file")
    args = parser.parse_args()

    global _LOCAL_AGENT_VERSION
    _LOCAL_AGENT_VERSION = args.agent
    ids_filter = args.ids
    prior_run_file = None

    if args.retry_fails:
        failing_ids, prior_run_file = get_failing_ids_from_last_run()
        if prior_run_file is None:
            print("No previous results file found — running all scenarios.")
        elif failing_ids is None:
            print(f"All scenarios passed in {os.path.basename(prior_run_file)} — nothing to retry.")
            return
        else:
            ids_filter = failing_ids
            print(f"Retrying failures from: {os.path.basename(prior_run_file)}")
            print(f"Failing IDs: {failing_ids}")

    endpoint = ENDPOINTS[args.env]
    scenarios = load_scenarios(args.file, ids_filter)

    print(f"\nRTS Agent v2 -- Scenario Test Runner")
    print(f"Endpoint  : {endpoint}")
    print(f"Scenarios : {len(scenarios)}" + (" (retry-fails mode)" if args.retry_fails else ""))
    print(f"{'-' * 72}\n")

    results = []
    for s in scenarios:
        if s.get("type") == "multi":
            result = run_multi(endpoint, s)
        else:
            result = run_single(endpoint, s)
        results.append(result)
        time.sleep(INTER_REQUEST_DELAY)

    # Summary
    total = len(results)
    n_pass = sum(1 for r in results if r.get("quick_check") == "likely_pass")
    n_fail = sum(1 for r in results if r.get("quick_check") == "likely_fail")
    n_unk  = total - n_pass - n_fail

    print(f"\n{'-' * 72}")
    print(f"Quick-check summary  (heuristic only - GPT does the real analysis)")
    print(f"  likely_pass : {n_pass}")
    print(f"  likely_fail : {n_fail}")
    print(f"  unknown     : {n_unk}")
    print(f"  total       : {total}")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = os.path.join(RESULTS_DIR, f"run_{ts}.json")
    run_data = {
        "run_id": f"run_{ts}",
        "endpoint": endpoint,
        "env": args.env,
        "timestamp": ts,
        "scenario_count": total,
        "retry_of": os.path.basename(prior_run_file) if prior_run_file else None,
        "quick_summary": {"likely_pass": n_pass, "likely_fail": n_fail, "unknown": n_unk},
        "results": results,
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(run_data, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved: {out_file}")
    print(f"\n{'-' * 72}")
    print(f"NEXT STEPS -- GPT Analysis:")
    print(f"  1. Open  tests/gpt_analysis_prompt.md  (copy entire content)")
    print(f"  2. Paste it into a fresh ChatGPT conversation")
    print(f"  3. Then paste the contents of:\n       {out_file}")
    print(f"  4. Ask: 'Analyze each scenario and return verdict JSON + summary'")
    print(f"  5. Paste GPT's suggested tasks back here in VS Code → Claude fixes them")
    print(f"\nNEXT STEPS — Add more scenarios:")
    print(f"  1. Open  tests/gpt_user_simulator_prompt.md  (copy entire content)")
    print(f"  2. Paste it into ChatGPT")
    print(f"  3. Copy the returned JSON array and APPEND it to tests/scenarios_v2.json")
    print(f"  4. Re-run this script to test the new scenarios\n")


if __name__ == "__main__":
    main()
