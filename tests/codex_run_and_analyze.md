# Codex: Run, Analyze, Fix, and Verify RTS Agent v2 Tests

Execute the following steps in order. Do NOT stop after analysis — implement
the fixes and verify them before reporting back.

---

## Step 1 — Run the full test suite

```
python tests/run_v2_scenarios.py
```

Note the path of the saved results file (e.g. `tests/results/run_YYYYMMDD_HHMMSS.json`).

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

## Step 4 — Implement all "safe" fixes

For each FAIL with `fix_type: "safe"`:

1. Read the relevant file.
2. Make the targeted fix — change only what is needed to address the issue.
3. Do NOT refactor surrounding code or make unrelated improvements.
4. Save the file.

Common safe fix patterns:
- **Wrong language in response**: Add Spanish alias to `_AREA_ALIASES` in
  `routes/schedule_service.py` (same pattern as existing entries).
- **Follow-up returns wrong time**: Adjust the `## FOLLOW-UP TIME ADVANCEMENT`
  section in the `SYSTEM_PROMPT` string in `routes/agent_v2.py`.
- **Out-of-scope response references customer service**: Update the
  `## WHEN THE QUESTION IS BEYOND YOUR TOOLS` section in `SYSTEM_PROMPT`.
- **Hallucination in a specific category**: Strengthen the relevant grounding
  rule in `SYSTEM_PROMPT`.
- **New scenario needed**: Append a correctly formatted object to
  `tests/scenarios_v2.json`.

---

## Step 5 — Re-run only the failing scenarios

After implementing fixes, re-run ONLY the scenarios that failed in Step 1.
The `--retry-fails` flag automatically reads the most recent results file
and re-runs any scenario with `quick_check != "likely_pass"` or `status == "error"`:

```
python tests/run_v2_scenarios.py --retry-fails
```

Read the new results file and verify each previously-failing scenario now passes.

---

## Step 6 — Report

Print a final report in this format:

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

---

## Important constraints

- Only edit files you have read first.
- Only change what is directly needed to fix the identified issue.
- Do not rename, restructure, or refactor working code.
- Do not add features beyond what is needed to fix the failing scenario.
- If you are unsure whether a fix is safe, put it in "needs Claude review".
