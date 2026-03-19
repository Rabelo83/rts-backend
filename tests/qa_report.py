#!/usr/bin/env python3
"""
QA Progress Report
==================
Reads all judged_*.json and replay_*.json result files, appends summaries
to qa_history.sqlite, and prints a quantitative trend report.

Usage:
  python tests/qa_report.py               # full report
  python tests/qa_report.py --last 10     # last 10 runs only
  python tests/qa_report.py --scenarios   # per-scenario reliability table
  python tests/qa_report.py --diff        # regression diff vs previous run
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent
RESULTS_DIR = SCRIPT_DIR / "results"
QA_HISTORY  = SCRIPT_DIR / "qa_history.sqlite"


# ── DB ────────────────────────────────────────────────────────────────────────

def _init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT UNIQUE,
            run_type    TEXT,      -- 'scenario' | 'replay'
            run_at      TEXT,
            env         TEXT,
            judged_by   TEXT,
            total       INTEGER,
            passed      INTEGER,
            failed      INTEGER,
            pass_pct    REAL
        );
        CREATE TABLE IF NOT EXISTS scenario_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT,
            scenario_id TEXT,
            verdict     TEXT,
            reason      TEXT,
            gtfs_verdict TEXT
        );
    """)
    conn.commit()


def _ingest_file(conn: sqlite3.Connection, path: Path) -> bool:
    """Parse one result file and insert into DB. Returns True if newly inserted."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    run_id = data.get("run_id") or path.stem
    # Skip if already recorded
    if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
        return False

    results  = data.get("results", [])
    total    = len(results)
    passed   = sum(1 for r in results if r.get("verdict") == "PASS")
    failed   = total - passed
    pass_pct = round(passed / total * 100, 1) if total else 0.0

    run_type = "replay" if "replay" in path.stem else "scenario"

    conn.execute("""
        INSERT OR IGNORE INTO runs
            (run_id, run_type, run_at, env, judged_by, total, passed, failed, pass_pct)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        run_id,
        run_type,
        data.get("run_at") or data.get("timestamp", ""),
        data.get("env", ""),
        data.get("judged_by", "keyword"),
        total, passed, failed, pass_pct,
    ))

    for r in results:
        sid = r.get("id") or r.get("session_id") or r.get("message", "")[:40]
        conn.execute("""
            INSERT INTO scenario_results (run_id, scenario_id, verdict, reason, gtfs_verdict)
            VALUES (?,?,?,?,?)
        """, (
            run_id,
            sid,
            r.get("verdict", ""),
            r.get("reason", ""),
            r.get("gtfs_verdict", ""),
        ))

    conn.commit()
    return True


def ingest_all(conn: sqlite3.Connection) -> int:
    """Scan results dir, ingest any new files. Returns count of new files."""
    count = 0
    for path in sorted(RESULTS_DIR.glob("*.json")):
        if _ingest_file(conn, path):
            count += 1
    return count


# ── Report sections ───────────────────────────────────────────────────────────

def _bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "#" * filled + "." * (width - filled)


def print_trend(conn: sqlite3.Connection, last_n: int, run_type: str | None = None):
    q = "SELECT run_id, run_type, run_at, env, judged_by, total, passed, failed, pass_pct FROM runs"
    params = []
    if run_type:
        q += " WHERE run_type=?"
        params.append(run_type)
    q += " ORDER BY run_at DESC"
    if last_n:
        q += f" LIMIT {last_n}"
    rows = conn.execute(q, params).fetchall()
    if not rows:
        print("  No runs recorded yet.")
        return
    rows = list(reversed(rows))  # oldest first for trend display

    print(f"\n  {'Date/Run':<26} {'Type':<9} {'Env':<9} {'Judge':<12}  {'Pass':>5}  {'Total':>5}  {'Rate':>6}  Trend")
    print(f"  {'-'*26} {'-'*9} {'-'*9} {'-'*12}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*20}")
    for r in rows:
        run_id, rtype, run_at, env, judged_by, total, passed, failed, pass_pct = r
        # Shorten run_id for display
        label = run_at[:16] if run_at else run_id[:26]
        bar   = _bar(pass_pct or 0)
        print(f"  {label:<26} {rtype:<9} {(env or ''):<9} {(judged_by or ''):<12}  {passed:>5}  {total:>5}  {pass_pct:>5.1f}%  {bar}")


def print_scenario_reliability(conn: sqlite3.Connection, min_runs: int = 2):
    """Show per-scenario pass rate across all runs."""
    rows = conn.execute("""
        SELECT scenario_id,
               COUNT(*) as runs,
               SUM(CASE WHEN verdict='PASS' THEN 1 ELSE 0 END) as passes,
               ROUND(100.0 * SUM(CASE WHEN verdict='PASS' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM scenario_results
        WHERE scenario_id NOT LIKE '%-%-%-%-%'   -- skip UUIDs (replay rows)
        GROUP BY scenario_id
        HAVING runs >= ?
        ORDER BY pct ASC, runs DESC
    """, (min_runs,)).fetchall()

    if not rows:
        print(f"  No scenarios with {min_runs}+ runs yet.")
        return

    print(f"\n  {'Scenario':<12} {'Runs':>5}  {'Pass':>5}  {'Rate':>6}  Reliability")
    print(f"  {'-'*12} {'-'*5}  {'-'*5}  {'-'*6}  {'-'*20}")
    for sid, runs, passes, pct in rows:
        bar = _bar(pct or 0)
        flag = "  [FLAKY]" if (pct or 0) < 80 else ""
        print(f"  {sid:<12} {runs:>5}  {passes:>5}  {pct:>5.1f}%  {bar}{flag}")


def print_regression_diff(conn: sqlite3.Connection, run_type: str = "scenario"):
    """Compare last two runs of the same type — show new failures and recoveries."""
    runs = conn.execute(
        "SELECT run_id FROM runs WHERE run_type=? ORDER BY run_at DESC LIMIT 2",
        (run_type,)
    ).fetchall()

    if len(runs) < 2:
        print(f"  Need at least 2 {run_type} runs to diff.")
        return

    curr_id, prev_id = runs[0][0], runs[1][0]

    def get_verdicts(run_id):
        rows = conn.execute(
            "SELECT scenario_id, verdict FROM scenario_results WHERE run_id=?",
            (run_id,)
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    curr = get_verdicts(curr_id)
    prev = get_verdicts(prev_id)

    new_fails     = [sid for sid, v in curr.items() if v == "FAIL" and prev.get(sid) == "PASS"]
    recovered     = [sid for sid, v in curr.items() if v == "PASS" and prev.get(sid) == "FAIL"]
    still_failing = [sid for sid, v in curr.items() if v == "FAIL" and prev.get(sid) == "FAIL"]

    print(f"\n  Diff: {prev_id[:30]}  =>  {curr_id[:30]}")
    if new_fails:
        print(f"\n  [REGRESSION] New failures ({len(new_fails)}):")
        for sid in new_fails:
            reason = conn.execute(
                "SELECT reason FROM scenario_results WHERE run_id=? AND scenario_id=?",
                (curr_id, sid)
            ).fetchone()
            print(f"    [{sid}] {(reason[0] if reason else '')[:80]}")
    else:
        print(f"\n  [OK] No new failures vs previous run")

    if recovered:
        print(f"\n  [RECOVERED] ({len(recovered)}):")
        for sid in recovered:
            print(f"    [{sid}]")

    if still_failing:
        print(f"\n  [STILL FAILING] ({len(still_failing)}):")
        for sid in still_failing:
            print(f"    [{sid}]")


def latest_summary(conn: sqlite3.Connection) -> dict:
    """Return latest run stats for each type — used by dashboard API."""
    result = {}
    for rtype in ("scenario", "replay"):
        row = conn.execute(
            "SELECT run_id, run_at, total, passed, failed, pass_pct, judged_by "
            "FROM runs WHERE run_type=? ORDER BY run_at DESC LIMIT 1",
            (rtype,)
        ).fetchone()
        if row:
            result[rtype] = {
                "run_id":   row[0],
                "run_at":   row[1],
                "total":    row[2],
                "passed":   row[3],
                "failed":   row[4],
                "pass_pct": row[5],
                "judged_by": row[6],
            }
    # trend: last 5 scenario runs pass_pct
    trend = conn.execute(
        "SELECT pass_pct FROM runs WHERE run_type='scenario' ORDER BY run_at DESC LIMIT 5"
    ).fetchall()
    result["trend"] = [r[0] for r in reversed(trend)]
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="QA Progress Report")
    parser.add_argument("--last",       type=int, default=20,
                        help="Show last N runs in trend (default 20)")
    parser.add_argument("--scenarios",  action="store_true",
                        help="Show per-scenario reliability table")
    parser.add_argument("--diff",       action="store_true",
                        help="Show regression diff between last two scenario runs")
    parser.add_argument("--type",       choices=["scenario", "replay"], default=None,
                        help="Filter trend to one run type")
    args = parser.parse_args()

    conn = sqlite3.connect(QA_HISTORY)
    _init_db(conn)

    new = ingest_all(conn)
    if new:
        print(f"  Ingested {new} new result file(s) into qa_history.sqlite\n")

    total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"\n{'='*72}")
    print(f"  RTS QA Progress Report  --  {total_runs} total runs on record")
    print(f"{'='*72}")

    print("\n-- Run History" + (f" (last {args.last})" if args.last else "") + " --")
    print_trend(conn, args.last, args.type)

    if args.scenarios:
        print("\n-- Per-Scenario Reliability (>=2 runs) --")
        print_scenario_reliability(conn)

    if args.diff:
        print("\n-- Regression Diff (scenario runs) --")
        print_regression_diff(conn, "scenario")

    # Always show latest summary
    summary = latest_summary(conn)
    print(f"\n-- Latest Results --")
    for rtype in ("scenario", "replay"):
        if rtype in summary:
            s = summary[rtype]
            print(f"  {rtype:<9}: {s['passed']}/{s['total']} PASS  ({s['pass_pct']}%)  "
                  f"judged by {s['judged_by']}  [{s['run_at'][:16]}]")
    if summary.get("trend"):
        trend_str = "  ->  ".join(f"{p}%" for p in summary["trend"])
        print(f"  trend    : {trend_str}")
    print()

    conn.close()


if __name__ == "__main__":
    main()
