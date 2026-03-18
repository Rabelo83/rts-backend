# RTS Project Task Tracker

Last updated: 2026-03-18

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

## Pending (Carry-over)

- [ ] Fix S12/S13: out-of-scope questions redirecting to ETA prompt (may be resolved by system prompt rewrite)
- [ ] Investigate M01/M02/M04/GPT13/GPT15: multi-turn scenarios returning empty responses in test runner
- [ ] Decide whether `/dashboard` and task API should require auth
- [ ] Improve production logging/monitoring/alerts
- [ ] GTFS/schedule data refresh — **manual process** (owner provides updated files directly when needed)
- [ ] Add route coincidence tool (where/when two routes share a stop)

## Blocked

- [ ] Realtime prediction reliability is limited by RTS/BusTime external API availability

## Next Steps

1. Start Session 19: implement `claude-1` through `claude-3` (SDK + new agent + clean system prompt).
2. Validate direction filtering fix via GTFS `direction_id` field (`claude-4`).
3. Wire and test end-to-end (`claude-5` through `claude-7`).
4. Deploy and flip default to v3 (`claude-8`).
