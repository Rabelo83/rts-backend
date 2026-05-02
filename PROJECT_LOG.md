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

### 2026-05-01 — Live Map final UX polish: multi-select routes + clearer markers
- Type: `feature, fix, docs`
- Summary: End-of-day Live Map polish focused on making the map behave more like a rider-facing tool and less like a debug overlay.
  - Route selector now supports **multi-select**: tapping route chips adds/removes routes; `All` clears the filter. Selected route count appears on the `Routes` toggle.
  - Vehicle polling was reduced from 10s/5s client/server cadence to 30s/30s to avoid exhausting the BusTime daily transaction allowance.
  - BusTime vehicle errors now surface as a non-blocking map status pill instead of silently looking like "no buses."
  - Added optional `BUS_API_KEYS` comma-separated fallback support for multiple authorized BusTime keys; the client retries the next key only when BusTime reports a daily transaction limit.
  - Fixed BusTime batched vehicle parsing: keep valid vehicles when BusTime also includes "No data found" for one route in the same response.
  - Added a single-route fallback when a BusTime key rejects batched multi-route vehicle calls with only "No data found."
  - Bus taps now remove the technical speed row and show upcoming stop ETAs for the selected bus when BusTime provides vehicle predictions.
  - Control layout is now mobile-first: `Routes: All` and Stop ID lookup share one toolbar row, and route chips live in an expandable tray below the Routes button instead of permanently crowding the map.
  - The route tray now collapses when the rider taps the map, while the Routes button remains the primary open/close control.
  - Tapping a bus now also selects that bus's route, updates the route toolbar/chips, and draws the route overlay before showing route info.
  - Location failure UX now gives actionable copy, a retry button, and a Stop ID fallback instead of echoing the browser's vague geolocation error.
  - Geolocation now retries automatically with a laptop-friendly lower-accuracy request when Chrome cannot provide a high-accuracy fix.
  - Route summary and bus/stop sheets now sit above live bus markers, preventing selected bus icons from covering schedule text.
  - Multiple selected routes render together with colored polylines and deduped stop dots. Route lines were softened and given a light casing so they read as context instead of cutting through bus markers.
  - Route chips no longer automatically open route info; route info remains available from bus taps / reopen, avoiding ambiguity when multiple routes are selected.
  - Bus markers and route chips use a side-view bus silhouette with larger route numbers and a 3-digit route mode.
  - Native stop `title` tooltips were replaced with custom hover/focus cards that show stop name, stop ID, and a "Tap for arrivals" cue.
  - Info surfaces remain mutually exclusive: route info and stop sheets do not stack on top of each other.
- Files/Areas: `public_html/map.js`, `public_html/chat.html`, `prompts/context/STATE-OF-PLAY.md`, `TASKS.md`, `PROJECT_LOG.md`
- Notes / Follow-up: Real-device QA remains the main open map task. Consider adding a selected-route legend only if multi-select feels unclear on mobile after testing.

---

### 2026-05-01 — Live Map: stop-ID search + "you are here" + nearby-stops sheet
- Type: `feature`
- Summary: Two UX gaps closed in the Live Map:
  1. **Geolocation** now drops a "You are here" pulsing pin (blue), centers the map, and opens a bottom sheet listing the 5 nearest stops within 500m. Tapping any nearby row pans to that stop and opens the existing predictions/schedule sheet. Previously the FAB just centered the map and did nothing visual.
  2. **Stop-ID search input** added above the route chip rail. User types a 3–4 digit stop ID (printed on physical bus stop signs), submits, and the map pans to the stop and opens the same predictions+schedule sheet. Mirrors the chat agent's "ETA if available, else next scheduled departure (today / tomorrow / next service day)" behavior — both surfaces now answer the same question the same way.
- Backend changes (`routes/map_api.py`):
  - **Extended** `/api/map/stop/<id>/schedule` to include `lat` and `lon` in the response. One round trip now powers the whole search flow — stop info AND scheduled departures in a single request. (Was tempted to add a separate `/api/map/stop/<id>` info endpoint and reverted that to avoid duplication; user pushed back on it correctly.)
  - **Added** `/api/map/nearby-stops?lat&lon&radius_m&limit` — surfaces `find_nearest_stops` from `utils/stop_finder` over HTTP (the agent doesn't take coords; this is a different use case). Returns stop_id/name/lat/lon/distance_m/walk_min/shelters.
- Frontend changes (`public_html/map.js`, `public_html/chat.html`):
  - New `#map-stop-search-form` row above the route chip rail.
  - New `.map-user-pin` blue pulsing marker, single-instance, updates on each FAB tap.
  - Geolocation flow: pan → place pin → fetch nearby → render scrollable list in the sheet → tap row to drill in.
  - Bumped `map.js?v=5` → `?v=6` and `SW_VERSION` v11 → v12.
- Files/Areas: `routes/map_api.py`, `public_html/map.js`, `public_html/chat.html`, `public_html/service-worker.js`
- Notes / Follow-up: Stop-search box is numeric-only by design (matches sign IDs). Future: support text/landmark search via the existing chat agent route. Real-device QA still pending.

---

### 2026-05-01 — Trip Planner: kill pathological arrive-by + UX sort + engine KeyError
- Type: `fix`
- Summary: Three coordinated fixes resolve the #1 trust-killer captured in the 2026-04-23 punch list. User-facing example was "Arrive by 2:29 PM" returning a 9:53 AM → 2:13 PM (4.5h) itinerary when a 12-min direct ride existed.
  1. **Pathological-cap filter** in `_dedup_and_rank` — drops itineraries whose `total_min` exceeds `max(2× shortest, shortest + 30min)`. Relative cap so it doesn't over-restrict genuinely long cross-city trips, but kills the 4.5h-when-15-min-exists case.
  2. **Sort arrive-by results latest-first.** Threading `mode` through `_dedup_and_rank` so arrive-by sorts by `-_first_dep` (latest acceptable departure first). Depart-after is unchanged. Result: "Arrive by 2:29 PM" now leads with the 2:00 PM bus, not the 9:30 AM bus.
  3. **Engine `_build_itinerary` KeyError fix** — pre-existing latent bug where walk-type transfer legs would index `full_legs[i-1]["arrive_min"]` and crash if `full_legs[i-1]` was another transfer. Walks back to find the most recent bus leg instead. This blocked the smoke test for the cap fix; affects both depart and arrive paths in the engine.
- Files/Areas: `utils/trip_planner.py` (filter + mode threading + sort), `utils/gtfs_engine.py` (KeyError fix in `_build_itinerary`)
- Verification: smoke-tested Rosa Parks → Reitz Union arrive-by 14:29 (now 5 sane options, latest 2:00 PM), depart-after 14:00 (1 result, 2:00 PM), Butler Plaza → Oaks Mall (33-min direct via Route 75). No pathological multi-hour itineraries surface in any test.
- Notes / Follow-up: Of the three trust bugs in the 2026-04-23 punch list, this closes #1. Card-label / expanded mismatch (#2) and walk-cap audit (#3) remain — both Codex-friendly tactical work.

---

### 2026-05-01 — Live Map info polish + handoff docs refresh
- Type: `feature, fix, docs`
- Summary: Refined the Live Map from "functional MVP" into a more self-explanatory rider surface.

  **Route-level improvements**
  - Added `GET /api/map/route/<route_id>/overview` so the map can show the same route summary value the chat agent already had: today's directions, first/last bus, frequency, and service hours by schedule type.
  - Added a top-of-map route summary drawer that opens when a route chip is selected or a bus marker is tapped.
  - Changed the drawer behavior from hard-close to **hide/reopen**, and added a bottom "Scroll for more" hint when the content overflows one screen.

  **Stop-level improvements**
  - Reworked stop schedules so the map no longer stops at "No more departures today." It now searches forward up to 14 days and returns the next real scheduled departure, even if that means tomorrow or the next active service day.
  - Added `service_day_label` ("Today", "Tomorrow", or a calendar label) to the stop-schedule payload so the UI can explain when that next service actually occurs.
  - Made the stop ID much more noticeable in the stop sheet with a dedicated badge (`Stop ID 0369` style).

  **Marker/UI improvements**
  - Replaced the plain circular vehicle markers with a more literal front-facing mini-bus treatment that still preserves route color, route number, and heading arrow.
  - Refreshed the Codex kickoff docs so future delegated sessions read not just the static context files, but also `PROJECT_LOG.md`, `TASKS.md`, and the real git branch state before assuming where the work stands.

- Files/Areas: `routes/map_api.py`, `public_html/map.js`, `public_html/chat.html`, `tests/test_map_api.py`, `prompts/codex-kickoff.md`, `prompts/context/STATE-OF-PLAY.md`, `TASKS.md`
- Notes / Follow-up: Still worth doing a real-device QA pass on iOS Safari and Android Chrome. Marker art may get one more visual pass if it still feels too badge-like in the browser.

---

### 2026-04-30 (session pt. 6) — Service worker rewrite: PWAs now get fresh HTML
- Type: `fix`
- Summary: User reported the Live Map tab missing from the **installed PWA** even after the live deployment showed it correctly in a fresh browser. Root cause: two compounding service-worker bugs.

  **Bug 1 — cache-first navigation strategy.** Once `/chat` HTML was cached (e.g. before the map tab existed), the SW served that cached version forever; it only refreshed when SW_VERSION bumped AND the install handler successfully replaced the entry. PWAs installed before 2026-04-30 were stuck with the 2-tab UI.

  **Bug 2 — install-time cache poisoning.** The SW's install handler fetched each SHELL_URL anonymously. With `DASHBOARD_PIN` set on Render, `/` and `/chat` returned 302 → /login. After `redirect: 'follow'`, the response was the login-page HTML — and the install handler dutifully `cache.put()`-ed that as if it were the app. So PWAs whose SW reinstalled while the user wasn't authenticated had `/chat` cached as the LOGIN PAGE.

  **Fix (SW v11):**
  - Removed `/`, `/chat` from `SHELL_URLS`. Static assets only (CSS/JS/icons/manifest).
  - Split fetch handler: HTML navigations now use **network-first**; static assets stay cache-first.
  - Navigation responses are cached **only** when `response.ok && !response.redirected` — anonymous redirects to /login no longer poison the cache.
  - Offline fallback path preserved (cache → cached `/` → cached `/chat` → offline page).

  Result: online users always see the latest HTML; offline users see whatever was last successfully cached during an authenticated session.

- Files/Areas: `public_html/service-worker.js` (rewrite of fetch handler + SHELL_URLS)
- Notes / Follow-up: Existing PWAs need to discard the v10 SW for v11 to take effect. iOS Safari sometimes holds the old SW for hours; instructions to user: hard-refresh (drag-down) or Settings → Safari → Advanced → Website Data → Remove rts-backend, then relaunch the PWA.

---

### 2026-04-30 (session pt. 5) — Map predictions fix + system-wide vehicle count tool
- Type: `fix, feature`
- Summary: Two issues surfaced after the map+IA ship:
  1. **Map stop bottom sheet always showed "No predictions right now"** — `map.js` was reading the raw BusTime field names (`prdctdn`, `rt`, `des`) but `/api/predictions` normalizes them to friendly names (`minutes`, `route`, `destination`). Fixed field mapping; added DUE handling and a delayed-flag warning glyph.
  2. **Chat agent had no system-wide vehicle count tool.** When asked "how many buses are running now?" it would offer to check individual routes. Added `get_active_vehicles_systemwide` tool that reuses the `/api/map/vehicles` aggregator (5s server cache shared with the live map → chat ↔ map can never disagree on counts). Registered in `agent_tools.TOOLS`, dispatch table, and the system-prompt routing table in `agent_claude.py`.

  Bumped `map.js?v=1` → `?v=2` and SW_VERSION `v9` → `v10` to force cache refresh.
- Files/Areas: `routes/agent_tools.py` (new tool), `routes/agent_claude.py` (routing table row), `public_html/map.js` (field mapping + DUE handling), `public_html/chat.html` (cache-buster), `public_html/service-worker.js` (SW v10).
- Notes / Follow-up: The agent now has 12 tools (was 11). Live verified locally that `/api/predictions?stop_id=1` returns `route/destination/minutes`-style payloads with real ETAs from BusTime.

---

### 2026-04-30 (session pt. 4) — IA reorganization SHIPPED
- Type: `feature, fix`
- Summary: User opened the production root URL mid-session and saw the legacy "RTS Bus Tracker" dropdown UI — confirming the IA problem in real time. Pulled the planned reorg forward and shipped it the same session. Changes:
  - `/` now serves the AI app (`chat.html` with Chat / Plan a Trip / Live Map tabs), gated by `DASHBOARD_PIN` like `/chat`.
  - `/chat` kept as alias (same handler) so existing bookmarks and the historical PWA `start_url` keep working.
  - Legacy `index.html` repurposed: now reachable at `/about` (public, not gated). Future white-label marketing landing slot.
  - `/wizard` retired — redirects to `/`.
  - PWA manifest `start_url: "/"` (was `/chat`).
  - Service worker `SW_VERSION` v8 → v9 with updated shell list (dropped wizard, added trip_planner.js + map.js).
  - `CLAUDE.md` updated with a complete URL map table for future contributors.

  Strategic framing: white-label resale needs ONE URL that IS the app per tenant. Splash pages and dropdown legacy fragment that contract.

- Files/Areas: `app.py` (route changes), `routes/pwa.py` (manifest start_url), `public_html/service-worker.js` (v9 + shell), `CLAUDE.md` (URL map), `TASKS.md` (IA section marked shipped).
- Notes / Follow-up: Real-device PWA install test still pending — verify "Add to Home Screen" lands on `/` and that previously-installed PWAs migrate cleanly when the new SW activates (iOS Safari is the high-risk path; commit `fe60194` previously fixed an iOS PWA launch bug here, so watch for regressions).

---

### 2026-04-30 (session pt. 3) — IA reorganization PLANNED, then shipped same session
- Type: `decision, docs`
- Summary: Captured the URL/IA reorganization plan in TASKS.md as a next-session pickup. Was implemented later the same session — see pt. 4 above. Original plan kept in TASKS.md for historical record. Current state has the legacy "RTS Bus Tracker" dropdown page at `/` and the actual product (Chat / Plan a Trip / Live Map) hidden at `/chat` — first-time visitors land on the legacy page and never discover the AI assistant. Decision: Option A — make the chat-app the root (`/` serves the 3-tab app directly), retire the legacy `index.html`, keep `/chat` as an alias for existing bookmarks/PWA shortcuts, retire `/wizard`. Rationale tied to white-label commercial framing: each agency should get ONE URL that IS the app. Implementation checklist + anchored file references documented in `TASKS.md > Information Architecture Reorganization` so any contributor can resume the work without context loss.
- Files/Areas: `TASKS.md` (new section), `PROJECT_LOG.md` (this entry). No code changes yet.
- Notes / Follow-up: Implement next session. Estimate: 30–60 min for the route swap + manifest/SW updates, plus one real-device PWA install test to confirm "Add to Home Screen" lands on the right URL. The PWA `start_url` and service-worker redirect logic are the most error-prone parts (commit `fe60194` previously fixed an iOS PWA launch bug here).

---

### 2026-04-30 (session pt. 2)
- Type: `feature`
- Summary: Live Map MVP scaffolded. New "Live Map" tab alongside Chat / Plan a Trip. Strategic intent: Go RTS / RideRTS ships a live bus map; replacement-grade requires we ship one too. Stack chosen for zero per-request marginal cost (white-label margin protection): **MapLibre GL JS + OpenFreeMap** vector tiles. No API keys, no per-tile billing, ships to production at any scale.

  Backend (`routes/map_api.py`):
  - `GET /api/map/routes` — 27 routes from GTFS with color + short/long names (process-lifetime cache)
  - `GET /api/map/route/<route_id>` — polyline shapes per direction + stops served (process-lifetime cache, since routes/shapes don't change between deploys)
  - `GET /api/map/vehicles` — server-side aggregation of all active vehicles across all routes (5s TTL cache, batched 10-route BusTime calls). Critical: amortizes BusTime load across concurrent map viewers, instead of every client polling per-route.

  Frontend (`public_html/map.js`, ~330 lines):
  - Lazy MapLibre init on first tab switch (no tile load for users who never open the map)
  - Horizontal route-chip rail at top, color-coded — tap to filter polyline + vehicles to one route
  - Bus markers with route-colored badge + heading arrow, animated each poll (10s)
  - Stop markers (route-stop layer when a route is selected)
  - "⊙" FAB to center on user via `navigator.geolocation`
  - Polling pauses on `visibilitychange` (battery + BusTime budget)

  Differentiation lever — bottom sheet on tap-bus / tap-stop carries:
  - **"Ask the Assistant"** button → switches to chat tab with a context-loaded question (e.g. "Where is bus 1204 on Route 5 right now…"), pre-filled in the input. Bridges visual ↔ AI surfaces without crowding either one.
  - "Plan trip from here" button on stop sheets → switches to Trip Planner tab with origin pre-filled.

  Smoke-tested: endpoints return 200, chat page includes all new references, `map.js` serves at 15 KB. Live BusTime polling not smoke-tested from CLI (avoided live API call); user to validate visually in browser.

- Files/Areas: `routes/map_api.py` (new), `app.py` (blueprint registration), `public_html/chat.html` (tab + CSS + script tags), `public_html/map.js` (new), `public_html/trip_planner.js` (switchTab updated for 3-tab)
- Notes / Follow-up: macOS port 5000 is hijacked by AirPlay Receiver — local dev needs `--port 5050` (or any non-5000). Polish items deferred: route polyline opacity per-direction, "no service today" greyed chips, smoother marker tween between polls. Live Map tab is functional but not polished — next session should drive a real-device test pass and address the three Trip Planner trust bugs in parallel.

---

### 2026-04-30
- Type: `fix, feature`
- Summary: Landmark `stop_id` anchoring shipped. Chat agent and Trip Planner now produce identical itineraries for the same landmark (e.g. "Rosa Parks"), eliminating the divergence flagged in the 2026-04-23 pickup notes. Root cause was the `landmarks.coordinates` table holding hand-typed lat/lon — Oaks Mall was ~3.4 km off, Rosa Parks ~810 m. Fix:
  1. Coordinates re-pulled from `rts_gtfs.sqlite` to match canonical GTFS stops.
  2. Added optional `stop_id` field to each landmark entry (architectural upgrade).
  3. `geocode()` now surfaces `stop_id` on a landmark hit.
  4. `find_trips()` accepts new `origin_stop_id` / `dest_stop_id` params — when supplied, pegs directly to that GTFS stop with a 0-min walk leg, bypassing nearest-stop spatial search entirely. This guarantees chat ↔ planner consistency regardless of geocoder drift.
  5. Threaded stop_id through `routes/trip_api.py` and the `plan_trip` chat tool.

  Smoke test (Rosa Parks → Oaks Mall): un-anchored version drifted origin to "Hampton Inn Hotel" (4-min walk); anchored version routes from stop 1 with 0-min walk and stop 1097 with 0-min walk-from on the best result. Same data path, two surfaces, one answer.

- Files/Areas: `agency_config.yaml`, `utils/geocoding.py`, `utils/trip_planner.py`, `routes/trip_api.py`, `routes/agent_tools.py`
- Notes / Follow-up: Pattern is generalizable — every entry in `landmarks.coordinates` can now opt in by adding `stop_id`. Next white-label agency just supplies their own list. Next session: trip planner trust bugs (pathological arrive-by, card-label mismatch, walk-cap audit), then live-map MVP.

---

### 2026-03-23 (session 6)
- Type: `decision, feature` (planned)
- Summary: Decided to replace the SQL-based trip planner with a full in-memory GTFS graph + RAPTOR algorithm. Root motivation: after 5 sessions of fixing individual SQL routing bugs, the architecture has hit its ceiling — SQL is the wrong tool for graph traversal, and every new edge case requires another SQL query pass. RAPTOR handles unlimited transfers in one pass, is used by Google Maps / OpenTripPlanner, and will benefit the chat agent (`plan_trip` tool) automatically with zero changes.

  **Architecture decision:**
  - `GTFSEngine` singleton: loads all GTFS at server startup (~8–10 MB RAM, ~2–3s load time)
  - Spatial grid index (0.005° cells) replaces SQL bounding-box stop search → O(1) lookup
  - Pre-computed transfer index: stop → nearby stops within 300m, built once at startup
  - `service_ids_for_date()` with `@lru_cache` — zero repeated DB hits per day
  - RAPTOR algorithm: multi-round, up to 4 transfers, journey reconstruction with walk legs
  - Public API unchanged — `find_trips()` signature stays the same

  **GTFS update workflow confirmed:** Replace `rts_gtfs.sqlite` → restart server → engine reloads automatically. `/api/gtfs-info` endpoint will show loaded_at, stop/trip counts, active service_ids.

  **Service awareness:** RAPTOR inherits all existing service-type logic — weekday/weekend/reduced/holiday/first-last bus all work through `service_ids_for_date()` exactly as before, just faster.

- Files/Areas: NEW `utils/gtfs_engine.py`, REWRITE `utils/trip_planner.py`, UPDATE `utils/stop_finder.py`, UPDATE `app.py`
- Notes / Follow-up: 6 tasks tracked as `raptor-1` through `raptor-6` in TASKS.md. Start with `raptor-1` (gtfs_engine.py), then rewrite trip_planner.py, then QA.

---

### 2026-03-23 (session 5)
- Type: `fix, perf`
- Summary: Major overhaul of the trip planner transfer search engine. Four compounding bugs were identified as the root cause of most "No routes found" failures.

  **Root causes fixed:**
  1. **Wait limit too tight (30 min)** — RTS runs routes on 40–80 min headways. Any connection requiring >30 min wait was silently discarded. Changed to `_MAX_WAIT_MIN = 90`.
  2. **Transfer walk never implemented** — `_MAX_TRANSFER_WALK_M = 300` was defined but dead code. Transfer search only matched leg1 exit and leg2 boarding at the exact same stop ID, missing all directional stop pairs (NB/SB stops at same intersection). Now builds a `boarding_map` / `feeder_map` of stops within 300m for every transfer point.
  3. **N×M SQLite connection storm** — `get_stop_by_id()` opened a new DB connection on every call. Inside the transfer double-loop (50 trips × 30 stops = up to 1,500 calls per search), this caused severe performance degradation and likely silent timeouts. Fixed with `_stop_cache: dict[int, dict]` in `stop_finder.py`; `find_nearest_stops` warms the cache for free. O(1) after first call.
  4. **Leg2 query inside double loop** — one SQL query per (trip × transfer stop). Now batched: one query for all boarding stops, matched in Python. ~100× fewer SQL round-trips.
  5. **same_side_penalty_sec always returned 0** — called with same stop ID twice; function short-circuits to 0 for identical IDs. Fixed: now passes alighting stop vs boarding stop so cross-street detection actually fires.

- Files/Areas: `utils/stop_finder.py`, `utils/trip_planner.py`
- Notes / Follow-up: Deploy and retest 34 SE 13th Rd → 7200 SW 8th Ave. `_debug` field still in no-routes response for diagnosis.

---

### 2026-03-20 (session 4)
- Type: `fix, feature`
- Summary: Swap button between trip planner From/To fields; dominated itinerary filter fix; dashboard Project Docs viewer.

  **Swap button (origin ↔ destination):**
  - Added `⇅` button between FROM and TO input fields in `buildFormHTML()`.
  - Added `swapFields()` function — swaps both text values AND the `_acState` geocoded lat/lon state so coordinates stay consistent after swap.
  - CSS added for `.trip-swap-row` / `.trip-swap-btn` in `chat.html`.

  **Dominated itinerary filter fix:**
  - **Bug:** Two itineraries with the same route sequence (e.g. 37→1) but different transfer stops (Center Dr vs Butler Plaza) had different dedup keys and both surfaced. Option 2 took 93 min vs option 1's 23 min — strictly dominated, but shown anyway.
  - **Root cause:** Dedup key was `(r1, xfer_stop, r2, dep_bucket)` — the transfer stop differentiated what should be identical choices.
  - **Fix:** Changed key to `(r1, r2, dep_bucket)` — same route combo in same 30-min departure window now collapses to best-scoring variant only.
  - `utils/trip_planner.py` → `_dedup_and_rank()`

  **Dashboard Project Docs viewer:**
  - Two new API endpoints: `GET /api/project/log` and `GET /api/project/tasks-md` serve raw markdown.
  - Project Docs panel added at bottom of dashboard with tab switcher (Project Log / Task Details).
  - `marked.js` CDN used for client-side markdown rendering.
  - `loadDoc(which)` JS function fetches from API and renders with `marked.parse()`.

  **Open issue — "No routes found" for some addresses:**
  - User tested 34 SE 13th Rd → 7200 SW 8th Ave and got "No routes found."
  - Not yet diagnosed — could be time-of-day (late evening, no buses), or a genuine routing gap for those addresses on Reduced_Service. `_debug` field still present in no-routes response for diagnosis. Investigate Monday with "Departing At 2 PM" to isolate.

- Files/Areas: `public_html/trip_planner.js`, `public_html/chat.html`, `utils/trip_planner.py`, `routes/admin_api.py`
- Notes / Follow-up: Remove `_debug` field before v1 release. Check "No routes found" on Monday with specific departure time test.

---

### 2026-03-20 (session 3)
- Type: `fix`
- Summary: Trip Planner — root cause of all "No routes found" failures on non-Weekday service days found and fixed. Multiple additional trip planner bug fixes from session 2 also documented.

  **Critical fix — service-unaware stop finder:**
  - **Root cause:** `find_nearest_stops()` returned any stop that appears in `stop_times`, regardless of service type. On Reduced_Service days (e.g. spring break), many stops are only served by Weekday trips. The routing SQL then filtered by `service_id IN ('Reduced_Service')` and found nothing — even though valid routes existed.
  - **Diagnosis method:** Added `_debug` field to no-routes response (target_time, service_ids, origin/dest stop IDs) — revealed `service_ids: ["Reduced_Service"]` and stops that had no Reduced_Service trips.
  - **Fix:** `service_ids` is now computed first in `find_trips()` (before the stop search). `find_nearest_stops()` accepts optional `service_ids` and filters the EXISTS clause to only return stops with active trips for today's service type. Both 1km and 5km fallback scans use the filter.
  - **Impact:** Fixes "No routes found" for all searches on Reduced_Service / Saturday / Sunday days — any day the active service_id differs from Weekday.

  **Supporting fixes (session 2 carry-over):**
  1. `_enrich_realtime` silently dead since day 1 — wrong keyword arg `prmstpid=` (should be positional `stop_id`); response dict iterated as keys instead of `.get("prd")`. Both fixed.
  2. Connection leaks — `conn.close()` not in `try/finally` in `_service_ids_for_date` and `find_trips`. Fixed.
  3. Wrong service banner — `get_active_service_label()` always used today; now passes `target_date`. Fixed.
  4. Useless same-route transfer (Route 37 → Butler Plaza → Route 37) — added `if leg2["route"] == leg1["route"]: continue`. Fixed.
  5. Useless transfer when direct route already covers destination — added `already_direct` SQL check in `_find_with_transfer`. Fixed.
  6. Earlier departure shown last — `_dedup_and_rank` sorted by score only; now sorts by `(depart_min, score)`. Fixed.
  7. `_find_direct LIMIT 10 → 30` — raised to prevent truncation on busy routes.
  8. Agent SYSTEM_PROMPT listed only 5 tools out of 10 — LLM never called vehicle count/location or trip planning tools. Rewrote prompt as full 10-tool markdown table. Fixed.
  9. Itinerary cards never collapsed — duplicate CSS `.itin-legs { display: flex }` at line 997 overrode `.itin-legs { display: none }` at line 843. Removed duplicate. Fixed.
  10. `tzdata` added to `requirements.txt` — required for `ZoneInfo("America/New_York")` on Linux containers without system timezone data.

- Files/Areas: `utils/trip_planner.py`, `utils/stop_finder.py`, `requirements.txt`, `routes/agent_v2.py`, `routes/agent_tools.py`, `public_html/chat.html`
- Notes / Follow-up: `_debug` field left in no-routes response for now — useful for ongoing diagnostics. Remove before v1 release.

### 2026-03-20 (session 2)
- Type: `fix, feature`
- Summary: Trip Planner — 5 critical bug fixes + 5 UI improvements deployed.

  **Critical fixes:**
  1. `stop_sequence TEXT comparison` — root cause of all cross-city routing failures. `stop_sequence` stored as TEXT in GTFS SQLite; `'29' > '3'` = FALSE lexicographically, silently truncating every route at stop 9. Rosa Parks (seq 29) was invisible to the router. Fixed: `CAST(stop_sequence AS INTEGER)` in all 5 SQL queries. Archer Rd → NW 34th Blvd now returns 3 options.
  2. `Leave Now timezone` — `_now_min()` used `datetime.now()` (UTC on Render), searching 4 hours in the future for EST users. Fixed: `datetime.now(ZoneInfo("America/New_York"))`.
  3. `Only 1 result returned` — dedup key `(r1, xfer, r2)` collapsed same route at different times into one. Fixed: added 30-min departure bucket. Also raised `_MAX_RESULTS` 3→5, `_SEARCH_WINDOW_MIN` 90→120 min.
  4. `Feedback buttons hidden` — `scrollDown()` fired before rating row was appended. Fixed: second scroll after `addRatingButtons()`.
  5. NW 34th Blvd confirmed working (stops 89m away, Routes 6+8 serving them) — "no options" was time-of-day (after 9 PM Reduced Service), not a bug.

  **UI improvements:**
  1. Vertical timeline stepper — 3-column layout (time | colored dots | content), BOARD/EXIT/ARRIVE AT action tags, solid route pills.
  2. Journey strip in card header — `🚶›[75]›⇄›[1]›🚶` + full dep→arr time range for at-a-glance comparison.
  3. Collapsible cards — first card open, rest collapsed; chevron toggle.
  4. Card gap fix — `#trip-results { gap:14px }`.

- Files/Areas: `utils/trip_planner.py`, `public_html/chat.html`, `public_html/trip_planner.js`, `public_html/chat_v2.js`
- Notes / Follow-up: Consider adding "service ends at X PM" to no-routes error message for better UX when user queries after last bus.

### 2026-03-20
- Type: `feature`
- Summary: Trip Planner v1.5 — smart ranking, time modes, date picker, architecture refactor.
  - Backend: composite score ranking (walk×2, +5min/transfer, same-side bonus); deduplication by route signature; Arrive By reverse routing; service_label in response for reduced service banner.
  - Frontend (v2→v3): Leave Now / Departing At / Arriving At toggle; date + time pickers side by side; ETA badge (<45min); walk distance in ft/mi; reduced service warning banner.
  - Architecture: `stop_finder.py` rewritten to query `rts_gtfs.sqlite` directly — eliminates `stops_geo.sqlite` and startup build step. Geojson enrichment (same-side/shelters) loaded in-memory if file present, gracefully skipped if not.
  - `bus_stops.geojson` removed from git — lives locally with GTFS source files, never deployed. Deploys are fast again.
  - Google Maps API key configured on Render (`GEOCODING_PROVIDER=google`, `GOOGLE_GEOCODING_KEY`), restricted to Render IP + Geocoding API only.
  - Starter question pills removed from chat UI.
  - Tab renamed "Plan a Trip".
- Files/Areas: `utils/trip_planner.py`, `utils/stop_finder.py`, `routes/trip_api.py`, `public_html/trip_planner.js`, `public_html/chat.html`, `public_html/chat_v2.js`
- Notes / Follow-up: Next GTFS refresh → add geojson enrichment to `build_gtfs_db.py` so street/crossroad/direction/shelters baked into rts_gtfs.sqlite (eliminates geojson dependency entirely).

### 2026-03-20
- Type: `feature` (planned)
- Summary: Three agent improvements planned after GTFS vehicle deployment analysis.
  1. `get_route_vehicle_count(route_id, date?)` — GTFS-based tool showing how many buses are simultaneously active by time window. Validated manually across Routes 5, 8, 15, 37, 43, 75. Route 37 peaks at 4 buses, Route 75 at 3, others at 2. Sat/Sun almost always 1. Customers ask this frequently.
  2. POI/Business query fix — agent was hallucinating business locations (e.g. listed fake McDonald's addresses from training data). Fix: new prompt rule directs agent to ask for road/area + origin, then call plan_trip. Google Geocoding resolves "McDonald's Newberry Road" to real coordinates — tested and confirmed working.
  3. plan_trip + get_vehicle_location already deployed (2026-03-20 earlier).
- Files/Areas: `routes/agent_tools.py`, `routes/agent_claude.py`
- Notes / Follow-up: Vehicle count ≠ frequency (opposite-direction buses counted separately). Agent should say "X buses deployed" not "frequency doubles."

### 2026-03-20
- Type: `feature` (planned)
- Summary: Two new agent chat tools approved for implementation.
  1. `get_vehicle_location(route_id)` — lists all active buses on a route with next stop name + ETA. Multiple vehicles shown sorted by soonest arrival; capped at 4. Uses existing `/api/vehicles` + `/api/predictions`.
  2. `plan_trip(origin, destination)` — natural language trip planning in chat. Geocodes both addresses via Google API (already live on Render), calls `find_trips()`, returns conversational itinerary summary.
- Files/Areas: `routes/agent_tools.py`, `routes/agent_claude.py`
- Notes / Follow-up: plan_trip uses same `GOOGLE_GEOCODING_KEY` env var already set. Agent will ask clarifying question if origin/destination is ambiguous.

### 2026-03-20
- Type: `fix`
- Summary: Trip Planner — "No bus stops found" for suburban addresses (SW 96th St area). Root cause: `_MAX_WALK_M` was 500m (~0.3 mi); Gainesville suburban stops (Route 75 corridor) can be 600–900m apart. Fix: raised radius to 1000m (~0.6 mi). Walk time still displayed correctly in itinerary card.
- Files/Areas: `utils/trip_planner.py` (`_MAX_WALK_M = 1000`)
- Notes / Follow-up: None — walk distance is shown to the user so they can judge acceptability themselves.

### 2026-03-20
- Type: `decision`
- Summary: Stop geo architecture — merge geojson data into rts_gtfs.sqlite at build time. Modify `build_gtfs_db.py` to read bus_stops.geojson alongside GTFS files, add street/crossroad/direction/shelters columns to stops table. One script, one database, one GTFS refresh step.
- Files/Areas: `Backend Basics/db/build_gtfs_db.py`, `utils/stop_finder.py`
- Notes / Follow-up: Do this at next GTFS data refresh.

### 2026-03-20
- Type: `decision`
- Summary: PWA first, then App Store. Plan a Trip tab is the foundation for the mobile app. PWA (manifest.json + service worker) enables "Add to Home Screen" on iOS/Android with zero store approval. Capacitor wrapper for App Store submission comes after PWA is validated.
- Files/Areas: `public_html/` (future manifest.json, sw.js)
- Notes / Follow-up: Tracked in TASKS.md Phase 6.

### 2026-03-19
- Type: `feature`
- Summary: User ratings (thumbs up/down) — full stack. Backend: `POST /api/feedback` stores rating (1/-1), session_id, message_index, user_message preview, answer_preview in `feedback` table (auto-created in `analytics.sqlite`). `satisfaction_pct` (7-day %) added to `/api/dashboard/metrics`. Frontend: 👍/👎 buttons rendered below every real bot response in chat UI; one-click, prevents double-vote, fails silently. Dashboard: User Satisfaction card shown when data exists. Committed and pushed (9aad939).
- Files/Areas: `routes/admin_api.py`, `public_html/chat_v2.js`, `public_html/chat.html`, `public_html/dashboard.html`
- Notes / Follow-up: Next: add `--rated-fails-only` flag to `replay_from_logs.py` and `--from-ratings` to `promote_to_scenario.py`.

### 2026-03-19
- Type: `decision`
- Summary: User ratings feature planned — thumbs up/down on each chat response stored in analytics.sqlite. Powers Level 0 QA: negatively-rated sessions auto-prioritized in replay_from_logs.py, can be promoted to scenarios via promote_to_scenario.py. Dashboard will show satisfaction % metric. Closes the full feedback loop with zero manual triage.
- Files/Areas: `public_html/chat` (UI), new `/api/feedback` endpoint, `analytics.sqlite`, `tests/replay_from_logs.py`, dashboard
- Notes / Follow-up: Build after targeted QA rerun confirms M02/M06 fix.

### 2026-03-20
- Type: `decision, planning`
- Summary: Trip Planner feature fully planned. Tab-based UI, mobile-first, results in panel. Nominatim geocoding (abstracted — swap to Google with one env var). Single-transfer routing v1. Key competitive advantages: real-time BusTime predictions for first leg; same-side-of-street transfer preference using directional stop names from bus_stops.geojson (1,609 stops with lat/lon, street, crossroad, amenities); dynamic transfer window. 16-task build plan (tp-1 through tp-16) added to TASKS.md. Estimated 4–5 days.
- Files/Areas: `TASKS.md`, `Backend Basics/bus_stops/bus_stops.geojson` (data asset confirmed)
- Notes / Follow-up: Start with tp-1 (load geojson to SQLite) + tp-2 (geocoding abstraction). Bus stop geojson is source of truth for coordinates — supersedes GTFS stops.txt for stop location data.

### 2026-03-20
- Type: `feature, fix`
- Summary: Phase 6 agent tools — all implemented and pushed to main.
  1. `get_vehicle_location(route_id)` — lists active buses on a route with next-stop name + ETA; capped at 4, sorted soonest first. Uses BusTime `getvehicles` + `getpredictions?vid=X` per vehicle.
  2. `plan_trip(origin, destination)` — natural language trip planning in chat. Geocodes both addresses via Google API, calls `find_trips()`, formats top itineraries conversationally. Agent asks for clarification if either address is ambiguous.
  3. `get_route_vehicle_count(route_id, date?)` — GTFS-based deployment windows: how many buses are simultaneously active at any time. Uses +1/-1 event model on trip start/end times; 5-min gap merging for turnaround smoothing. Returns current count, peak count, and named windows.
  4. BUSINESS/POI QUERIES rule — agent no longer guesses business locations from training data. Asks for road/area + origin, then calls `plan_trip` with Google Geocoding to resolve the real coordinates.
  5. `tp-fix-2` — Added 5km fallback stop scan in `trip_planner.py` for addresses where Google geocoding on Render places coordinates just outside the 1km radius (e.g. NW 34th St, SE 13th Rd).
  6. ROUTE-LEVEL QUESTIONS rule — "what time does bus X stop running?" calls `get_route_overview` immediately; no stop disambiguation asked.
- Files/Areas: `routes/agent_tools.py`, `routes/agent_claude.py`, `utils/trip_planner.py`
- Notes / Follow-up: All 6 items pushed to main (ac68334 + earlier commits). Verify on Render after deploy.

### 2026-03-20
- Type: `fix, feature`
- Summary: New Claude Haiku baseline 21/36 = 58% (up from 47%). Ollama comparison: qwen3:8b scored 12/36 = 33% — too slow (15–100s/query), drops Spanish, hallucinates GTFS data, invents tool parameters. Claude Haiku stays as default. Added `--ollama` flag to run_and_judge.py for free local dev runs. Fixed agent_gpt_v3 model env var fallback. Added REAL-TIME FIRST RULE to system prompt — agent now prefers get_realtime_predictions over get_schedule for route+stop queries. Added "Useful?" label before 👍/👎 rating buttons. Identified 15 remaining failures grouped by fix category.
- Files/Areas: `routes/agent_claude.py`, `tests/run_and_judge.py`, `routes/agent_gpt_v3.py`, `public_html/chat_v2.js`, `public_html/chat.html`
- Notes / Follow-up: Next: quick-win prompt fixes (S14 greeting, S13 trip planning, S20 dates, M04 Spanish). Then trip planner UI with geocoding tools.

### 2026-03-19
- Type: `fix`
- Summary: Targeted QA fixes — M02/M06/S06/GPT20 all now PASS (4/4). M02 expected_behavior corrected (Route 10 genuinely doesn't serve Butler Plaza — agent was correct). M06 disambiguation rule rewritten with CRITICAL/FORBIDDEN language; agent now calls get_schedule(route_id, stop_name) directly instead of search_stops. S06/GPT20 were judge false-FAILs: judge prompt hardened to trust service type from GTFS and not compare departure times against training knowledge; scenario expected_behaviors updated to accept Reduced Service and "Saturday, March 21" format. Added "Useful?" label before 👍/👎 rating buttons. Full suite rerun in progress.
- Files/Areas: `routes/agent_claude.py`, `tests/run_and_judge.py`, `tests/scenarios_v2.json`, `public_html/chat_v2.js`, `public_html/chat.html`
- Notes / Follow-up: New baseline expected ~55-65% (up from 47%).

### 2026-03-19
- Type: `fix`
- Summary: First LLM-judged QA baseline — 17/36 PASS (47%). Fixed M02/M06 route-context disambiguation: system prompt rule had `stop_id` typo instead of `stop_name`, causing agent to call `search_stops` and show a stop picker when route was already known. Fixed judge prompt to not penalize correct calendar dates or optional reduced service notes (S06/GPT20 were false FAILs). Fixed all Windows cp1252 unicode errors in `run_and_judge.py` and `qa_report.py`. Removed raw API links from dashboard Quick Links panel.
- Files/Areas: `routes/agent_claude.py`, `tests/run_and_judge.py`, `tests/qa_report.py`, `public_html/dashboard.html`
- Notes / Follow-up: Rerun targeted scenarios (M02, M06, S06, GPT20) after Render deploys to confirm fixes before full rerun.

### 2026-03-19
- Type: `feature`
- Summary: Added `get_service_differences` tool — answers "which buses are affected by Reduced Service?" by comparing Weekday vs target service_id in GTFS. Returns suspended_routes, extra_routes, running_routes. Routes 55, 76, 118 suspended on Reduced Service. All agent versions (v2, gpt_v3, claude) updated with system prompt rule to call this tool instead of guessing or refusing. Fix pushed and deployed.
- Files/Areas: `routes/agent_tools.py`, `routes/agent_claude.py`, `routes/agent_gpt_v3.py`
- Notes / Follow-up: Tool works for Saturday/Sunday service differences too.

### 2026-03-19
- Type: `feature`
- Summary: Built `tests/promote_to_scenario.py` — closes the production feedback loop. Reads FAIL entries from any judged_*.json or replay_*.json result file, deduplicates against existing suite, then walks user through writing expected_behavior + category + description and appends the new scenario to scenarios_v2.json. Interactive by default; --non-interactive auto-promotes FAILs that already have expected_behavior (for CI). Flags: --file, --all, --dry-run, --non-interactive.
- Files/Areas: `tests/promote_to_scenario.py`
- Notes / Follow-up: Completes the QA architecture: real FAIL → reviewed → permanent scenario. Run after any replay or judged run that reveals new bugs.

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
