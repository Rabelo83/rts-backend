# Codex: Run v2 Agent Tests and Analyze Results

Do the following steps in order:

## Step 1 — Run the test suite

Execute this command in the terminal:

```
python tests/run_v2_scenarios.py
```

Wait for it to finish. It will print the path of the results file it saved
(e.g. `tests/results/run_20260227_143022.json`).

## Step 2 — Read the results file

Open and read the JSON file that was just saved to `tests/results/`.

## Step 3 — Analyze using the rubric

Read `tests/gpt_analysis_prompt.md` for the full evaluation rubric and output format.

Apply that rubric to every scenario in the results file.

## Step 4 — Return output

Return:
1. A JSON array with one verdict object per scenario (id, verdict, tool_check,
   hallucination, issue, suggested_task).
2. A plain-text summary (total, PASS, FAIL, top issues, suggested new tasks).
3. (Optional) 5–10 new scenario objects ready to append to tests/scenarios_v2.json.
