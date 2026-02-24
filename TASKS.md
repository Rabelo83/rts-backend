# RTS Project Task Tracker

Last updated: 2026-02-24

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

## Pending

- [ ] Keep this task tracker updated after each feature/change
- [ ] Add/expand automated tests for routes and service logic
- [ ] Decide whether `/dashboard` and task API should require auth
- [ ] Improve production logging/monitoring/alerts
- [ ] Document and automate GTFS/schedule data refresh workflow

## Blocked

- [ ] Realtime prediction reliability is limited by RTS/BusTime external API availability
- [ ] Final project completion depends on agreed acceptance criteria / stakeholder signoff

## Next Steps (Recommended)

1. Define a clear "done" checklist (features, quality, deployment, ownership).
2. Add tests for the highest-risk routes (`/api/predictions`, `/api/agent`, `/api/health`).
3. Decide access control for `/dashboard` (public vs. protected).
4. Update this file and `data/project_tasks.json` after each completed task.
