# RTS Project Log

Purpose: Keep a short history of meaningful project changes (features, fixes, deployments, blockers, decisions).

How to use:
- Update `TASKS.md` and `data/project_tasks.json` for current status changes.
- Add a log entry here for major changes only.
- Keep entries short and dated.

## Entry Template

### YYYY-MM-DD
- Type: `feature` | `fix` | `deploy` | `docs` | `decision` | `blocker`
- Summary:
- Files/Areas:
- Notes / Follow-up:

---

### 2026-02-24
- Type: `feature`, `docs`
- Summary: Added project task tracking system and dashboard page for status visibility.
- Files/Areas: `TASKS.md`, `data/project_tasks.json`, `routes/project_status.py`, `public_html/dashboard.html`, `app.py`
- Notes / Follow-up: Keep `TASKS.md` and `data/project_tasks.json` updated after each completed/pending/blocked status change. Dashboard is currently public at `/dashboard` once deployed.

### 2026-02-24
- Type: `feature`
- Summary: Added manual task creation from dashboard via `POST /api/project/tasks`.
- Files/Areas: `routes/project_status.py`, `public_html/dashboard.html`
- Notes / Follow-up: Followed by SQLite-backed storage for manual dashboard tasks. Set `PROJECT_TASKS_DB_PATH` to a persistent disk path on Render for redeploy-safe storage.

### 2026-02-24
- Type: `feature`
- Summary: Switched manual dashboard task storage from JSON file writes to SQLite and merged manual tasks into dashboard API output.
- Files/Areas: `routes/project_status.py`, `public_html/dashboard.html`
- Notes / Follow-up: Default DB path is `data/project_tasks.sqlite`; override with `PROJECT_TASKS_DB_PATH` (recommended for Render persistent disk mount path).

### 2026-02-24
- Type: `decision`
- Summary: Reverted dashboard manual task entry and returned `/dashboard` to read-only task viewing.
- Files/Areas: `routes/project_status.py`, `public_html/dashboard.html`
- Notes / Follow-up: Continue updating `TASKS.md` and `data/project_tasks.json` manually as the project task source of truth.

---

### 2026-02-27
- Type: `feature`
- Summary: Built tool-use agent v2 (GPT-4o-mini) — LLM now calls tools instead of acting as a chat model. Wired into Flask as `/api/agent/v2` and `/api/agent/v2/stream`.
- Files/Areas: `routes/agent_v2.py`, `routes/agent_tools.py`, `routes/agent_api.py`, `public_html/chat_v2.js`
- Notes / Follow-up: Chat UI switches to v2 via `?agent=v2` URL param. Old `/api/agent` untouched.

### 2026-02-27
- Type: `feature`
- Summary: Built GPT-driven scenario testing infrastructure: 30 scenarios, test runner, analysis prompt, user simulator prompt.
- Files/Areas: `tests/scenarios_v2.json`, `tests/run_v2_scenarios.py`, `tests/gpt_analysis_prompt.md`, `tests/gpt_user_simulator_prompt.md`, `tests/codex_run_and_analyze.md`
- Notes / Follow-up: Run with `python tests/run_v2_scenarios.py` (prod) or `--env local` (Flask test client). Last run: 23/30 pass.

### 2026-02-27
- Type: `fix`
- Summary: Fixed wrong-day schedule bug — Render server (UTC) returned Saturday schedule after 7 PM Eastern because `date.today()` crossed midnight UTC.
- Files/Areas: `routes/schedule_service.py` (3 call sites)
- Notes / Follow-up: Replaced `date.today()` with `datetime.now(TZ).date()` using existing `TZ = ZoneInfo("America/New_York")`.

### 2026-02-27
- Type: `feature`
- Summary: Added customer service hours to all no-data / escalation messages (Mon–Fri 8 AM–5 PM / lun–vie 8 AM–5 PM).
- Files/Areas: `routes/agent_v2.py`, `routes/agent_api.py`
- Notes / Follow-up: Affects English and Spanish escalation paths.

### 2026-02-27
- Type: `feature`
- Summary: Added Quick Links panel to dashboard sidebar and expanded README with full live-site URLs and API endpoint table.
- Files/Areas: `public_html/dashboard.html`, `README.md`
- Notes / Follow-up: v2 agent test URL (`/chat?agent=v2`) highlighted in purple in dashboard.

### 2026-02-27
- Type: `docs`
- Summary: Documented Codex sandbox limitations — no network, no pip, no socket binding. Updated `codex_run_and_analyze.md` to use Flask test client (`--env local`) which avoids socket binding. Codex analyzes pre-existing results when pip packages unavailable.
- Files/Areas: `tests/codex_run_and_analyze.md`
- Notes / Follow-up: If Codex CLI installed locally, full test+fix loop works. In web sandbox, only file read/write/git is reliable.

---

### 2026-02-28
- Type: `fix`
- Summary: Fixed `get_route_day_summary` SQL bug — query used `stop_sequence = 1` (hardcoded) to find each trip's first stop. Routes whose GTFS trips start at a different sequence number (e.g. 0, 2) returned no rows and were incorrectly reported as `runs_today = False`. Replaced with a `trip_first_seq` CTE using `MIN(stop_sequence)` per trip.
- Files/Areas: `routes/schedule_service.py` — `get_route_day_summary()`
- Notes / Follow-up: This was likely the cause of "There are no trips for Route 75 today" when Route 75 was verifiably running. The `get_schedule` function was unaffected (it queries by stop, not stop_sequence).

### 2026-02-28
- Type: `fix`
- Summary: LLM incorrectly presented `get_route_overview` times as departures from a named stop (e.g. "departs from Rosa Parks at 11:13 PM"). The tool actually returns departure times from each trip's origin stop (stop_sequence=1), not from any named intermediate or terminal stop.
- Files/Areas: `routes/agent_v2.py` — added `## INTERPRETING get_route_overview RESULTS` section to SYSTEM_PROMPT
- Notes / Follow-up: Agent now says "Route X's last trip starts at HH:MM" rather than attributing the time to a specific stop it hasn't verified.

### 2026-02-28
- Type: `fix`
- Summary: Added "latest/last bus running today system-wide" as an explicit out-of-scope example in `## WHEN THE QUESTION IS BEYOND YOUR TOOLS`. Cross-route comparison (which route runs latest tonight?) is not supported by the per-route tools. Agent was attempting the comparison and hallucinating results.
- Files/Areas: `routes/agent_v2.py` — SYSTEM_PROMPT
- Notes / Follow-up: Also reformatted that section as a bullet list for clarity. May also improve S12/S13 failures — needs re-run to confirm.

### 2026-02-28
- Type: `fix`
- Summary: Added `## HANDLING DISAMBIGUATION RESPONSES` section to SYSTEM_PROMPT. When multiple stops are presented and the user says "it doesn't matter / any", the agent was forgetting time/date from the original query and defaulting to current time. Also: when the user corrects a parameter (e.g. "I said noon, not 8pm"), the agent was calling `search_routes` instead of retrying `get_schedule`.
- Files/Areas: `routes/agent_v2.py` — SYSTEM_PROMPT
- Notes / Follow-up: Explicit rules: pick first candidate, preserve all original params (time, date, route, kind), do NOT call search_routes on a parameter correction.

---

### 2026-03-18
- Type: `decision`
- Summary: Decided to migrate agent from GPT-4o-mini to Anthropic Claude API (Session 19 — Option B). After 18 sessions, the system prompt reached 276 lines with contradictions and competing rules. Direction filtering at hub stops (Rosa Parks) remained unresolved after 2 sessions of attempts. Production endpoint hits 30 req/hr rate limit with no graceful degradation. Core test pass rate was 95% (19/20) but real-world reliability felt inconsistent.
- Files/Areas: `TASKS.md`, `data/project_tasks.json`, `PROJECT_LOG.md`
- Notes / Follow-up: Plan — new `routes/agent_claude.py` using Claude tool-use, clean system prompt rewrite (<100 lines), direction filtering via GTFS `direction_id` at code level (no more prompt rules for this), API error graceful fallback. GTFS data refresh confirmed manual. Existing 5 tools (`agent_tools.py`) carry over unchanged.

### 2026-03-18
- Type: `feature`
- Summary: Session 19 Part 1 complete — claude-1, claude-2, claude-3, claude-6 done. New `routes/agent_claude.py` built using Anthropic SDK (v0.85.0). System prompt rewritten from 276 lines → ~90 lines, no contradictions, direction filtering removed from prompt (handled in code). `/api/agent/v3` and `/api/agent/v3/stream` endpoints wired in Flask. Live tests pass: route discovery, schedule fallback chain, Spanish real-time predictions.
- Files/Areas: `routes/agent_claude.py` (new), `routes/agent_api.py`, `requirements.txt`, `TASKS.md`
- Notes / Follow-up: Remaining: claude-4 (direction_id GTFS fix), claude-5 (rate limit fallback), claude-7 (test suite), claude-8 (deploy). Frontend toggle: `?agent=v3`.

### 2026-03-18
- Type: `fix`, `feature`
- Summary: Session 19 Part 2 — claude-4 and claude-5 complete. Direction filtering root cause found: GPT was using `kind="first"` for "after Xpm" queries (bypassing the code filter); Claude correctly uses `kind="next"`, so `_filter_inbound_departures()` in agent_tools.py runs and works. No GTFS direction_id changes needed. Also added rate-limit-specific error handling in agent_claude.py (429 → go-rts.com + phone message; other errors → retry message).
- Files/Areas: `routes/agent_claude.py`, `TASKS.md`
- Notes / Follow-up: claude-4 was a diagnosis, not a code change. claude-5 added 10 lines. Remaining: claude-7 (test suite run), claude-8 (deploy to Render).

### 2026-03-18
- Type: `fix`
- Summary: Post-deploy bug fixes — `routes_serving_destination` silently returned empty results due to wrong column key (`route_short_name` vs aliased `route_id`). Fixed "what bus goes to Sam's Club/Walmart/UF" returning no routes. Also fixed Claude hallucinating place name spellings (Jonsonville vs Jonesville) via system prompt rule.
- Files/Areas: `routes/schedule_service.py`, `routes/agent_claude.py`
- Notes / Follow-up: Silent exception swallowed by `except Exception: return []` — pattern to watch for in other query functions.

### 2026-03-18
- Type: `feature`, `decision`
- Summary: Built GPT-4o-mini v4 agent (`routes/agent_gpt_v3.py`) using same clean system prompt as Claude v3. Goal: verify if GPT-4o-mini can match Claude's 30/30 score at ~5x lower cost (~$45/mo vs ~$240/mo at 1k users). Reuses existing OPENAI_API_KEY. Wired as `/api/agent/v4`, dashboard and frontend updated.
- Files/Areas: `routes/agent_gpt_v3.py` (new), `routes/agent_api.py`, `public_html/chat_v2.js`, `public_html/dashboard.html`
- Notes / Follow-up: GPT-4o-mini scored 28/30 but hallucinates departure times from tool results (GPT19: returned 8:30 PM when tool said 7:52 PM). Decision: keep Claude Haiku v3 as default. v4 remains at `?agent=v4` for reference. Also added `kind="before"` to get_schedule tool (last departure before a time cutoff) and code-level guardrail redirecting kind=last+time → kind=before.
