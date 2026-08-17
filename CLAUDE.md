# RTS Assistant — Claude Code Project Guide

This file is read automatically by Claude Code at the start of every session.
It gives Claude full context so work can resume on any computer without re-explaining everything.

---

## What This Project Is

A web-based AI transit assistant for **RTS (Regional Transit System)** in Gainesville, FL.
Users can ask natural-language questions about bus routes, schedules, and trip planning.

Live URL: `rabelotestingenv.com` (Render deployment, auto-deploys from GitHub `main` branch)
Render URL: `https://rts-backend-7ru5.onrender.com`
GitHub: `https://github.com/Rabelo83/rts-backend`

---

## URL Map (post 2026-04-30 IA reorg)

| URL | Page | Auth | Notes |
|---|---|---|---|
| `/` | `chat.html` (Chat / Plan a Trip / Live Map tabs) | PIN-gated (if `DASHBOARD_PIN` set) | The real product. ONE app, one URL. |
| `/chat` | same as `/` | PIN-gated | Alias kept for old bookmarks + PWA `start_url` history |
| `/about` | `index.html` (legacy "RTS Bus Tracker" dropdown UI) | Public | Kept reachable for reference; can be repurposed as marketing landing |
| `/wizard` | redirect → `/` | — | Retired |
| `/dashboard` | `dashboard.html` admin/QA dashboard | PIN-gated | Internal |
| `/login` | PIN gate | — | Set `DASHBOARD_PIN` env var to enable |
| `/api/*` | JSON endpoints | Mixed | See blueprint files in `routes/` |

**Why this layout:** white-label commercial framing — each agency gets one URL that IS the app, no splash, no dropdown legacy. See `PROJECT_LOG.md` 2026-04-30 entries for full rationale.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask, Gunicorn |
| AI Agent | Anthropic Claude API (`claude-haiku-4-5` default, `claude-sonnet-4-6` optional) |
| GTFS Routing | In-memory RAPTOR engine (`utils/gtfs_engine.py`) — zero SQL during routing |
| Data | GTFS SQLite (`Backend Basics/db/rts_gtfs.sqlite`), stop enrichment GeoJSON |
| Real-time | RTS BusTime API (`rts_api.py`) |
| Frontend | Vanilla JS / HTML / CSS in `public_html/` |
| Hosting | Render (free tier → paid if needed) |
| Geocoding | Google Maps API (IP-restricted key) |

---

## Key Files

```
MULTI_AGENT_ROADMAP.md        Multi-Agent execution plan (read to determine role constraints)
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
  analytics.sqlite            Trip plan analytics (auto-created, NOT in git)
Backend Basics/
  RTSGTFS_<Season><Year>_V<n>/  Raw GTFS feed .txt files (versioned in git), e.g. RTSGTFS_Fall2026_V1/
  db/
    build_gtfs_db.py          Builds rts_gtfs.sqlite from GTFS_DIR (see GTFS update workflow below)
    rts_gtfs.sqlite            THE live GTFS database — NOT in git (*.sqlite is gitignored). It is a
                               BUILD ARTIFACT: render.yaml's buildCommand runs build_gtfs_db.py on
                               every deploy, regenerating this file from the git-tracked RTSGTFS_*
                               folder. Never upload this file manually — just push the raw feed.
                               Read at runtime by routes/schedule_service.py DB_PATH.
  bus_stops/
    bus_stops.geojson         Stop enrichment (street, direction, shelter, is_uf)
TASKS.md                      Full task history + current open items
PROJECT_LOG.md                Session-by-session decision log
```

---

## Environment Variables (set in Render dashboard)

```
ANTHROPIC_API_KEY       Claude API key (required for chat agent)
GEOCODING_PROVIDER      "google" | "nominatim" | "mapbox" (default: nominatim)
GOOGLE_GEOCODING_KEY    Google Geocoding API key (preferred). Legacy
                        GOOGLE_MAPS_API_KEY is also accepted as fallback.
                        IP-restrict to the Render server's egress IP.
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

GTFS update workflow (verified 2026-08-17, corrects earlier wrong notes — the DB is NEVER hand-uploaded):
1. Get the new raw GTFS feed (unzipped `.txt` files: `stops`, `routes`, `trips`, `stop_times`,
   `calendar`, `calendar_dates`, `fare_attributes`, `fare_rules`, `feed_info`, `shapes`, `agency`).
2. Drop them into a new folder under `Backend Basics/`, following the existing naming convention:
   `RTSGTFS_<Season><Year>_V<n>/` (e.g. `RTSGTFS_Fall2026_V1/`). These folders are git-tracked —
   old seasons (Spring2026_V6, Summer2026_V1) are kept for history, don't delete them.
3. Update `GTFS_DIR` in `Backend Basics/db/build_gtfs_db.py` (line ~9) to point at the new folder.
4. (Optional but recommended) Rebuild locally to sanity-check before pushing:
   `python3 "Backend Basics/db/build_gtfs_db.py"` — regenerates
   `Backend Basics/db/rts_gtfs.sqlite` locally so you can check row counts / `feed_info` service
   date range. This local file is gitignored and never pushed — it's just a local preview.
5. Commit + push the new `RTSGTFS_*_V*/` folder and the `GTFS_DIR` change in `build_gtfs_db.py`
   to `main`. **That's the entire deploy step — do not touch Render's disk.**
6. Render's `render.yaml` `buildCommand` (`pip install -r requirements.txt && python
   "Backend Basics/db/build_gtfs_db.py"`) runs `build_gtfs_db.py` fresh on every deploy, rebuilding
   `rts_gtfs.sqlite` from whatever `RTSGTFS_*` folder `GTFS_DIR` currently points to. The DB is a
   build artifact, not a persisted file — the `/data` persistent disk (mounted per `render.yaml`)
   is only for `analytics.sqlite`/`sessions.sqlite`, not the GTFS DB.
7. Verify: hit `/api/gtfs-info` after the deploy finishes and confirm `loaded_at` is fresh and
   `stops`/`trips` counts match the new feed.

Note: `scripts/gtfs_ingest.py` and `data/gtfs.sqlite` are a separate, currently-unused download-based
ingestion path (pulls a zip from a `GTFS_URL` env var). It is NOT what `render.yaml` or the live app
uses — ignore it unless this build-from-git-tracked-files workflow is being replaced with automated
fetching.

---

## AI Agent

- Default endpoint: `/api/agent/v3` (Claude Haiku)
- Tools: `get_next_departures`, `get_route_overview`, `get_route_day_summary`, `get_route_stops`, `plan_trip`
- System prompt: `routes/agent_claude.py` (clean, ~100 lines — do NOT let it grow)
- Fallback endpoint: `/api/agent/v4` (GPT-4o-mini) — kept for cost comparison only

---

## Deployment

- **Render service**: auto-deploys when `main` branch is pushed to GitHub
- **Build step** (`render.yaml` `buildCommand`): `pip install -r requirements.txt && python
  "Backend Basics/db/build_gtfs_db.py"` — this REBUILDS `rts_gtfs.sqlite` from the git-tracked
  `RTSGTFS_*` folder on every single deploy, even ones unrelated to GTFS. See "GTFS update workflow".
- **Persistent disk** (`/data`, mounted per `render.yaml`) only holds `analytics.sqlite` /
  `sessions.sqlite` — NOT the GTFS DB, which is always rebuilt fresh from git at deploy time.
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

- **CRITICAL MULTI-AGENT PROTOCOL**: Before modifying code, read `MULTI_AGENT_ROADMAP.md`. Note whether your instructions fall under "Agent Alpha" (UI/Frontend) or "Agent Bravo" (Backend). *Do not cross isolation boundaries.*
- The user is the sole developer; explain things clearly but don't over-explain
- Preferred commit style: concise subject line + Co-Authored-By footer
- Do NOT grow the agent system prompt — it causes regression bugs
- `Backend Basics/db/rts_gtfs.sqlite` is NOT in git and is NEVER uploaded to Render manually — it's
  a build artifact Render regenerates on every deploy via `render.yaml`'s `buildCommand` running
  `Backend Basics/db/build_gtfs_db.py`. To ship a new GTFS feed: commit the raw `.txt` files under a
  new `RTSGTFS_<Season><Year>_V<n>/` folder (these ARE git-tracked), update `GTFS_DIR` in
  `build_gtfs_db.py`, and push. See "GTFS update workflow" above.
- CISCO firewall may block Render URL on user's work PC — test from cell phone or home
- The `data/` directory contains runtime SQLite files — none should be committed
