# RTS Project Task Tracker

Last updated: 2026-03-19

This file is a project task tracker for the RTS backend/web assistant project. It captures:
- what has already been completed to reach the current state
- what is still pending
- what is blocked
- what should be done next

Note: The initial list below was inferred from the current repository contents and routes/pages present in the codebase.

## Completed

- [x] Set up Flask backend app factory and server entrypoint (`app.py`, `server.py`)
- [x] Added core API blueprints (health, BusTime, agent, schedule)
- [x] Added health monitoring endpoint with cache/session visibility
- [x] Built frontend pages in `public_html` (main page, chat, wizard)
- [x] Added AI assistant/web indexing endpoints (`/api/web/*`, agent routes)
- [x] Added deployment/config docs (`README.md`, `render.yaml`, `DEPLOYMENT_V2.md`, SOP docs)
- [x] Added project task dashboard endpoint/page (`/api/project/tasks`, `/dashboard`)
- [x] Built tool-use agent v2 — GPT-4o-mini with 5 tools (`routes/agent_v2.py`, `routes/agent_tools.py`)
- [x] Wired v2 into Flask (`/api/agent/v2`, `/api/agent/v2/stream`); chat UI toggle via `?agent=v2`
- [x] Built GPT-driven scenario test suite (30 scenarios, runner, analysis/simulator prompts)
- [x] Fixed UTC→Eastern timezone bug in schedule service (wrong day after 7 PM ET)
- [x] Added customer service hours to all no-data messages (Mon–Fri 8 AM–5 PM)
- [x] Added Quick Links panel to dashboard; expanded README with all URLs and API endpoints
- [x] Dashboard dark-mode redesign with progress ring, search, collapsible tasks
- [x] Human escalation after 2 consecutive unresolved turns
- [x] Real-time vs static schedule labeling in all responses
- [x] Fixed `get_route_day_summary` SQL bug — `stop_sequence = 1` hardcode replaced with `MIN(stop_sequence)` CTE; routes whose GTFS trips don't start at sequence 1 were incorrectly reported as having no service (`routes/schedule_service.py`)
- [x] Added `## INTERPRETING get_route_overview RESULTS` to system prompt — LLM now understands first/last times are from origin stop, not from a named terminus like Rosa Parks (`routes/agent_v2.py`)
- [x] Added `## HANDLING DISAMBIGUATION RESPONSES` to system prompt — LLM preserves all original query params (time, date, route, kind) after a disambiguation exchange; handles "it doesn't matter" and user corrections without calling wrong tools (`routes/agent_v2.py`)
- [x] Updated `## WHEN THE QUESTION IS BEYOND YOUR TOOLS` — added "latest/last bus running today system-wide" as explicit out-of-scope example; reformatted as bullet list for clarity (`routes/agent_v2.py`)

## 🔄 Active — Session 19: Option B — Migrate Agent to Claude API

**Decision (2026-03-18):** After 18 sessions of prompt-patching on GPT-4o-mini, the system prompt has grown to 276 lines with contradictions, direction filtering is unresolved, and rate limiting hits production hard. Decision: rebuild the agent layer using the Anthropic Claude API (claude-haiku-4-5 or claude-sonnet-4-6) with a clean system prompt rewrite.

- [x] `claude-1` Add `anthropic` SDK to `requirements.txt` (v0.85.0 installed)
- [x] `claude-2` Create `routes/agent_claude.py` — new agent loop using Claude tool-use API
- [x] `claude-3` Rewrite system prompt — clean, concise (<100 lines), no contradictions
- [x] `claude-4` Direction filtering validated — existing `_filter_inbound_departures()` in `agent_tools.py` works correctly with Claude. Root cause of prior failures was GPT misusing `kind="first"` instead of `kind="next"` for "after Xpm" queries, bypassing the filter. Claude uses the correct kind, filter runs, only outbound headsigns returned.
- [x] `claude-5` Rate limit / API error graceful degradation — rate_limit returns go-rts.com + phone, other errors return retry message
- [x] `claude-6` Wire new agent into Flask at `/api/agent/v3`; add frontend toggle `?agent=v3`
- [x] `claude-7` Run full test suite against v3 endpoint — **30/30 passing** (S12 fixed with prompt clarification)
- [x] `claude-8` Deploy to Render — pushed to main (2026-03-18); ANTHROPIC_API_KEY set in Render dashboard; smoke test v3 at /api/agent/v3

## 🔄 Active — Session 19 cont: Cost Comparison (v4 GPT-4o-mini + clean prompt)

**Goal:** Verify GPT-4o-mini scores 28+/30 with the new clean prompt. If so, switch default to v4 and save ~$200/mo at 1k users vs Claude Haiku.

- [x] `cost-1` Build `routes/agent_gpt_v3.py` — GPT-4o-mini with same clean system prompt as v3
- [x] `cost-2` Wire `/api/agent/v4` in Flask; add `?agent=v4` frontend toggle; dashboard link added
- [x] `cost-3` Ran full test suite against prod_v4 — GPT-4o-mini scored 28/30 (hallucinates departure times on edge cases)
- [x] `cost-4` **Decision: keep v3 (Claude Haiku) as default.** GPT-4o-mini hallucinates departure times from tool results — unacceptable for a transit assistant. v4 remains available at `?agent=v4` for reference.

## ✅ Session 19 cont. — Post-deploy Fixes & UI Redesign

- [x] Fixed `routes_serving_destination` silent empty result (wrong column key `route_short_name` → `route_id`)
- [x] Fixed agent hallucinating place name spellings ("Jonsonville" → system prompt rule)
- [x] Fixed S12/S13 — out-of-scope prompt rules strengthened (2026-03-18)
- [x] Injected active GTFS service type into agent context (`get_active_service_label()`) — agent can now answer "are we on reduced service today?" directly
- [x] Chat UI complete redesign — dark glassmorphism, Inter font, animated orbs, frosted-glass panel, bubble animations
- [x] Fixed CSS bubble class mismatch (`.bubble.user` / `.bubble.bot` to match JS) + added `renderMarkdown()` to JS
- [x] Fixed context loss in streaming endpoints — `session_manager.add_message()` moved before token streaming to prevent history loss on client disconnect
- [x] Added `## CONTEXT RETENTION` system prompt rule — agent scans history for most recent route/stop on ambiguous follow-ups

## ✅ Session 19 cont. — 2026-03-19: Pre-Presentation Polish

- [x] Added `get_route_stops` tool — agent can now list ordered stops for a route by direction/headsign (`routes/schedule_service.py`, `routes/agent_tools.py`, `routes/agent_claude.py`)
- [x] Extended service injection to 7-day table — agent can answer "is tomorrow reduced service?" and multi-day service questions without calling a tool
- [x] Added `get_route_first_last_by_service_type()` — `get_route_overview` now returns first/last per Weekday/Saturday/Sunday/Reduced in `schedule_by_service_type` key
- [x] Added ROUTE OVERVIEW RESPONSES prompt rule — agent always shows full service-type breakdown, not just today
- [x] Added TENSE + ETA prompt rule — past tense for elapsed times, `(~N min)` ETA appended when < 90 min away
- [x] Added CONTEXT RETENTION prompt rule — agent scans history for most recent route/stop on ambiguous follow-ups
- [x] Fixed S12/S14 false-positive test signals — `"both routes"` and `"depart"` were too broad
- [x] Fixed hallucination bug — agent invented Route 15 schedule at stop 221 (which Route 15 never serves). Root cause: `get_realtime_predictions` has no route filter; agent was ignoring its result and fabricating from training knowledge. Fix: new `route_not_at_stop` status returned by `get_schedule` when route+stop has zero GTFS trips; new ROUTE + STOP COMBINATION RULE in prompt forces `get_schedule` (with both `route_id` + `stop_id`) whenever user specifies both. (`routes/agent_tools.py`, `routes/agent_claude.py`)
- [x] Added ROUTE STOPS RESPONSES prompt rule — stop lists rendered as numbered list with stop ID in parentheses
- [x] Automated GPT analysis — `tests/auto_analyze.py` calls GPT-4o-mini API directly after test run; saves verdicts to `tests/analysis/`; no more manual ChatGPT copy-paste
- [x] Updated `gpt_analysis_prompt.md` — added `get_route_stops` + `route_not_at_stop`; bumped tool count to 7
- [x] Updated `codex_run_and_analyze.md` — points to v3 agent, documents `auto_analyze.py` shortcut

## 🗺️ Testing & Quality Roadmap

### Level 1 — Inline LLM-as-Judge ✅ DONE (2026-03-19)
- [x] Built `tests/run_and_judge.py` — single command, GPT-4o-mini judges inline per scenario
- [x] Uses `expected_behavior` as truth source — no keyword signals
- [x] Covers ~90% of regressions: behavioral errors, refusals, hallucination signals, context loss
- **Known gap:** Cannot verify factual accuracy of times/stops (no GTFS knowledge) — addressed in Level 1b
- Usage: `python tests/run_and_judge.py` · `--retry-fails` · `--no-judge` · `--ids S01,S07`

### Level 1b — Wire LLM Judge into replay_from_logs.py ✅ DONE (2026-03-19)
- [x] Replaced keyword signals with GPT-4o-mini inline judge in `replay_from_logs.py`
- [x] Keyword signals kept as fast pre-filter (skip LLM call on obvious failures)
- [x] Dropped ambiguous WARN verdict — now PASS/FAIL only, cleaner results
- [x] `--no-judge` flag for fast offline runs; auto-disables if no OPENAI_API_KEY
- [x] Judge prompt tuned for open-ended transit queries (no expected_behavior — quality judge, not correctness judge)
- Usage: `python tests/replay_from_logs.py` · `--last 100` · `--no-judge` · `--fails-only`

### Level 1c — GTFS-Grounded Verifier ✅ DONE (2026-03-19)
Closes the 10% gap the LLM judge cannot cover — factual accuracy of times, stops, headsigns.
- [x] Built `tests/judge_gtfs.py` — extracts routes/stops/times/negative-service claims via regex, queries `rts_gtfs.sqlite` directly to verify each one
- [x] Verifies: route exists, stop exists, route serves stop, route does NOT serve stop (negative claims), departure time within ±10min tolerance
- [x] Returns `PASS` / `FAIL` / `UNVERIFIABLE` with per-claim check list
- [x] Wired into `run_and_judge.py` as `--gtfs-verify` flag — escalates LLM PASS to FAIL if GTFS contradicts a claim
- [x] Runs locally with direct DB access — no external API needed
- [x] Smoke tested: correctly PASSes "Route 15 does not serve stop 221" and FAILs hallucinated "Route 15 departs at 6:00 AM from stop 221"
- Usage: `python tests/run_and_judge.py --gtfs-verify` or standalone `python tests/judge_gtfs.py --query "..." --response "..."`
- Pattern: **LLM generates → grounded verifier checks** — reusable for any structured-data domain

### QA Progress Tracking ✅ DONE (2026-03-19)
- [x] Built `tests/qa_report.py` — reads all result files, populates `tests/qa_history.sqlite`, prints trend/per-scenario reliability/regression diff
- [x] `routes/admin_api.py` now exposes `qa` key in `/api/dashboard/metrics` — latest scenario + replay pass rates + 5-run trend
- [x] Dashboard QA panel — shows scenario pass %, replay pass %, sparkline trend; hidden when no data yet
- Usage: `python tests/qa_report.py` · `--scenarios` · `--diff` · `--last 10`

### promote_to_scenario.py ✅ DONE (2026-03-19)
- [x] Built `tests/promote_to_scenario.py` — closes feedback loop: FAIL → reviewed → permanent scenario
- [x] Reads any `judged_*.json` or `replay_*.json` results file, surfaces FAILs not already in suite
- [x] Interactive mode: shows query + response + judge reason, prompts for `expected_behavior`, category, description
- [x] `--non-interactive` flag: auto-promotes FAILs that already have `expected_behavior` (CI use)
- [x] `--all` flag: dedupes across all result files; `--dry-run`: preview without writing
- Usage: `python tests/promote_to_scenario.py` · `--file <path>` · `--all` · `--dry-run` · `--non-interactive`

### Level 2 — Production Feedback Loop ✅ DONE (2026-03-19)
- [x] Real user queries logged to `data/analytics.sqlite`
- [x] Built `tests/replay_from_logs.py` — replays last N real conversations, scores PASS/FAIL
- [x] LLM judge (Level 1b) wired into replay scoring
- [x] `promote_to_scenario.py` closes the loop: replay FAIL → scenario suite
- Usage: run weekly or after any GTFS data refresh

### Level 3 — Adversarial Scenario Generation (Low priority / quarterly)
- [ ] Build `tests/generate_scenarios.py` — feeds route/stop list to GPT, returns 50 tricky test cases
- [ ] Auto-append to `scenarios_v2.json` after human review
- [ ] Focus: wrong stop IDs, route+stop mismatches, late-night edge cases, Spanish multi-turn

---

## 🏗️ Reference Architecture Notes
This project is being used as a model for future AI-powered development tools.

**Core patterns established here:**
1. **LLM tool-use agent** — LLM calls structured tools, never guesses from training data
2. **Clean grounded system prompt** — explicit rules, no contradictions, direction filtering in code not prompt
3. **LLM-as-judge testing** — `expected_behavior` as truth source, inline verdicts, no keyword signals
4. **Production feedback loop** — replay real user queries as regression tests
5. **GTFS-grounded verifier** (planned) — extract claims → verify against DB → grounded verdict

**Key lesson from 19 sessions:** LLM tool-use + grounded verification > prompt engineering + keyword testing.
The progression from GPT prompt-patching (276-line prompt, 18 sessions, unresolved direction filtering)
to clean Claude tool-use agent (session 19, 30/30 tests) demonstrates why architectural decisions matter
more than iterative prompt fixes.

## ✅ Session 19 cont. — 2026-03-19: Infrastructure & UX Fixes

- [x] SQLite session persistence — `utils/session_manager.py` now writes sessions to `sessions.sqlite` on every `add_message()`. On cache miss (server restart), sessions are restored from DB automatically. Background cleanup purges expired rows from both memory and SQLite.
- [x] Render Persistent Disk — `render.yaml` updated with `disk: rts-data, mountPath: /data, 1 GB`. `DATA_DIR=/data` env var wires both `analytics.sqlite` and `sessions.sqlite` to the persistent volume — data now survives redeploys.
- [x] `DATA_DIR` env var — `routes/agent_api.py` analytics paths and `utils/session_manager.py` session DB path both respect `DATA_DIR`. Local dev unchanged (defaults to `data/`).
- [x] Stop-only query — "stop 1492" with no question now calls `get_realtime_predictions` immediately instead of asking a clarifying question.
- [x] Reduced Service note — now rendered as a separate paragraph, not appended inline to the schedule answer.
- [x] Stop ID display — leading zeros stripped when showing stop IDs to users (1492 not 0001492).
- [x] Route-context disambiguation — follow-up place-name queries in a route-specific conversation now pass the known route_id to `get_schedule` directly, avoiding the generic search_stops disambiguation list.

## Pending (Carry-over)

- [x] `/dashboard`, `/chat`, `/wizard` PIN protection — set `DASHBOARD_PIN` + `SECRET_KEY` on Render
- [ ] Add GitHub Secrets: `RENDER_BACKEND_URL` + `DASHBOARD_PIN` for analytics backup action
- [ ] Find and document Hostinger frontend domain — add to README + CORS_ORIGINS in render.yaml
- [ ] Level 1b: wire LLM judge into `replay_from_logs.py` — ~30 min effort
- [ ] Level 1c: GTFS-grounded verifier (`tests/judge_gtfs.py`) — planned next major QA work
- [ ] GTFS/schedule data refresh — **manual process** (owner provides updated files directly when needed)
- [ ] Add route coincidence tool (where/when two routes share a stop) — deferred
- [ ] Trip planning tool (multi-leg A→B routing) — deferred; requires significant new tooling

## Blocked

- [ ] Realtime prediction reliability is limited by RTS/BusTime external API availability

## ✅ Session 19 cont. — 2026-03-19: Dashboard Interactivity + Replay QA

- [x] Dashboard wow redesign — animated orbs, glassmorphism hero, live metrics strip, health dots, activity feed, animated counters
- [x] `routes/admin_api.py` — `/api/dashboard/metrics` (live stats) + `/api/admin/analytics/export` (PIN-protected)
- [x] PIN login — `DASHBOARD_PIN` env var gates `/dashboard`; `SECRET_KEY` for session cookie
- [x] `.github/workflows/analytics-backup.yml` — weekly GitHub Action exports analytics JSON to `backups/analytics/`
- [x] Dashboard By Area clickable — clicking an area filters task list + scrolls to it; active area highlighted; clear button
- [x] Blocked alert banner — red callout at top of task list when blocked tasks exist; click to filter
- [x] Next-Up spotlight — top 3 "next" tasks shown as quick-action cards above task list
- [x] `tests/replay_from_logs.py` — Level 2 QA: replays last N real user queries from analytics.sqlite against live v3 agent; scores PASS/WARN/FAIL; saves results JSON

## Pending (Carry-over)

- [ ] Decide whether `/dashboard` and task API should require auth (PIN login built — just set DASHBOARD_PIN env var)
- [ ] GTFS/schedule data refresh — **manual process** (owner provides updated files directly when needed)
- [ ] Add route coincidence tool (where/when two routes share a stop) — deferred
- [ ] Trip planning tool (multi-leg A→B routing) — deferred; requires significant new tooling
- [ ] Find and document Hostinger frontend domain — add to README + CORS_ORIGINS in render.yaml
- [ ] Level 1 testing: inline LLM-as-judge in `run_v2_scenarios.py` (eliminates false positives permanently)
- [ ] Add GitHub Secrets for analytics backup: `RENDER_BACKEND_URL` + `DASHBOARD_PIN`

## Blocked

- [ ] Realtime prediction reliability is limited by RTS/BusTime external API availability

## Next Steps

1. Set `DASHBOARD_PIN` + `SECRET_KEY` + `RENDER_BACKEND_URL` on Render/GitHub if locking dashboard before presentation.
2. Level 1 testing upgrade: inline LLM-as-judge in `run_v2_scenarios.py`.
3. Run `python tests/replay_from_logs.py` after production traffic accumulates to catch real-world regressions.
