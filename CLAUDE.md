# RTS Assistant — Claude Code Project Guide

This file is read automatically by Claude Code at the start of every session.
It gives Claude full context so work can resume on any computer without re-explaining everything.

---

## What This Project Is

A web-based AI transit assistant for **RTS (Regional Transit System)** in Gainesville, FL.
Users can ask natural-language questions about bus routes, schedules, and trip planning.

Live URL: `rabelotestingenv.com` (Render deployment, auto-deploys from GitHub `main` branch)
GitHub: `https://github.com/Rabelo83/rts-backend`

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask, Gunicorn |
| AI Agent | Anthropic Claude API (`claude-haiku-4-5` default, `claude-sonnet-4-6` optional) |
| GTFS Routing | In-memory RAPTOR engine (`utils/gtfs_engine.py`) — zero SQL during routing |
| Data | GTFS SQLite (`rts_gtfs.sqlite`), stop enrichment GeoJSON |
| Real-time | RTS BusTime API (`rts_api.py`) |
| Frontend | Vanilla JS / HTML / CSS in `public_html/` |
| Hosting | Render (free tier → paid if needed) |
| Geocoding | Google Maps API (IP-restricted key) |

---

## Key Files

```
app.py                        Flask app factory, blueprints, login/auth
routes/
  agent_claude.py             Claude AI agent — main chat endpoint /api/agent/v3
  agent_tools.py              Tool implementations (schedule queries)
  agent_service.py            GTFS/schedule query helpers
  schedule_service.py         DB_PATH, get_active_service_label(), GTFS SQL helpers
  schedule_api.py             /api/schedule/* endpoints
  trip_api.py                 /api/trip/plan endpoint + geocode autocomplete
  bustime.py                  /api/bustime/* real-time proxy
  health.py                   /api/health
  admin_api.py                /api/admin/* (PIN-protected)
utils/
  gtfs_engine.py              In-memory GTFS graph + RAPTOR router (loaded at startup)
  trip_planner.py             find_trips() — thin wrapper over GTFSEngine
  stop_finder.py              find_nearest_stops(), get_stop_by_id(), enrichment
  geocoding.py                Google Maps geocoding wrapper
  limiter.py                  Flask-Limiter setup
public_html/
  index.html / chat.html / wizard.html / dashboard.html
  trip_planner.js             Trip planner UI (autocomplete, rendering, swap)
data/
  rts_gtfs.sqlite             GTFS database (NOT in git — upload to Render manually)
  analytics.sqlite            Trip plan analytics (auto-created, NOT in git)
Backend Basics/bus_stops/
  bus_stops.geojson           Stop enrichment (street, direction, shelter, is_uf)
TASKS.md                      Full task history + current open items
PROJECT_LOG.md                Session-by-session decision log
```

---

## Environment Variables (set in Render dashboard)

```
ANTHROPIC_API_KEY       Claude API key (required for chat agent)
GOOGLE_MAPS_API_KEY     Google Maps geocoding key (IP-restricted to Render server IP)
RTS_API_KEY             RTS BusTime real-time API key
SECRET_KEY              Flask session secret (any random string)
DASHBOARD_PIN           Optional PIN to protect /dashboard, /chat, /wizard
CORS_ORIGINS            Allowed origins (e.g. https://rabelotestingenv.com)
DATA_DIR                Path to data directory (default: ./data)
```

Local dev: copy `.env.local.example` to `.env.local` and fill in keys.
Run locally: `bash run_local.sh`

---

## Architecture: GTFS Routing

The routing engine (`utils/gtfs_engine.py`) loads ALL GTFS data into memory at Flask startup (~2-3s, ~10MB RAM). Zero SQL during trip planning.

- **`GTFSEngine`** — singleton loaded once via `get_engine()`
- **`route_depart()`** — RAPTOR forward routing (depart-after mode)
- **`route_arrive()`** — RAPTOR backward routing (arrive-by mode)
- **Transfer hubs fallback** — `_find_via_hub()` in `trip_planner.py` for 2-transfer gaps
- **`/api/gtfs-info`** — returns `{stops, trips, loaded_at}` to verify engine is loaded

GTFS update workflow:
1. Replace `data/rts_gtfs.sqlite` with new file
2. Push or redeploy on Render — engine reloads automatically at startup

---

## AI Agent

- Default endpoint: `/api/agent/v3` (Claude Haiku)
- Tools: `get_next_departures`, `get_route_overview`, `get_route_day_summary`, `get_route_stops`, `plan_trip`
- System prompt: `routes/agent_claude.py` (clean, ~100 lines — do NOT let it grow)
- Fallback endpoint: `/api/agent/v4` (GPT-4o-mini) — kept for cost comparison only

---

## Deployment

- **Render service**: auto-deploys when `main` branch is pushed to GitHub
- **Deploy time**: ~2-3 min (includes GTFSEngine load)
- **Check deploy**: hit `/api/gtfs-info` or `/api/health` after deploy
- **Logs**: Render dashboard → Logs tab (check here for Python tracebacks)

---

## Current Open Issues (as of 2026-03-23)

- **`raptor-6`** — QA: retest SE 13th Rd → SW 8th Ave (both directions), verify transfer badge count, verify no 5-route options
- **SW 8th Ave → SE 13th Rd bug** — shows "Could not connect to server." For one direction. Fix deployed (commit 78b9abc): wrapped find_trips() in try/except so Python errors now return JSON error instead of HTML 500. If error still occurs, check Render logs for actual traceback.
- **CORS_ORIGINS** — confirm `rabelotestingenv.com` is in Render env var

---

## Test Suite

```bash
cd tests/
python run_tests.py              # runs 30 scenarios against /api/agent/v3
python auto_analyze.py           # GPT analysis of latest results
```

Results saved to `tests/results/`. QA history in `tests/qa_history.sqlite`.

---

## Notes for Claude

- The user is the sole developer; explain things clearly but don't over-explain
- Preferred commit style: concise subject line + Co-Authored-By footer
- Do NOT grow the agent system prompt — it causes regression bugs
- `rts_gtfs.sqlite` is NOT in git — it lives on Render's disk; user uploads it manually
- CISCO firewall may block Render URL on user's work PC — test from cell phone or home
- The `data/` directory contains runtime SQLite files — none should be committed
