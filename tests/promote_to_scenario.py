#!/usr/bin/env python3
"""
promote_to_scenario.py — Close the feedback loop
==================================================
Takes FAIL entries from any judged_*.json or replay_*.json result file
and scaffolds them into new entries in scenarios_v2.json.

Usage:
  python tests/promote_to_scenario.py                  # latest judged/replay file
  python tests/promote_to_scenario.py --file <path>    # specific file
  python tests/promote_to_scenario.py --all            # all result files (dedupe by query)
  python tests/promote_to_scenario.py --dry-run        # print what would be added, no write
  python tests/promote_to_scenario.py --non-interactive # auto-promote all FAILs (CI use)
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

SCRIPT_DIR    = Path(__file__).parent
RESULTS_DIR   = SCRIPT_DIR / "results"
SCENARIOS_FILE = SCRIPT_DIR / "scenarios_v2.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_scenarios() -> list:
    return json.loads(SCENARIOS_FILE.read_text(encoding="utf-8"))


def _save_scenarios(scenarios: list) -> None:
    SCENARIOS_FILE.write_text(
        json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _next_id(scenarios: list, kind: str) -> str:
    """Return next available S## or M## id."""
    prefix = "S" if kind == "single" else "M"
    existing = {
        int(s["id"][1:])
        for s in scenarios
        if s.get("id", "").startswith(prefix) and s["id"][1:].isdigit()
    }
    n = max(existing, default=0) + 1
    return f"{prefix}{n:02d}"


def _collect_fails(path: Path) -> list[dict]:
    """
    Load a result file and return a list of normalised fail dicts:
      {source_file, source_id, type, category, description, query,
       expected_behavior, response, reason}
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", [])
    fails = []

    for r in results:
        if r.get("verdict") != "FAIL":
            continue

        # Replay files use 'message' / 'replay_answer'
        is_replay = "message" in r and "replay_answer" in r
        if is_replay:
            query    = (r.get("message") or "").strip()
            response = (r.get("replay_answer") or "").strip()
            expected = ""   # user must write this
            cat      = "replay_fail"
            desc     = query[:80]
            src_id   = r.get("original_session_id", "")[:16]
            typ      = "single"
        else:
            query    = (r.get("query") or "").strip()
            response = (r.get("response") or "").strip()
            expected = (r.get("expected_behavior") or "").strip()
            cat      = r.get("category", "")
            desc     = r.get("description", query[:80])
            src_id   = r.get("id", "")
            typ      = r.get("type", "single")

        if not query:
            continue

        fails.append({
            "source_file": path.name,
            "source_id":   src_id,
            "type":        typ,
            "category":    cat,
            "description": desc,
            "query":       query,
            "expected_behavior": expected,
            "response":    response,
            "reason":      r.get("reason", ""),
        })

    return fails


def _dedupe(fails: list[dict]) -> list[dict]:
    """Remove duplicate queries (keep first occurrence)."""
    seen = set()
    out = []
    for f in fails:
        key = f["query"].lower().strip()
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _already_in_suite(query: str, scenarios: list) -> bool:
    """Return True if this exact query already exists in scenarios_v2.json."""
    q = query.lower().strip()
    for s in scenarios:
        if s.get("query", "").lower().strip() == q:
            return True
        for turn in s.get("turns", []):
            if turn.lower().strip() == q:
                return True
    return False


# ── Interactive promotion ─────────────────────────────────────────────────────

def _wrap(text: str, width: int = 90, indent: str = "  ") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent,
                         subsequent_indent=indent)


def _prompt_field(label: str, prefill: str = "") -> str:
    """Prompt user for a field, showing prefill as default."""
    if prefill:
        print(f"\n  {label}")
        print(_wrap(prefill, indent="    "))
        raw = input(f"  Keep as-is? [Enter] or type replacement: ").strip()
        return raw if raw else prefill
    else:
        print(f"\n  {label}")
        return input("  > ").strip()


def promote_interactive(fail: dict, scenarios: list, dry_run: bool) -> bool:
    """Walk user through promoting one fail. Returns True if promoted."""
    print("\n" + "─" * 72)
    print(f"  SOURCE : {fail['source_file']}  [{fail['source_id']}]")
    print(f"  REASON : {fail['reason']}")
    print(f"\n  QUERY  : {fail['query']}")
    print("\n  AGENT RESPONSE:")
    print(_wrap(fail["response"][:600] or "(empty)", indent="    "))

    ans = input("\n  Promote to scenario? [y/n/q] ").strip().lower()
    if ans == "q":
        return None   # signal to stop
    if ans != "y":
        return False

    expected = _prompt_field("expected_behavior:", fail["expected_behavior"])
    if not expected:
        print("  Skipped — expected_behavior is required.")
        return False

    category = _prompt_field("category:", fail["category"])
    description = _prompt_field("description:", fail["description"])

    typ = fail["type"]
    new_id = _next_id(scenarios, typ)

    entry: dict = {
        "id":               new_id,
        "type":             typ,
        "category":         category or "promoted_fail",
        "description":      description or fail["query"][:80],
        "query":            fail["query"],
        "expected_behavior": expected,
    }

    print(f"\n  Will add: [{new_id}] {entry['description'][:60]}")
    if dry_run:
        print("  (dry-run — not written)")
        return False

    scenarios.append(entry)
    _save_scenarios(scenarios)
    print(f"  Saved to scenarios_v2.json  ({len(scenarios)} total)")
    return True


def promote_auto(fail: dict, scenarios: list, dry_run: bool) -> bool:
    """Non-interactive: promote only if expected_behavior is already present."""
    if not fail.get("expected_behavior"):
        return False
    typ = fail["type"]
    new_id = _next_id(scenarios, typ)
    entry = {
        "id":               new_id,
        "type":             typ,
        "category":         fail["category"] or "promoted_fail",
        "description":      fail["description"] or fail["query"][:80],
        "query":            fail["query"],
        "expected_behavior": fail["expected_behavior"],
    }
    print(f"  AUTO [{new_id}] {entry['description'][:60]}")
    if dry_run:
        print("  (dry-run)")
        return False
    scenarios.append(entry)
    _save_scenarios(scenarios)
    return True


# ── File selection ────────────────────────────────────────────────────────────

def _latest_result_file() -> Path | None:
    files = (
        sorted(RESULTS_DIR.glob("judged_*.json"))
        + sorted(RESULTS_DIR.glob("replay_*.json"))
    )
    return files[-1] if files else None


def _all_result_files() -> list[Path]:
    return sorted(
        list(RESULTS_DIR.glob("judged_*.json"))
        + list(RESULTS_DIR.glob("replay_*.json"))
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Promote FAIL results to scenarios")
    parser.add_argument("--file",            help="Specific result file to read")
    parser.add_argument("--all",             action="store_true",
                        help="Read all judged_/replay_ files (deduped)")
    parser.add_argument("--dry-run",         action="store_true",
                        help="Show what would be added without writing")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Auto-promote any FAIL that has expected_behavior (no prompts)")
    args = parser.parse_args()

    # Collect result files
    if args.file:
        paths = [Path(args.file)]
    elif args.all:
        paths = _all_result_files()
    else:
        p = _latest_result_file()
        if not p:
            print("No judged_*.json or replay_*.json files found in tests/results/")
            sys.exit(1)
        paths = [p]

    # Load fails
    all_fails: list[dict] = []
    for p in paths:
        all_fails.extend(_collect_fails(p))

    all_fails = _dedupe(all_fails)

    if not all_fails:
        print("No FAIL results found in the selected file(s).")
        sys.exit(0)

    # Filter out queries already in the suite
    scenarios = _load_scenarios()
    new_fails = [f for f in all_fails if not _already_in_suite(f["query"], scenarios)]
    skipped = len(all_fails) - len(new_fails)

    print(f"\n  Found {len(all_fails)} FAIL(s) — {skipped} already in suite — {len(new_fails)} new\n")

    if not new_fails:
        print("  Nothing to promote.")
        sys.exit(0)

    promoted = 0
    for fail in new_fails:
        # Reload in case previous iteration appended
        scenarios = _load_scenarios()

        if args.non_interactive:
            if promote_auto(fail, scenarios, args.dry_run):
                promoted += 1
        else:
            result = promote_interactive(fail, scenarios, args.dry_run)
            if result is None:
                print("\n  Stopped early.")
                break
            if result:
                promoted += 1

    print(f"\n  Done — {promoted} scenario(s) promoted.")
    if args.dry_run:
        print("  (dry-run: no changes written)")


if __name__ == "__main__":
    main()
