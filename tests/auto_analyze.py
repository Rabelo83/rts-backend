#!/usr/bin/env python3
"""
RTS Agent — Automated GPT Analysis of Test Results
====================================================
Reads the most recent (or specified) results JSON, calls GPT-4o-mini
with the analysis rubric, and prints + saves the verdict JSON.

Usage:
  python tests/auto_analyze.py                        # latest results file
  python tests/auto_analyze.py --file run_XYZ.json   # specific file
  python tests/auto_analyze.py --fails-only           # only analyze likely_fail rows

Requires:
  OPENAI_API_KEY in environment (or .env.local)
  pip install openai
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
RESULTS_DIR = SCRIPT_DIR / "results"
ANALYSIS_DIR = SCRIPT_DIR / "analysis"
RUBRIC_FILE = SCRIPT_DIR / "gpt_analysis_prompt.md"

# Load .env.local if present
_env_local = REPO_ROOT / ".env.local"
if _env_local.exists():
    with open(_env_local, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai not installed.  Run: pip install openai")
    sys.exit(1)


def find_latest_results() -> Path:
    files = sorted(RESULTS_DIR.glob("run_*.json"))
    if not files:
        print("ERROR: No results files found in tests/results/")
        sys.exit(1)
    return files[-1]


def load_results(path: Path, fails_only: bool) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if fails_only:
        data["results"] = [
            r for r in data.get("results", [])
            if r.get("quick_check") != "likely_pass" or r.get("status") == "error"
        ]
    return data


def build_messages(rubric: str, results_json: str) -> list:
    return [
        {
            "role": "system",
            "content": rubric,
        },
        {
            "role": "user",
            "content": (
                "Here are the test results. Analyze each scenario per the rubric "
                "and return Section 1 (verdict JSON array) and Section 2 (plain-text summary).\n\n"
                "```json\n" + results_json + "\n```"
            ),
        },
    ]


def run_analysis(results_path: Path, fails_only: bool = False) -> None:
    api_key = os.getenv("OPENAI_API_KEY_V4") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set.")
        sys.exit(1)

    rubric = RUBRIC_FILE.read_text(encoding="utf-8")
    data = load_results(results_path, fails_only)
    n_scenarios = len(data.get("results", []))

    if n_scenarios == 0:
        print("No scenarios to analyze (all likely_pass and --fails-only is set).")
        return

    results_json = json.dumps(data, indent=2, ensure_ascii=False)

    print(f"Analyzing {n_scenarios} scenarios from {results_path.name} ...")
    print(f"Using model: gpt-4o-mini")

    client = OpenAI(api_key=api_key, timeout=120)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=build_messages(rubric, results_json),
        temperature=0,
        max_tokens=4096,
    )

    answer = response.choices[0].message.content or ""

    # Print to console
    print("\n" + "=" * 72)
    print("GPT ANALYSIS RESULTS")
    print("=" * 72)
    print(answer)
    print("=" * 72)

    # Save to analysis/ directory alongside results
    ANALYSIS_DIR.mkdir(exist_ok=True)
    stem = results_path.stem  # e.g. "run_20260319_104154"
    out_file = ANALYSIS_DIR / f"analysis_{stem}.txt"
    out_file.write_text(answer, encoding="utf-8")
    print(f"\nAnalysis saved: {out_file}")

    # Try to extract and save the JSON verdict block for easy parsing
    try:
        import re
        match = re.search(r"\[[\s\S]+?\]", answer)
        if match:
            verdicts = json.loads(match.group())
            verdict_file = ANALYSIS_DIR / f"verdicts_{stem}.json"
            with open(verdict_file, "w", encoding="utf-8") as vf:
                json.dump(verdicts, vf, indent=2, ensure_ascii=False)
            n_fail = sum(1 for v in verdicts if v.get("verdict") == "FAIL")
            n_pass = sum(1 for v in verdicts if v.get("verdict") == "PASS")
            print(f"\nVerdict summary: {n_pass} PASS / {n_fail} FAIL")
            print(f"Verdicts saved:  {verdict_file}")
            if n_fail:
                print("\nFailing scenarios:")
                for v in verdicts:
                    if v.get("verdict") == "FAIL":
                        print(f"  [{v['id']}] {v.get('issue', '(no issue)')}")
                        if v.get("suggested_task"):
                            print(f"         -> {v['suggested_task']}")
    except Exception:
        pass  # JSON extraction is best-effort


def main():
    parser = argparse.ArgumentParser(description="Auto-analyze RTS test results with GPT")
    parser.add_argument("--file", help="Results file name (e.g. run_20260319_104154.json). Defaults to latest.")
    parser.add_argument("--fails-only", action="store_true", help="Only send likely_fail scenarios to GPT")
    args = parser.parse_args()

    if args.file:
        path = RESULTS_DIR / args.file if not os.path.isabs(args.file) else Path(args.file)
    else:
        path = find_latest_results()

    print(f"Results file: {path}")
    run_analysis(path, fails_only=args.fails_only)


if __name__ == "__main__":
    main()
