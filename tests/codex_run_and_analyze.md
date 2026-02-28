# Codex: Run, Analyze, Fix, and Verify RTS Agent v2 Tests

You are an autonomous coding agent. Execute ALL six steps below in order
using your file-reading and file-writing tools. Do NOT stop after analysis.
Do NOT describe what you would do — actually open the files and write the
changes. Only report back after Step 6 is complete.

> **Important:** The scenario runner now talks to the Flask app via its
> built-in test client, so you do **not** need to start a separate server or
> open any network ports.

---

## Step 0 — Set environment variables

Before running the tests:

- `OPENAI_API_KEY` can point to a real key, but a placeholder
  (`export OPENAI_API_KEY=dummy`) is also acceptable. When no valid key is
  available the agent automatically falls back to the legacy rule-based
  orchestration, which is sufficient for regression tests.
- `BUS_API_KEY` is optional. If absent, realtime prediction scenarios will
  return `api_unavailable`, but the suite will still provide useful coverage.
- Leave `RTPIDATAFEED=bustime` unless you intentionally need another feed.

---

## Step 1 — Run the full test suite

```
python tests/run_v2_scenarios.py --env local
```

The script automatically creates a Flask test client and issues HTTP requests
against `/api/agent/v2` internally. Note the path of the saved results file
(e.g. `tests/results/run_YYYYMMDD_HHMMSS.json`).

---

## Step 2 — Load context files

Read all three files before proceeding:
- `tests/results/run_YYYYMMDD_HHMMSS.json`  (the results you just generated)
- `tests/gpt_analysis_prompt.md`             (the evaluation rubric)
- `routes/agent_v2.py`                       (the agent system prompt + loop)

---

## Step 3 — Analyze each scenario

Apply the rubric from `gpt_analysis_prompt.md` to every scenario in the results.

For each scenario produce:
```json
{
  "id": "S11",
  "verdict": "FAIL",
  "tool_check": "OK",
  "hallucination": "NO",
  "issue": "One sentence describing the problem",
  "fix_type": "safe | needs_review",
  "fix_plan": "What you will change and in which file"
}
```

`fix_type` classification:
- **safe** — Codex can implement autonomously:
  - Text changes to `SYSTEM_PROMPT` in `routes/agent_v2.py`
  - Adding entries to `_AREA_ALIASES` in `routes/schedule_service.py`
  - Adding new scenarios to `tests/scenarios_v2.json`
  - Wording/formatting fixes in any prompt or config file
- **needs_review** — Flag for the user to hand to Claude:
  - New tool implementations or changes to tool dispatch logic
  - Changes to the agent loop (the `handle_message` function)
  - New Python files, API endpoints, or database queries
  - Any change you are not confident about

---

## Step 4 — Implement all "safe" fixes NOW (write to disk)

For each FAIL where you assigned `fix_type: "safe"`:

1. **Open the file** using your file-read tool.
2. **Edit the file** using your file-write/patch tool — make only the targeted
   change, nothing else.
3. **Confirm** the change was saved by reading the relevant section back.

Do NOT just describe the fix. Do NOT ask for permission. Write the change.

Common safe fix patterns:
- **Wrong language in response**: Open `routes/schedule_service.py`, find
  `_AREA_ALIASES`, and add the missing Spanish key → area-code entry.
  Example: `"universidad de florida": "UF"` (follow the existing dict format).
- **Follow-up returns wrong time**: Open `routes/agent_v2.py`, find the
  `## FOLLOW-UP TIME ADVANCEMENT` block inside `SYSTEM_PROMPT`, and edit
  the relevant sentence. Do not touch any Python code outside the string.
- **Out-of-scope response references customer service**: Open `routes/agent_v2.py`,
  find `## WHEN THE QUESTION IS BEYOND YOUR TOOLS` inside `SYSTEM_PROMPT`,
  and edit only that paragraph.
- **Hallucination in a specific category**: Open `routes/agent_v2.py`,
  find the relevant `## HARD RULES` bullet, strengthen the wording.
- **New scenario needed**: Open `tests/scenarios_v2.json`, append the new
  scenario object before the closing `]`. Preserve valid JSON.

---

## Step 5 — Re-run only the failing scenarios (do not skip this step)

After writing all safe fixes in Step 4, immediately run:

```
python tests/run_v2_scenarios.py --env local --retry-fails
```

This automatically reads the most recent results file and re-runs only the
scenarios that were not `likely_pass`. Read the new results file and note
which scenarios now pass vs still fail.

---

## Step 6 — Report

```
=== RTS Agent v2 — Fix Report ===

Run 1 results: XX/YY PASS
Run 2 results (post-fix): XX/YY PASS

Fixed autonomously:
- [ID] issue description → fix applied in routes/agent_v2.py (or wherever)
- [ID] ...

Still failing after fix (needs Claude review):
- [ID] issue: ... | suggested fix: ...

Needs Claude review (complex fixes):
- [ID] issue: ... | suggested approach: ...

New scenarios added to scenarios_v2.json:
- [GPTXX] description

Next: paste the "needs Claude review" items into the VS Code Claude chat.
```
