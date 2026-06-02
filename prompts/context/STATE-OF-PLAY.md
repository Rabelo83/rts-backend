# State of play — read after `project-brief.md`

This file captures the **current state of the work-in-progress** so a delegated AI (Codex, Sonnet, Haiku, etc.) can pick up cold without reading the entire repo. Updated each session.

> Last updated: **2026-06-02** (Live Map route schedule drill-in + post-deploy backend fix)

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
   - Route summaries now include a DB-backed `View full schedule` drill-in.
   - Tapped buses now include `View route schedule` so riders can jump from a live vehicle into that route's timetable.

---

## What shipped recently (newest work first)

| Commit | Subject | Why |
|---|---|---|
| `(pending)` | `fix(schedule): import formatter for route schedule drill-in` | Post-deploy fix for the new full-schedule flow. The first version returned `route_not_found` for valid routes because `get_route_departure_schedule()` called `format_time_12h()` without importing it, then swallowed the `NameError` and surfaced a false 404. |
| `(pending)` | `feat(map): add route schedule drill-in` | First rider-facing non-chat schedule surface. Route summary drawers and bus sheets can now open DB-backed route schedules grouped by direction, with `Today` / `Tomorrow` toggles. This is a first step toward a broader schedule UX, not the final product shape. |
| `(pending)` | `fix(agent): prefer ETA for route-stop next questions` | Current GTFS calendar is stale, but BusTime live predictions still show active vehicles. The Claude prompt now sends route+stop "next"/ETA questions to realtime first, and `get_realtime_predictions` accepts `route_id` so the tool can return only that route's ETA before falling back to schedule. |
| `(pending)` | `fix(map): keep selected stop visible above sheet` | Stop-ID search and stop taps now use MapLibre's camera `offset` after the ETA sheet renders, so the pulsing selected-stop marker remains visible above the bottom sheet even while zooming. Mobile CSS also uses 16px inputs, `100svh`, and safe-area sheet spacing to avoid the phone browser zooming/oversizing the app. |
| `(pending)` | `feat(agent): system-wide first/last bus tool` | Closes the gap exposed by a real 2026-05-03 conversation — user asked "first bus today across all routes," agent admitted "I don't have a tool" and fell back to `https://go-rts.com` (the competitor URL — captured as a deferred prompt fix). New `get_system_first_last_today` iterates the route inventory, returns earliest first / latest last + per-route breakdown. |
| `7e2756d` | `fix(map): agency-config-driven default viewport` | Last Gainesville hardcode removed from `map.js`. New `map.default_view` block in `agency_config.yaml`; `/api/map/routes` payload now ships it alongside routes. Map fetches before construction so the agency's view is in place from frame 1. White-label rule #1 enforced. |
| `(2026-05-01)` | `feat(map): multi-select route filters + route line polish` | Route chips now support selecting multiple routes at once; `All` clears the filter. Selected routes render together with softer supporting polylines and deduped stop dots, while buses remain the primary visual objects. |
| `ccef4c1` | `fix(map): refine bus icons and stop hover cards` | Replaced front-facing bus badges with side-view bus silhouettes, added custom stop hover/focus cards, and fixed route tray / route-info overlap. |
| `10d0896` | `fix(map): simplify live map info surfaces` | Route and stop info are mutually exclusive; stop sheet shows live ETAs first and scheduled departures only as fallback; route selector became expandable. |
| `d054251` | `fix(map): polish live map controls` | Compacted stop-ID lookup, aligned route selector visuals with bus markers, and moved the route-info hide control to the bottom of the drawer. |
| `3cc4d0c` | `feat(map): stop-ID search + you-are-here pin + nearby-stops sheet` | Two UX gaps in Live Map closed. Geolocation now drops a pulsing user pin and opens a "5 nearest stops" sheet; new search input lets users type a stop ID and jump to it. Schedule endpoint extended with lat/lon (one round trip). Mirrors chat agent's "ETA if available else next scheduled" semantics. |
| `8b91ee6` | `fix(trip): kill pathological arrive-by + latest-first sort + engine KeyError` | Closes trust bug #1 from 2026-04-23 punch list. Pathological cap (`>2× shortest viable`), arrive-by sorts latest-departure-first, fixes pre-existing `_build_itinerary` crash on walk-transfer-after-transfer. |
| `392e4fb` | `feat(map): refine live map route + stop overlays` (Codex) | Top-of-map route overview drawer, hide/reopen behavior, scroll hint, forward-looking stop schedules that roll into tomorrow/next service day, more prominent stop IDs, and more literal bus markers. |
| `25be4b1` | `fix(map): show scheduled departures in stop sheet` | Stop taps now show GTFS-backed scheduled departures alongside live ETAs instead of just real-time predictions. |
| `0de42e1` | `fix(pwa): network-first HTML; stop precaching auth-gated routes` | Installed PWAs were getting stale or login-page HTML. Navigation is now network-first and auth redirects no longer poison the cache. |
| `3286cb8` | `fix(trip): anchor landmarks to GTFS stop_id` | Chat agent and Trip Planner returned different itineraries for "Rosa Parks" — landmark coords were 800 m – 3.4 km off. Fixed by adding optional `stop_id` to landmarks; `find_trips()` pegs directly to that stop with 0-min walk. |
| `e856724` | `feat(map): Live Map MVP` | Replacement-grade pillar — Go RTS / RideRTS ship a live map. Stack: MapLibre + OpenFreeMap (zero per-request cost; required for white-label margin discipline). |
| `97f74b7` | `feat(ia): / now serves the AI app` | First-time visitors at the bare URL were landing on the legacy dropdown UI and never discovering the AI assistant. The legacy page was *competing* with the actual product. |
| `b8b30cd` | `fix(map+chat): real ETAs in stop sheet + system-wide vehicle count tool` | Map's stop sheet was hard-wired to raw BusTime field names; `/api/predictions` normalizes them. Also added `get_active_vehicles_systemwide` tool so the agent can answer "how many buses are running now" across the whole system. |
| `8e595c1` | `docs(prompts): Codex handoff package` | Adds STATE-OF-PLAY.md (this file) + codex-kickoff.md so Codex can pick up cold without burning Opus tokens on context-rebuilding. |

---

## Agent tools (13 total — `routes/agent_tools.py`)

`search_stops`, `get_realtime_predictions`, `get_schedule`, `search_routes`, `suggest_destinations`, `get_route_overview`, `get_route_stops`, `get_route_vehicle_count`, `get_vehicle_location`, **`get_active_vehicles_systemwide`** (2026-04-30), **`get_system_first_last_today`** (new 2026-05-03), `plan_trip`, `get_service_differences`.

Routing table (which tool to pick for which user query) lives in [routes/agent_claude.py](../../routes/agent_claude.py) — search for `## TOOL ROUTING`. Do **not** add system-prompt rules to fix tool-selection bugs; fix the routing table or the tool description instead.

---

## Live Map architecture

- **Backend**: [routes/map_api.py](../../routes/map_api.py) — live-map endpoints:
  - `/api/map/routes` — chip rail data
  - `/api/map/route/<id>` — polylines + stops
  - `/api/map/route/<id>/overview` — first/last/frequency drawer
  - `/api/map/route/<id>/schedule` — full scheduled departures grouped by direction/origin stop for one route/day
  - `/api/map/vehicles` — all active vehicles, 30s cache, batched 10/call; explicit BusTime limit/unavailable status when the vendor returns an error payload
  - `/api/map/vehicle/<vehicle_id>/predictions` — upcoming stop ETAs for one tapped bus
  - `/api/map/stop/<id>/schedule` — scheduled departures (rolls forward to next service day) + lat/lon
  - `/api/map/nearby-stops?lat&lon&radius_m&limit` — used by the geolocation flow
- **Frontend**: [public_html/map.js](../../public_html/map.js) — lazy-init on first tab switch, MapLibre + OpenFreeMap (`tiles.openfreemap.org/styles/liberty`), 30s vehicle polling paused on `visibilitychange`.
- **BusTime quota**: [rts_api.py](../../rts_api.py) supports `BUS_API_KEYS` as a comma-separated list of authorized fallback keys. It tries the next key only when BusTime returns a transaction-limit error.
- **Route selector**: compact mobile-first toolbar with `Routes: All` on the left and Stop ID search on the right. Route chips live in an expandable tray under the Routes button and are multi-select. Empty selection means **All** active vehicles; tapping route chips adds/removes them and opens that route's summary; tapping **All** clears the set. Selected routes render together with softer polylines and deduped route-colored stop dots.
- **Route overlay**: tapping a route chip or reopening a route summary opens a top-of-map route drawer anchored to the map canvas with today's directions, first/last runs, frequency, service-type hours, hide/reopen controls, and a scroll hint when content overflows.
- **Route schedule drill-in** (2026-06-02): the route drawer now exposes `View full schedule`, which opens a bottom sheet with GTFS-backed route departures grouped by direction/headsign and origin stop. The sheet currently supports `Today` / `Tomorrow` toggles and is intentionally an on-demand view, not a preload of all route schedules.
- **Bus overlay**: tapping a bus selects its route and opens a bottom sheet with destination plus upcoming stop ETAs for that specific vehicle. Speed is intentionally hidden as too operational/noisy for riders. Bus taps hide route info into the `Show route info` reopen control so the rider can return without overlapping panels.
- **Bus → schedule bridge** (2026-06-02): tapped bus sheets now include `View route schedule`, giving riders a non-chat path from a live bus to the route's upcoming scheduled departures.
- **Agent ETA rule**: for route+stop "next"/ETA questions, realtime wins. `get_realtime_predictions(stop_id, route_id?)` can filter live predictions to the requested route; static GTFS is only fallback for first/last/schedule/date questions or when realtime has no match/unavailable.
- **Stop overlay**: tapping a stop shows live ETAs when they exist; scheduled departures appear only as fallback when no live ETA exists. The selected stop gets a pulsing focus marker on the map, and the map camera offsets that marker above the bottom sheet so it stays visible. The schedule fallback rolls forward into tomorrow / next service day and labels that day explicitly.
- **Stop-ID search** (2026-05-01): input in the top map toolbar accepts a numeric stop ID; on submit pans the map, drops the selected-stop marker, and opens the same sheet as tapping a stop dot. Mobile inputs are kept at 16px to avoid iOS/Chrome auto-zoom after focus.
- **Geolocation flow** (2026-05-01): center-on-me FAB drops a pulsing "You are here" pin and opens a sheet listing the 5 nearest stops within 500m. If browser/device location fails, the sheet now explains likely causes, offers retry, and falls back to Stop ID search.
- **Differentiation lever**: the stop/bus sheets still carry "Ask the Assistant" / "Plan trip from here" deep-links, so the visual surface hands off into the AI surface with context preloaded.
- **Current UI note**: bus markers and route selector chips use a side-view bus silhouette with larger route numbers, including special sizing for 3-digit routes.

---

## Open work — explicitly NOT done

These are the live punch-list items. Each is a candidate task to delegate.

### Trip Planner trust bugs (in [TASKS.md](../../TASKS.md), captured 2026-04-23)
- ~~**Pathological arrive-by itineraries**~~ — **FIXED 2026-05-01.** Pathological-cap filter in `_dedup_and_rank` (>2× shortest viable rejected) + arrive-by now sorts latest-departure-first. Engine `_build_itinerary` KeyError also fixed in passing.
- **Card-label ↔ expanded mismatch** — collapsed card says "7 transfers, 52 min", expanded shows 1 transfer, 28 min. Audit backend → card-badge mapping in `public_html/trip_planner.js`.
- **Walk-cap not enforced on final leg** — already fixed for trip planner output (`_MAX_FINAL_WALK_MIN = 12`) but verify it applies to all paths including hub-relay fallback.

### Live Map polish (post-MVP)
- Dedicated top-level `Schedules` tab is **not** built yet. Current schedule UX lives inside Live Map as a drill-in. Product direction being discussed: `Chat | Plan a Trip | Live Map | Schedules`.
- Greyed-out chips for routes with no service today (use `engine.service_ids_for_date()`)
- Smoother marker tween between polls (currently snaps)
- Per-direction polyline coloring (inbound desaturated)
- Cluster overlapping stops at low zoom (~970 stops at zoom 11 = busy)
- Real-device QA pass (iOS Safari + Android Chrome)
- Optional map-legend / selected-route count polish after real-device review

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
