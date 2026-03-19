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

### 2026-03-18
- Type: `feature`
- Summary: Complete chat UI redesign — dark glassmorphism "wow effect". Rewrote `public_html/chat.html` from scratch with dark navy background (#070d1a), animated CSS gradient orbs, CSS grid overlay, frosted-glass chat panel (backdrop-filter: blur(28px)), Inter font, message bubble entrance animations, typing indicator, pill-shaped starter question chips, spring-animation send button, custom dark scrollbar. All JS IDs preserved for `chat_v2.js` compatibility.
- Files/Areas: `public_html/chat.html`
- Notes / Follow-up: CSS class fix included — `.bubble.user` / `.bubble.bot` match what `appendBubble()` in JS creates. `renderMarkdown()` added to JS to render `**bold**` and `*italic*` as HTML instead of raw asterisks. Input bar fixed to sit at bottom of panel card (not bottom of page).

### 2026-03-18
- Type: `fix`
- Summary: Injected active GTFS service type into agent context. Added `get_active_service_label()` to `schedule_service.py` — queries `calendar` and `calendar_dates` tables to determine today's service (Reduced Service, Regular Weekday, Saturday Schedule, etc.). Injected into the date header passed to both `agent_claude.py` and `agent_gpt_v3.py` so the agent can answer "are we on reduced service today?" directly without calling a tool.
- Files/Areas: `routes/schedule_service.py`, `routes/agent_claude.py`, `routes/agent_gpt_v3.py`
- Notes / Follow-up: GTFS `calendar_dates.txt` exception_type=2 records are used to identify reduced/holiday service days.

### 2026-03-18
- Type: `fix`
- Summary: Fixed conversation context loss bug in streaming endpoints. In `/api/agent/v3/stream` and `/api/agent/v2/stream`, `session_manager.add_message()` was called *after* streaming tokens were yielded. If the client disconnected mid-stream (network hiccup), the session history was never saved and the next message had no conversation context — producing "I don't have information about a previous query" failures. Fixed by moving `add_message()` calls to *before* token streaming begins.
- Files/Areas: `routes/agent_api.py`
- Notes / Follow-up: Also added `## CONTEXT RETENTION` rule to the Claude system prompt: when the user sends a follow-up ("what's the last bus today?") without repeating route/stop, agent must scan conversation history and reuse the most recently discussed route and stop rather than claiming it lacks context.

---

### 2026-03-19
- Type: `feature`
- Summary: Added `get_route_stops` tool — agent can now answer "what stops does route X make?" and "outbound stops for route 1". Built `get_route_stops()` in `schedule_service.py` using longest representative trip per headsign. Tool handler in `agent_tools.py` caps at 50 stops per direction. System prompt routing table updated.
- Files/Areas: `routes/schedule_service.py`, `routes/agent_tools.py`, `routes/agent_claude.py`
- Notes / Follow-up: Returns ordered stop list with stop_id, stop_name, and sequence per direction/headsign.

### 2026-03-19
- Type: `feature`
- Summary: Upgraded service type injection from single-day to 7-day table. Agent can now answer "is tomorrow reduced service?" and multi-day service questions for up to 7 days ahead without calling any tool. `get_route_overview` now also returns `schedule_by_service_type` (first/last per Weekday/Saturday/Sunday/Reduced).
- Files/Areas: `routes/schedule_service.py`, `routes/agent_claude.py`, `routes/agent_gpt_v3.py`
- Notes / Follow-up: Added ROUTE OVERVIEW RESPONSES prompt rule so agent always shows the full service-type table.

### 2026-03-19
- Type: `feature`
- Summary: Added tense + ETA formatting to schedule responses. Past tense ("was at 6:00 AM") for elapsed departures; approximate wait "(~N min)" appended when departure is within 90 minutes of current time.
- Files/Areas: `routes/agent_claude.py` — TENSE AND ETA FOR SCHEDULE RESULTS prompt rule
- Notes / Follow-up: Prompt-only change; no tool or service changes needed.

### 2026-03-19
- Type: `fix`
- Summary: Fixed critical hallucination — agent invented Route 15 schedule at stop 221, which Route 15 never serves. Root cause: agent called `get_realtime_predictions` (no route filter), got Route 6 data, ignored it, then fabricated Route 15 from training knowledge. Two-layer fix: (1) new `route_not_at_stop` status in `get_schedule` when route+stop has zero GTFS trips (unambiguous signal vs the old ambiguous `no_trips`); (2) new ROUTE + STOP COMBINATION RULE in system prompt forces `get_schedule` with both `route_id` and `stop_id` whenever user specifies both.
- Files/Areas: `routes/agent_tools.py` (`_route_never_serves_stop()` helper, `route_not_at_stop` status), `routes/agent_claude.py` (prompt rules)
- Notes / Follow-up: `_route_never_serves_stop()` does a lightweight DB COUNT query. If the route never has any trips at the stop, returns the unambiguous status immediately. Also strengthened the GROUND TRUTH RULE to explicitly cover this case.

### 2026-03-19
- Type: `feature`
- Summary: Automated the GPT analysis step of the test suite. New `tests/auto_analyze.py` reads the latest results JSON, calls GPT-4o-mini via OpenAI API, saves full analysis + parsed verdict JSON to `tests/analysis/`. Eliminates the manual copy-paste-into-ChatGPT workflow.
- Files/Areas: `tests/auto_analyze.py` (new), `tests/gpt_analysis_prompt.md`, `tests/codex_run_and_analyze.md`, `tests/run_v2_scenarios.py`
- Notes / Follow-up: Full loop is now `python tests/run_v2_scenarios.py && python tests/auto_analyze.py`. `--fails-only` flag available to save tokens when most scenarios pass.

### 2026-03-19
- Type: `decision`
- Summary: Evaluated current testing approach and defined a 3-level quality improvement roadmap. Current system (static JSON scenarios + keyword signals + GPT analysis) is functional but has known weaknesses: fragile signals, no regression tracking, no production feedback loop. Roadmap: Level 1 — inline LLM-as-judge (replace keyword signals); Level 2 — production query replay from analytics.sqlite; Level 3 — adversarial scenario generation from GTFS data.
- Files/Areas: `TASKS.md` — Testing & Quality Roadmap section
- Notes / Follow-up: Level 1 is the highest priority — eliminates false-positive signal failures permanently without requiring any infrastructure changes.

### 2026-03-19
- Type: `decision`
- Summary: Evaluated database architecture. Decision: keep SQLite for all three stores (GTFS, analytics, sessions). SQLite is the correct choice at this scale and operational context.
- Files/Areas: `rts_gtfs.sqlite` (13.8 MB, read-only GTFS), `data/analytics.sqlite` (append-only chat logs), session store (currently in-memory).
- Notes / Follow-up: GTFS is read-only at query time — SQLite's single-writer limitation is irrelevant. analytics.sqlite is append-only with rare reads — SQLite handles this cleanly. Estimated traffic (<200 queries/day for a city transit chatbot) is far below any SQLite ceiling. The one real gap is session persistence: in-memory OrderedDict is lost on every Render restart. Fix: add a `sessions` table to `analytics.sqlite` and persist session history there. No PostgreSQL migration needed at current or foreseeable scale.

### 2026-03-19
- Type: `feature`
- Summary: Added SQLite session persistence and Render Persistent Disk support. Sessions now survive server restarts and idle spin-downs via write-through SQLite in `utils/session_manager.py`. Analytics and session DB files now write to `/data` (Render Persistent Disk) so data survives redeploys. `DATA_DIR` env var controls the path — defaults to local `data/` folder, override to `/data` on Render.
- Files/Areas: `utils/session_manager.py` (SQLite write-through, DB restore on cache miss), `routes/agent_api.py` (DATA_DIR-aware paths), `render.yaml` (disk: rts-data, mountPath: /data, 1 GB; DATA_DIR=/data env var)
- Notes / Follow-up: Render Persistent Disk provisioned manually via dashboard (render.yaml disk config does not auto-provision on existing services). Starter plan required — confirmed active.

### 2026-03-19
- Type: `feature`
- Summary: Dashboard full wow redesign — animated gradient orb background, glassmorphism hero, live metrics strip (queries today, success rate, active sessions, avg response), pulsing system health dots (Claude API, BusTime API, GTFS DB, Sessions), Recent Activity feed from PROJECT_LOG.md, animated counters on all stat cards. Auto-refreshes metrics every 30s. PIN login via DASHBOARD_PIN env var.
- Files/Areas: `public_html/dashboard.html`, `routes/admin_api.py` (new), `app.py`
- Notes / Follow-up: Dashboard feeds from project_tasks.json (tasks) + /api/dashboard/metrics (live stats) + PROJECT_LOG.md (activity feed).

### 2026-03-19
- Type: `feature`
- Summary: Dashboard interactivity — By Area rows clickable (filters task list + scrolls, active area highlighted, clear button). Blocked alert banner when blocked tasks exist. Next-Up spotlight shows top 3 next tasks as quick-action cards. Clicking spotlight card jumps directly to that task.
- Files/Areas: `public_html/dashboard.html`
- Notes / Follow-up: Client-side only. Area filter stacks with status filter.

### 2026-03-19
- Type: `feature`
- Summary: Built Level 2 QA — tests/replay_from_logs.py. Loads real user queries from analytics.sqlite, replays against live v3 agent, scores PASS/WARN/FAIL, saves JSON results. Weekly GitHub Action (.github/workflows/analytics-backup.yml) exports analytics to backups/analytics/ and commits to repo.
- Files/Areas: `tests/replay_from_logs.py` (new), `.github/workflows/analytics-backup.yml` (new), `backups/analytics/`
- Notes / Follow-up: Requires RENDER_BACKEND_URL + DASHBOARD_PIN as GitHub Secrets for backup action. Run replay with `python tests/replay_from_logs.py --last 50`.

### 2026-03-19
- Type: `fix`
- Summary: Three UX fixes to the v3 Claude agent: (1) Reduced Service note now renders as a separate paragraph instead of appended inline to the last sentence. (2) Stop IDs displayed to users now strip leading zeros (show "1492" not "0001492"). (3) Route-context disambiguation — when user asks a place-name follow-up in a route-specific conversation (e.g. "what about from Butler Plaza?" after Route 1), agent now passes the known route_id to get_schedule directly instead of calling search_stops generically and showing an unrelated stop list.
- Files/Areas: `routes/agent_claude.py` — system prompt rules updated
- Notes / Follow-up: All prompt-only changes, no tool or service layer changes needed.

---

### 2026-03-19
- Type: `fix`
- Summary: Cleaned up project_tasks.json — consolidated 30+ inconsistent area labels to 8 clean categories (Backend, AI / Agent, Frontend, Quality, Security, Ops, Data, Project Management). Fixed stale statuses (claude-1 through claude-8, tooluse-7, fix-direction-filtering, pending-rerun-tests all marked completed). Removed duplicate task entry. Updated updated_at to 2026-03-19.
- Files/Areas: `data/project_tasks.json`
- Notes / Follow-up: Dashboard By Area filter now shows clean consistent categories. This file is the single source of truth for the dashboard — keep it in sync with TASKS.md.

### 2026-03-19
- Type: `feature`
- Summary: PIN protection extended to /chat and /wizard routes. Single unified /login endpoint with ?next= redirect parameter covers all protected pages (dashboard, chat, wizard). One PIN entry grants access to all three. Legacy /dashboard/login bookmarks still work via redirect.
- Files/Areas: `app.py` — /login, /logout routes; /chat and /wizard PIN checks added
- Notes / Follow-up: Activated when DASHBOARD_PIN env var is set on Render. SECRET_KEY env var controls session cookie signing. No PIN set = all pages open (dev mode).

### 2026-03-19
- Type: `fix`
- Summary: Agent no longer assumes reduced service affects all routes. Root cause: system prompt example note said "fewer trips than a normal weekday" — agent was copying this phrase for every route regardless of actual GTFS data. Fixed example to say "Note: today is Reduced Service." only, with an explicit rule: never claim a route is affected unless get_route_overview or get_schedule confirms it.
- Files/Areas: `routes/agent_claude.py` — date_header service note updated
- Notes / Follow-up: Some routes (e.g. Route 15) run identical schedules on reduced service days. The agent must only report what tool results show.

### 2026-03-19
- Type: `feature`
- Summary: Level 1 QA complete — built tests/run_and_judge.py. Single command replaces the old two-step run + copy-paste-to-ChatGPT workflow. Runs all scenarios against live agent, then calls GPT-4o-mini inline for a real PASS/FAIL verdict per scenario using expected_behavior as the truth source. No keyword signals. Saves to tests/results/judged_YYYYMMDD_HHMMSS.json.
- Files/Areas: `tests/run_and_judge.py` (new), `TASKS.md`
- Notes / Follow-up: Replaces fragile keyword signal scoring permanently. The judge reads expected_behavior + agent response and reasons about it. Known limitation: cannot verify factual accuracy of times/stops (no GTFS knowledge). Two planned follow-ups: (1) wire same judge into replay_from_logs.py; (2) build GTFS-grounded verifier for factual accuracy checks.

### 2026-03-19
- Type: `decision`
- Summary: QA roadmap evolution decision. Current judge (GPT-4o-mini, no GTFS context) catches ~90% of regressions — behavioral errors, refusals, hallucination signals, context loss. The 10% it misses are factual accuracy bugs (wrong times, wrong headsigns). Two-phase plan: Phase 1 — wire LLM judge into replay_from_logs.py (30-min effort, quick win); Phase 2 — GTFS-grounded verifier: extract claimed facts from agent response, query gtfs.db to verify, return grounded verdict. Phase 2 runs locally with direct DB access, no external API needed for the verification step.
- Files/Areas: `TASKS.md` — QA roadmap updated
- Notes / Follow-up: This project is being used as a reference architecture for AI-powered development tool workflows — the judge/verifier pattern (LLM generates, grounded verifier checks) is broadly applicable beyond transit.

### 2026-03-19
- Type: `feature`
- Summary: Level 1b complete — LLM judge wired into replay_from_logs.py. Upgraded replay scoring from keyword signals to GPT-4o-mini inline verdicts. Keyword signals kept as fast pre-filter. WARN verdict removed — clean PASS/FAIL only. --no-judge flag available for fast offline runs. Both QA pipelines (run_and_judge.py + replay_from_logs.py) now use real LLM verdicts.
- Files/Areas: `tests/replay_from_logs.py`
- Notes / Follow-up: Quality judge (not correctness judge) — asks "was this helpful?" since no expected_behavior exists for real user queries.

### 2026-03-19
- Type: `feature`
- Summary: 6 new scenarios added to scenarios_v2.json (30→36 total) covering every bug and feature added in sessions 17-19. S16: route_not_at_stop hallucination prevention. S17: stop-only query must show predictions immediately. S18: reduced service must not be generalized to unaffected routes. S19: get_route_stops numbered list. S20: service type answered from injected context (0 tool calls). M06: route-context disambiguation multi-turn.
- Files/Areas: `tests/scenarios_v2.json`
- Notes / Follow-up: Pattern: every bug fixed → immediately becomes a scenario. The scenario file is now a living record of every problem the agent has had.

### 2026-03-19
- Type: `feature`
- Summary: Level 1c complete — GTFS-grounded verifier (tests/judge_gtfs.py). Extracts routes/stops/times/negative-service claims from agent responses via regex, queries rts_gtfs.sqlite directly to verify each fact. Closes the 10% gap the LLM judge cannot cover: factual accuracy of departure times, route-stop relationships. Wired into run_and_judge.py as --gtfs-verify flag. Smoke tested: correctly catches hallucinated Route 15 departure at stop 221.
- Files/Areas: `tests/judge_gtfs.py` (new), `tests/run_and_judge.py` (--gtfs-verify flag)
- Notes / Follow-up: LLM generates → grounded verifier checks. This pattern (not just the code) is the key reference architecture contribution — reusable for any domain where an LLM answers questions grounded in structured data.

### 2026-03-19
- Type: `decision`
- Summary: Project identified as reference architecture for AI development tool workflows. Key patterns established: (1) LLM agent with tool-use API (not chat prompt engineering); (2) clean system prompt with explicit grounding rules; (3) automated test suite with LLM-as-judge; (4) production feedback loop via query replay; (5) GTFS-grounded verifier for factual accuracy. These patterns apply to any domain where an LLM must answer questions grounded in a structured data source.
- Files/Areas: `TASKS.md`, `PROJECT_LOG.md`
- Notes / Follow-up: The progression from GPT prompt-patching (18 sessions, 276-line prompt with contradictions) to clean Claude tool-use agent (session 19, 30/30 tests) is the core lesson: LLM tool-use + grounded verification > prompt engineering + keyword testing.
