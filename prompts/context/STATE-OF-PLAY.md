# State of play — read after `project-brief.md`

This file captures the **current state of the work-in-progress** so a delegated AI (Codex, Sonnet, Haiku, etc.) can pick up cold without reading the entire repo. Updated each session.

> Last updated: **2026-05-01** (Live Map refinement batch documented; kickoff docs refreshed)

---

## What's live in production right now

`https://rts-backend-7ru5.onrender.com` (PIN-gated via `DASHBOARD_PIN` env var; Render auto-deploys from `main`).

### URL map (post 2026-04-30 IA reorg)

| URL | Page | Auth | Purpose |
|---|---|---|---|
| `/` | `chat.html` (Chat / Plan a Trip / Live Map tabs) | PIN-gated | **The product.** ONE app, one URL. |
| `/chat` | same as `/` | PIN-gated | Alias for old bookmarks + historical PWA `start_url` |
| `/about` | `index.html` (legacy dropdown UI) | Public | Kept reachable for white-label marketing repurpose |
| `/wizard` | redirect → `/` | — | Retired |
| `/dashboard` | admin/QA dashboard | PIN-gated | Internal |
| `/api/*` | JSON endpoints | Mixed | See blueprints in `routes/` |

### Frontend tabs (inside `chat.html`)

1. **💬 Chat** — Claude Haiku tool-use agent (12 tools)
2. **🚌 Plan a Trip** — RAPTOR trip planner (in-memory, sub-second)
3. **🗺️ Live Map** — MapLibre GL + OpenFreeMap, live BusTime markers, route filter rail, bottom sheet on tap with deep-links to chat / trip planner

---

## What shipped recently (newest work first)

| Commit | Subject | Why |
|---|---|---|
| `(current batch)` | `feat(map): refine live map route + stop overlays` | The map now surfaces the same route-summary value the chat agent has: top-of-map route overview drawer, hide/reopen behavior, scroll hint, forward-looking stop schedules that roll into tomorrow/next service day, more prominent stop IDs, and more literal bus markers. |
| `25be4b1` | `fix(map): show scheduled departures in stop sheet` | Stop taps now show GTFS-backed scheduled departures alongside live ETAs instead of just real-time predictions. |
| `0de42e1` | `fix(pwa): network-first HTML; stop precaching auth-gated routes` | Installed PWAs were getting stale or login-page HTML. Navigation is now network-first and auth redirects no longer poison the cache. |
| `3286cb8` | `fix(trip): anchor landmarks to GTFS stop_id` | Chat agent and Trip Planner returned different itineraries for "Rosa Parks" — landmark coords were 800 m – 3.4 km off. Fixed by adding optional `stop_id` to landmarks; `find_trips()` pegs directly to that stop with 0-min walk. |
| `e856724` | `feat(map): Live Map MVP` | Replacement-grade pillar — Go RTS / RideRTS ship a live map. Stack: MapLibre + OpenFreeMap (zero per-request cost; required for white-label margin discipline). |
| `97f74b7` | `feat(ia): / now serves the AI app` | First-time visitors at the bare URL were landing on the legacy dropdown UI and never discovering the AI assistant. The legacy page was *competing* with the actual product. |
| `b8b30cd` | `fix(map+chat): real ETAs in stop sheet + system-wide vehicle count tool` | Map's stop sheet was hard-wired to raw BusTime field names; `/api/predictions` normalizes them. Also added `get_active_vehicles_systemwide` tool so the agent can answer "how many buses are running now" across the whole system. |
| `8e595c1` | `docs(prompts): Codex handoff package` | Adds STATE-OF-PLAY.md (this file) + codex-kickoff.md so Codex can pick up cold without burning Opus tokens on context-rebuilding. |

---

## Agent tools (12 total — `routes/agent_tools.py`)

`search_stops`, `get_realtime_predictions`, `get_schedule`, `search_routes`, `suggest_destinations`, `get_route_overview`, `get_route_stops`, `get_route_vehicle_count`, `get_vehicle_location`, **`get_active_vehicles_systemwide`** (new 2026-04-30), `plan_trip`, `get_service_differences`.

Routing table (which tool to pick for which user query) lives in [routes/agent_claude.py](../../routes/agent_claude.py) — search for `## TOOL ROUTING`. Do **not** add system-prompt rules to fix tool-selection bugs; fix the routing table or the tool description instead.

---

## Live Map architecture

- **Backend**: [routes/map_api.py](../../routes/map_api.py) — live-map endpoints for routes, route detail, route overview, vehicles, and stop schedules. Vehicle aggregation is cached server-side for 5 seconds and batched across routes to amortize BusTime load.
- **Frontend**: [public_html/map.js](../../public_html/map.js) — lazy-init on first tab switch, MapLibre + OpenFreeMap (`tiles.openfreemap.org/styles/liberty`), 10s polling paused on `visibilitychange`.
- **Route overlay**: selecting a route chip or tapping a bus opens a top-of-map route summary drawer with today's directions, first/last runs, frequency, service-type hours, hide/reopen controls, and a scroll hint when content overflows.
- **Stop overlay**: tapping a stop shows live ETAs and the **next actual scheduled departures**, even if service has rolled into tomorrow or the next service day. The stop sheet now labels that service day explicitly and shows a more prominent stop ID badge.
- **Differentiation lever**: the stop/bus sheets still carry "Ask the Assistant" / "Plan trip from here" deep-links, so the visual surface hands off into the AI surface with context preloaded.
- **Current UI note**: bus markers are now a more literal front-facing mini-bus icon rather than plain circles; this is functional, but a future art pass may still improve the feel.

---

## Open work — explicitly NOT done

These are the live punch-list items. Each is a candidate task to delegate.

### Trip Planner trust bugs (in [TASKS.md](../../TASKS.md), captured 2026-04-23)
- **Pathological arrive-by itineraries** — "Arrive by 2:29 PM" returns "9:53 AM → 2:13 PM" (4.5h trip when 15 min exists). Bound earliest dep to `arrive_by - 2h`; reject travel time > 2× shortest viable.
- **Card-label ↔ expanded mismatch** — collapsed card says "7 transfers, 52 min", expanded shows 1 transfer, 28 min. Audit backend → card-badge mapping in `public_html/trip_planner.js`.
- **Walk-cap not enforced on final leg** — already fixed for trip planner output (`_MAX_FINAL_WALK_MIN = 12`) but verify it applies to all paths including hub-relay fallback.

### Live Map polish (post-MVP)
- Greyed-out chips for routes with no service today (use `engine.service_ids_for_date()`)
- Smoother marker tween between polls (currently snaps)
- Per-direction polyline coloring (inbound desaturated)
- Cluster overlapping stops at low zoom (~970 stops at zoom 11 = busy)
- Real-device QA pass (iOS Safari + Android Chrome)
- Optional marker-art pass if the current mini-bus icon still feels too badge-like on-device

### IA reorg follow-ups
- Real-device PWA install test — verify "Add to Home Screen" lands on `/` (not `/chat`) and that previously-installed PWAs migrate cleanly when SW v10 activates. iOS Safari is the high-risk path.

### Chat agent regressions seen in production (2026-04-23)
- Direction filter miss on Oaks Mall for Route 5 (terminus list incomplete in `_filter_inbound_departures`).
- `get_vehicle_location` says 1 active bus on Route 8 while `get_route_vehicle_count` says no scheduled trips today — investigate which is wrong.

### POI / landmark curation (manual, non-blocking)
- `scripts/extract_landmarks_from_gtfs.py` surfaces 543 candidates. Curate ~30–50 high-value entries into `agency_config.yaml > common_destinations.landmarks`.

---

## Things you should know

- **No hardcoded agency content.** Anything Gainesville-specific must route through [agency_config.yaml](../../agency_config.yaml). White-label is the commercial thesis.
- **Default to free / self-hostable services.** Never propose Google paid APIs as default. Geocoding goes through Nominatim by default; map tiles use OpenFreeMap. See `feedback_zero_per_request_cost` in user memory if available.
- **Local dev**: macOS port 5000 is hijacked by AirPlay Receiver. Use `--port 5050`.
- **GTFS database** (`Backend Basics/db/rts_gtfs.sqlite`) is **not** in git — uploaded to Render's disk manually. Don't propose automation until agency #2 onboards.
- **Tests**: `cd tests/ && python run_tests.py` (30 scenarios against `/api/agent/v3`). `python tests/replay_from_logs.py` for production-traffic replay.

---

## How to report back

When the delegated AI finishes a task, it should report:
1. **What changed** (files + line ranges).
2. **Why** (link the change to the strategic rule it serves — e.g. "unblocks replicability rule #1" or "addresses the trust bug listed in STATE-OF-PLAY").
3. **Verification** — tests run, greps confirming cleanliness, smoke-test results.
4. **Suggested commit message** in the project's format (subject + `Co-Authored-By: <Model> <noreply@...>` footer).
5. **What's next** — if the task surfaced new follow-ups, name them.
