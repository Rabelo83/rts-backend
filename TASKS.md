# RTS Project Task Tracker

Last updated: 2026-02-27

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

## Pending

- [ ] Fix S12/S13: out-of-scope questions redirecting to ETA prompt instead of "can't help"
- [ ] Investigate M01/M02/M04/GPT13/GPT15: multi-turn scenarios returning empty responses in test runner
- [ ] Decide whether `/dashboard` and task API should require auth
- [ ] Improve production logging/monitoring/alerts
- [ ] Document and automate GTFS/schedule data refresh workflow
- [ ] Add route coincidence tool (where/when two routes share a stop)

## Blocked

- [ ] Realtime prediction reliability is limited by RTS/BusTime external API availability
- [ ] Codex web sandbox: no network/pip/socket — can only do file read/write/git; Codex CLI locally would unlock full test loop

## Next Steps (Recommended)

1. Fix S12/S13 out-of-scope system prompt (safe Claude fix).
2. Debug multi-turn empty responses in test runner.
3. Run full test suite after fixes; target 28+/30.
4. Decide access control for `/dashboard` (public vs. protected).
5. Plan GTFS data refresh workflow (schedule data expires May 2026).
