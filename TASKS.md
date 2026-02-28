# RTS Project Task Tracker

Last updated: 2026-02-28

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

## Pending

- [ ] Fix S12/S13: out-of-scope questions redirecting to ETA prompt instead of "can't help" (re-run tests to confirm if BEYOND YOUR TOOLS update resolved it)
- [ ] Investigate M01/M02/M04/GPT13/GPT15: multi-turn scenarios returning empty responses in test runner
- [ ] Decide whether `/dashboard` and task API should require auth
- [ ] Improve production logging/monitoring/alerts
- [ ] Document and automate GTFS/schedule data refresh workflow
- [ ] Add route coincidence tool (where/when two routes share a stop)

## Blocked

- [ ] Realtime prediction reliability is limited by RTS/BusTime external API availability
- [ ] Codex web sandbox: no network/pip/socket — can only do file read/write/git; Codex CLI locally would unlock full test loop

## Next Steps (Recommended)

1. Re-run full test suite (`python tests/run_v2_scenarios.py --env local`) — target 28+/30. Fixes this session may have resolved S12/S13.
2. Debug multi-turn empty responses in test runner (M01/M02/M04/GPT13/GPT15).
3. Decide access control for `/dashboard` (public vs. protected).
4. Plan GTFS data refresh workflow (schedule data expires May 2026).
5. Update `data/project_tasks.json` to sync completed items to dashboard.
