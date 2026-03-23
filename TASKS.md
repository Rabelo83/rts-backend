# RTS Project Task Tracker

Last updated: 2026-03-20 (session 2)

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

### First LLM-Judged Baseline ✅ DONE (2026-03-19)
- [x] First full run with GPT-4o-mini judge: **17/36 PASS (47%)** — baseline established
- [x] Fixed M02/M06: route-context disambiguation — `stop_id` typo in prompt → agent now calls `get_schedule(route_id, stop_name)` directly instead of showing stop picker
- [x] Fixed judge prompt: no longer penalizes correct calendar dates or optional reduced service notes (was causing false FAILs on S06, GPT20)
- [x] Fixed all Windows cp1252 unicode errors in `run_and_judge.py` and `qa_report.py`
- [x] Rerun M02, M06, S06, GPT20 — **4/4 PASS** after strengthened disambiguation rule + corrected scenario expected_behaviors
- [x] Full rerun completed — **Claude Haiku: 21/36 = 58%** (new baseline, up from 47%)
- [x] Ollama comparison run — **qwen3:8b: 12/36 = 33%** — too slow, drops Spanish, hallucinates; not production-ready
- [x] `--ollama` flag added to `run_and_judge.py` — single command for free local test runs
- [x] Fixed `OPENAI_MODEL_V4` fallback in `agent_gpt_v3.py` to respect `OPENAI_MODEL` env var
- [x] Fixed `sys.path` for local Flask test client in `run_and_judge.py`
- [x] Real-time first rule added to system prompt — agent now tries `get_realtime_predictions` before `get_schedule` for route+stop queries

### Level 2 — Production Feedback Loop ✅ DONE (2026-03-19)
- [x] Real user queries logged to `data/analytics.sqlite`
- [x] Built `tests/replay_from_logs.py` — replays last N real conversations, scores PASS/FAIL
- [x] LLM judge (Level 1b) wired into replay scoring
- [x] `promote_to_scenario.py` closes the loop: replay FAIL → scenario suite
- Usage: run weekly or after any GTFS data refresh

### User Ratings (Thumbs Up/Down) ✅ DONE (2026-03-19)
- [x] Thumbs up/down appear below every real bot response in chat UI (not greetings/session messages)
- [x] `POST /api/feedback` stores rating (1/-1) + session_id + message_index + previews in `analytics.sqlite`
- [x] `feedback` table auto-created on first use — zero migration needed
- [x] Dashboard shows User Satisfaction card (7-day %) when feedback data exists
- [x] `/api/dashboard/metrics` includes `satisfaction_pct` field
- [x] "Useful?" label added before 👍/👎 buttons for clarity (chat_v2.js v8)
- [ ] `replay_from_logs.py` — add `--rated-fails-only` flag to prioritize negatively-rated sessions
- [ ] `promote_to_scenario.py` — add `--from-ratings` flag to pull thumbs-down sessions directly
- **Why:** Real user signal with zero manual triage — Level 0 QA feeding directly into the existing pipeline

### Remaining Claude Haiku Failures (15 scenarios, 2026-03-20 baseline)
Prioritized by fix effort vs impact:

**Quick wins (prompt rules):**
- [ ] S14 — greeting adds transit suggestions; add "Hi/Hello → warm greeting only, no transit list" rule
- [ ] S13 — trip planning implies it can help; strengthen OUT OF SCOPE rule
- [ ] S20 — service week dates wrong; investigate injected context format
- [ ] M05 — shows stop 473 instead of 0473; leading zero stripping rule

**Multi-turn context (medium):**
- [ ] M04 / GPT13 — Spanish follow-up drifts to English; add "stay in detected language" rule
- [ ] GPT12 — real-time "after that?" repeats same predictions; need re-call logic
- [ ] GPT15 / GPT17 — time-advance follow-ups ("an hour later?") not working
- [ ] GPT15 — location switch on route context; related to M06 CRITICAL rule

**Tool/data issues:**
- [ ] S07 / S08 / S11 — `search_routes` returns incomplete lists; investigate tool response
- [ ] S03 — "next departures for route 10" returns only one time instead of list
- [ ] S19 — route stop list truncated (28 stops, response cuts off)

**New feature (trip planning):** → See full plan below

---

## 🗺️ Trip Planner — Full Plan (2026-03-20)

**Decision:** Tab-based UI, mobile-first, results in panel (not chat), Nominatim geocoding (free, abstracted so Google can replace with env var), single-transfer routing for v1.

**Competitive advantages over Google Maps:**
1. Real-time BusTime predictions for first leg (not just static GTFS schedule)
2. Same-side-of-street transfer preference using directional stop names + lat/lon from `bus_stops.geojson`
3. Dynamic transfer window — adjusts if first bus is running late
4. Reduced Service awareness from live GTFS service type
5. 1,609 stops with exact coordinates, street+crossroad, shelter/amenity data

**Data assets:**
- `Backend Basics/bus_stops/bus_stops.geojson` — 1,609 stops, lat/lon, street, crossroad, directional names, amenities
- `gtfs.db` — trips, stop_times, routes, stops — for schedule-based routing
- BusTime API (`rts_api`) — real-time vehicle positions and arrival predictions

---

### Phase 1 — Geocoding + Stop Resolution (Day 1)

- [x] `tp-1` Load `bus_stops.geojson` into SQLite at startup → `stops_geo` table (superseded: stop_finder now queries rts_gtfs.sqlite directly) (stop_id, name, lat, lon, street, crossroad, direction, status)
- [ ] `tp-2` Build `utils/geocoding.py` — abstracted geocoder:
  - `GEOCODING_PROVIDER=nominatim` (default) | `google` | `mapbox`
  - `geocode(query, city="Gainesville, FL")` → `{lat, lon, formatted_address}`
  - Nominatim: `https://nominatim.openstreetmap.org/search` with Gainesville bounding box
  - 24h in-memory cache (same address → no repeat API call)
- [ ] `tp-3` Build `utils/stop_finder.py`:
  - `find_nearest_stops(lat, lon, radius_m=500, limit=5)` → stops ordered by walking distance
  - Uses Haversine formula on `stops_geo` table
  - Filters ACTIVE stops only
- [ ] `tp-4` `GET /api/geocode/autocomplete?q=...` — calls geocoder, returns suggestions
  - Frontend calls this; never exposes provider API key to browser
  - Rate-limited (10 req/min per IP)

### Phase 2 — Routing Engine (Day 2)

- [ ] `tp-5` Build `utils/trip_planner.py` — `find_trips(origin_lat, origin_lon, dest_lat, dest_lon, depart_after, date, service_id)`:

  **Direct routes:**
  ```sql
  SELECT r.route_short_name, st1.departure_time, st2.arrival_time, st1.stop_id, st2.stop_id
  FROM stop_times st1
  JOIN stop_times st2 ON st2.trip_id = st1.trip_id AND st2.stop_sequence > st1.stop_sequence
  JOIN trips t ON t.trip_id = st1.trip_id
  JOIN routes r ON r.route_id = t.route_id
  WHERE st1.stop_id IN (origin_stops) AND st2.stop_id IN (dest_stops)
    AND t.service_id = ? AND st1.departure_time >= ?
  ORDER BY st1.departure_time LIMIT 3
  ```

  **Single transfer:**
  - For each route from origin stops: find all intermediate stops
  - For each intermediate stop: find routes to destination stops
  - Calculate: walk_to_origin + ride1 + transfer_walk + wait + ride2 + walk_to_dest
  - Transfer walk time = Haversine(alighting_stop, boarding_stop) / 1.2 m/s

- [ ] `tp-6` Same-side transfer preference:
  - Parse directional prefix from stop name (Northbound/Southbound/Eastbound/Westbound)
  - If alighting stop and boarding stop at same crossroad AND same direction → `same_side: true`, walk_penalty = 0
  - If opposite direction at same crossroad → `cross_street: true`, walk_penalty = +60s (signal wait)
  - Prefer same-side transfers in ranking

- [ ] `tp-7` Real-time hybrid: for the first departure, call `rts_api.get_predictions(stop_id)` and prefer real-time ETA over static schedule. Flag itinerary as `realtime: true/false`.

- [ ] `tp-8` `POST /api/trip/plan` endpoint:
  - Input: `{origin_address, destination_address, depart_at}` OR `{origin_lat, origin_lon, dest_lat, dest_lon, depart_at}`
  - Returns: top 3 itineraries sorted by total_minutes
  - Each itinerary: `{legs: [{type, route, from_stop, to_stop, depart, arrive, walk_m, same_side}], total_minutes, realtime}`

### Phase 3 — UI (Day 3–4)

- [ ] `tp-9` Tab system in `chat.html` — "💬 Chat" / "🗺️ Trip Planner" tabs, full-width, thumb-friendly
- [ ] `tp-10` `public_html/trip_planner.js`:
  - Address inputs with autocomplete (calls `/api/geocode/autocomplete` on each keystroke, debounced 300ms)
  - Time selector: "Now" (default) or specific time picker
  - Submit → POST `/api/trip/plan` → render results
- [ ] `tp-11` Itinerary card component (mobile-first):
  ```
  ┌─ 18 min · Real-time ──────────────────┐
  │ 🚶 3 min walk → Rosa Parks (Stop 1)   │
  │ 🚌 Route 1 dep 8:42 AM               │
  │ 🔄 Transfer @ Downtown (same side ✓)  │
  │ 🚌 Route 5 dep 9:05 AM               │
  │ 🚶 2 min walk to destination          │
  └────────────────────────────────────── ┘
  ```
  - Same-side indicator (✓ no crossing / ⚠ cross street)
  - Shelter indicator if transfer stop has a covered shelter
  - Real-time badge on first leg when live data used
- [ ] `tp-12` Error states: no routes found, geocoding failed, service not running

### Phase 4 — Polish & QA (Day 5)
- [ ] `tp-13` Add 5 trip planner scenarios to `scenarios_v2.json` — test direct + transfer + no-route cases
- [ ] `tp-14` Mobile browser testing (Android Chrome, iOS Safari)
- [ ] `tp-15` Cache trip results 60s (same origin+dest+time = no recompute)
- [ ] `tp-16` Update README + deployment docs with `GEOCODING_PROVIDER` env var

**To switch from Nominatim to Google later:** set `GEOCODING_PROVIDER=google` + `GOOGLE_GEOCODING_KEY=...` in Render env vars. Zero code changes.

### Phase 5 — Trip Planner v1.5 (Smart Ranking + Time Modes + UX)
Priority upgrade before PWA. Targets the gap between "it works" and "it feels good."

#### 5a — Smart Route Ranking (composite score)
Current sort is by `total_min` only. Replace with weighted penalty score (lower = better):

| Factor | Penalty | Rationale |
|---|---|---|
| Walk distance | +1 min per 75m walked | People hate walking — this is the #1 complaint |
| Total ride time | +1 min per min | Core metric |
| Each transfer | +5 min flat | Transfers are stressful regardless of wait time |
| Same-side transfer | -2 min bonus | No street crossing = less friction |
| Real-time available | -1 min bonus | Prefer options with live data |

- [x] `tp-v1.5-1` Add `score` field to each itinerary in `find_trips()` using the formula above
- [x] `tp-v1.5-2` Sort by `score` instead of `total_min`; keep `total_min` for display only
- [x] `tp-v1.5-3` Deduplicate near-identical results — key by `(route1, transfer_stop_name, route2)`; keep lowest-score variant

#### 5b — Time Modes (Leave Now / Departing At / Arriving At)
- [x] `tp-v1.5-4` **UI: time mode selector** — 3-button toggle: "Leave Now" (default) / "Departing At" / "Arriving At"
- [x] `tp-v1.5-5` **"Departing At" UI** — date + time pickers side by side; pass `depart_after` + `date` to backend
- [x] `tp-v1.5-6` **"Arriving At" backend** — reverse routing through GTFS with `st2.arrival_time <= arrive_by`; `arrive_by` param added to `find_trips()`
- [x] `tp-v1.5-7` **"Arriving At" UI** — date + time pickers; pass `arrive_by` + `date` to backend; results show "arriving by" framing

#### 5c — Distance & Time Display
- [x] `tp-v1.5-8` **Walk distance in feet/miles** — < 500ft → feet, ≥ 500ft → miles ("0.3 mi")
- [x] `tp-v1.5-9` **ETA badge on first leg** — "in N min" when < 45 min away; live dot when realtime
- [x] `tp-v1.5-10` **Arrival time on each leg** — depart → arrive shown on every bus leg (12h AM/PM)

#### 5d — Other v1.5 improvements
- [x] `tp-v1.5-11` **Reduced Service notice** — amber banner when service_label != Weekday
- [ ] `tp-v1.5-12` **Sort toggle UI** — "Best Match" / "Least Walking" / "Fewest Transfers" buttons reorder results client-side without re-querying backend

#### 5e — Bug Fixes (2026-03-20)
- [x] `tp-fix-1` **Suburban address "No bus stops found"** — Increased `_MAX_WALK_M` from 500m to 1000m (~0.6 mi). (`utils/trip_planner.py`)
- [x] `tp-fix-2` **Render geocoding offset "No bus stops found"** — Added 5km fallback scan when 1000m returns zero stops. (`utils/trip_planner.py`)
- [x] `agent-fix-1` **Route operating hours unnecessary disambiguation** — Added `## ROUTE-LEVEL QUESTIONS` rule, calls `get_route_overview` immediately. (`routes/agent_claude.py`)
- [x] `tp-fix-3` **"Leave Now" searching 4 hours in future** — `_now_min()` used `datetime.now()` (UTC on Render). Fixed to `datetime.now(ZoneInfo("America/New_York"))`. Same fix applied to default date in `find_trips()`. (`utils/trip_planner.py`)
- [x] `tp-fix-4` **CRITICAL: stop_sequence TEXT comparison truncating all routes at stop 9** — `stop_sequence` is stored as TEXT in GTFS SQLite. Lexicographic comparison made `'29' > '3'` = FALSE, silently cutting off Rosa Parks (seq 29) and all downstream stops. Fixed all 5 SQL JOINs/WHERE clauses with `CAST(stop_sequence AS INTEGER)`. Cross-city trips (e.g. Archer Rd → NW 34th Blvd) now return results. (`utils/trip_planner.py`)
- [x] `tp-fix-5` **Only 1 trip option returned** — Dedup key `(r1, xfer, r2)` collapsed same route at different departure times into one result. Fixed: added 30-min departure bucket to key. Also: `_MAX_RESULTS` 3→5, `_SEARCH_WINDOW_MIN` 90→120 min, leg query limits increased. (`utils/trip_planner.py`)

#### 5i — Session 5 (2026-03-23) — Transfer Engine Overhaul
- [x] `tp-fix-20` **Wait limit 30→90 min** — RTS routes run 40–80 min headways; 30 min silently dropped most valid connections. Changed `_MAX_WAIT_MIN = 90`. (`utils/trip_planner.py`)
- [x] `tp-fix-21` **Transfer walk implemented** — `_MAX_TRANSFER_WALK_M = 300` was dead code. Transfer search now builds `boarding_map` / `feeder_map` of stops within 300m of every transfer point; handles directional stop pairs (NB/SB at same intersection). (`utils/trip_planner.py`)
- [x] `tp-fix-22` **Stop cache eliminates N×M DB connection storm** — `get_stop_by_id()` opened a new SQLite connection per call; transfer double-loop caused up to 1,500 connections per search. Added `_stop_cache` dict in `stop_finder.py`; O(1) after first call. `find_nearest_stops` warms cache for free. (`utils/stop_finder.py`)
- [x] `tp-fix-23` **Batched leg2 query** — was one SQL query per (trip × transfer stop). Now collects all boarding stop IDs and runs one query; results matched in Python. ~100× fewer SQL round-trips. (`utils/trip_planner.py`)
- [x] `tp-fix-24` **same_side_penalty_sec always returned 0** — called with same stop ID twice; now correctly passes alighting vs boarding stop so cross-street detection fires. (`utils/trip_planner.py`)
- [ ] `tp-debug-1` **"No routes found" for 34 SE 13th Rd → 7200 SW 8th Ave** — retest after deploy with above fixes.

#### 5h — Session 4 (2026-03-20)
- [x] `tp-ui-6` **Swap button (↕) between From/To fields** — swaps text values + geocoded `_acState` lat/lon. (`public_html/trip_planner.js`, `public_html/chat.html`)
- [x] `tp-fix-19` **Dominated itinerary shown (93 min when 23 min option exists)** — dedup key included transfer stop name, so same route combo via different transfer stops both surfaced. Changed key to `(r1, r2, dep_bucket)` — only best-scoring variant kept. (`utils/trip_planner.py`)
- [x] `dashboard-docs-1` **Dashboard Project Docs viewer** — `GET /api/project/log` + `GET /api/project/tasks-md` endpoints serve raw markdown; dashboard panel with tab switcher + `marked.js` rendering. (`routes/admin_api.py`, `public_html/dashboard.html`)
- [ ] `tp-debug-1` **"No routes found" for 34 SE 13th Rd → 7200 SW 8th Ave** — not yet diagnosed. Test Monday with "Departing At 2 PM" to rule out time-of-day. `_debug` field in response for diagnosis.

#### 5g — Bug Fixes (2026-03-20 session 3)
- [x] `tp-fix-10` **CRITICAL: "No routes found" on all non-Weekday service days** — `find_nearest_stops()` returned stops present in any `stop_times` row, ignoring service type. On Reduced_Service days, those stops had zero Reduced_Service trips, so every routing query returned empty. Fix: compute `service_ids` before the stop search; `find_nearest_stops()` now accepts `service_ids` and JOINs `trips` to filter stops by active service type. (`utils/stop_finder.py`, `utils/trip_planner.py`)
- [x] `tp-fix-11` **`_enrich_realtime` dead since day 1** — wrong keyword arg `prmstpid=` (positional-only); response dict iterated as keys instead of `.get("prd")`. (`utils/trip_planner.py`)
- [x] `tp-fix-12` **Connection leaks** — `conn.close()` missing from `try/finally` in `_service_ids_for_date` and `find_trips`. (`utils/trip_planner.py`)
- [x] `tp-fix-13` **Wrong service banner on future-date queries** — `get_active_service_label()` always used today, ignored `target_date`. (`utils/trip_planner.py`)
- [x] `tp-fix-14` **Same-route useless transfer (Route 37 → Butler Plaza → Route 37)** — added `if leg2["route"] == leg1["route"]: continue`. (`utils/trip_planner.py`)
- [x] `tp-fix-15` **Useless transfer when direct route already covers destination** — added `already_direct` SQL subquery in `_find_with_transfer`. (`utils/trip_planner.py`)
- [x] `tp-fix-16` **Earlier departure shown after later one** — `_dedup_and_rank` sorted by score only; now `(depart_min, score)`. (`utils/trip_planner.py`)
- [x] `agent-fix-2` **Agent ignored 5 tools (vehicle count, location, trip planning, route stops, service diff)** — SYSTEM_PROMPT listed only 5 tools; LLM didn't know the others existed. Rewrote as 10-tool markdown table. (`routes/agent_v2.py`, `routes/agent_tools.py`)
- [x] `tp-fix-17` **Itinerary cards always expanded** — duplicate CSS `.itin-legs { display: flex }` overrode `.itin-legs { display: none }`. Removed duplicate. (`public_html/chat.html`)
- [x] `tp-fix-18` **`ZoneInfo` fails on Linux without system tzdata** — added `tzdata` to `requirements.txt`. (`requirements.txt`)

#### 5f — UI Improvements (2026-03-20 session 2)
- [x] `tp-ui-1` **Feedback buttons hidden below viewport** — `scrollDown()` called before rating row appended. Fixed: second `scrollDown()` after `addRatingButtons()`. (`public_html/chat_v2.js`)
- [x] `tp-ui-2` **Itinerary timeline stepper redesign** — Replaced flat leg rows with 3-column timeline (time | track | content). Colored dots: blue=board, amber=exit, green=arrive. BOARD/EXIT AT/ARRIVE AT action tags. Solid route number pills. Transfer as amber inset box. (`public_html/chat.html`, `public_html/trip_planner.js`)
- [x] `tp-ui-3` **Cards bleeding together** — Added `#trip-results { display:flex; gap:14px }`. (`public_html/chat.html`)
- [x] `tp-ui-4` **Journey strip + time range in card header** — Added `🚶›[75]›⇄›[1]›🚶` sequence strip and full dep→arr time range to every card header for at-a-glance comparison. (`public_html/trip_planner.js`, `public_html/chat.html`)
- [x] `tp-ui-5` **Collapsible itinerary cards** — First card open, rest collapsed. Tap header to expand/collapse. Chevron rotates on open. (`public_html/chat.html`, `public_html/trip_planner.js`)

### Phase 6 — Agent Chat Tools (2026-03-20)
Two new tools wired into the Claude agent so the chat can answer location and trip questions directly.

#### 6a — Vehicle Location Tool
- [x] `agent-vl-1` Add `get_vehicle_location(route_id)` tool to `agent_tools.py` — calls `/api/vehicles` + `/api/predictions` for each vehicle; returns all active buses on the route with next stop name + minutes away
- [x] `agent-vl-2` Cap response at 4 vehicles sorted by next-stop ETA (soonest first); if 0 vehicles found return "no buses currently active on this route"
- [x] `agent-vl-3` Wire into `agent_claude.py` tool list + system prompt rule: *"For 'where is bus X' queries → call get_vehicle_location"*

**Response format:**
> Route 8 — 3 buses running:
> • Bus 1204 → to Butler Plaza · 2 min from Stop 0473 (NW 13th & University Ave)
> • Bus 1187 → to Butler Plaza · 11 min from Stop 0821 (SW Archer & SW 34th St)
> • Bus 1093 → to Downtown · 4 min from Stop 0156 (Main St & 2nd Ave)

#### 6b — Chat Trip Planning Tool
- [x] `agent-tp-1` Add `plan_trip(origin, destination)` tool to `agent_tools.py` — calls Google Geocoding API to resolve both addresses to lat/lon, then calls `find_trips()`; returns top 3 itineraries formatted for chat
- [x] `agent-tp-2` Agent asks clarifying question if origin/destination is ambiguous ("Where are you traveling from?")
- [x] `agent-tp-3` Wire into `agent_claude.py` tool list + system prompt rule: *"For trip planning queries → call plan_trip with origin and destination as the user described them"*

#### 6c — Vehicle Deployment Count Tool
- [x] `agent-vd-1` Add `get_route_vehicle_count(route_id, date?)` tool to `agent_tools.py` — queries GTFS trips + stop_times to calculate how many buses are simultaneously active at any point in the day; returns current count, peak count, and daily windows (e.g. "2 buses 7:30 AM – 11:25 AM")
- [x] `agent-vd-2` Wire into `agent_claude.py` + prompt rule: *"For 'how many buses on route X' or 'when will there be 2 buses' → call get_route_vehicle_count"*

**Background:** Exercise across Routes 5, 8, 15, 37, 43, 75 confirmed the GTFS schedule reliably shows vehicle deployment windows. Customers ask this frequently. Key findings: Route 37 peaks at 4 buses (weekdays), Route 75 at 3, all others at 2. Sat/Sun almost always 1 bus. Vehicle count ≠ frequency (opposite-direction buses count separately) but IS useful for ops awareness.

#### 6d — POI / Business Query Fix
- [x] `agent-poi-1` Add BUSINESS/POI QUERIES prompt rule to `agent_claude.py`: when user asks about a business by name, do NOT guess locations from training data — ask for the road/area + origin, then call `plan_trip` directly. Google Geocoding resolves "McDonald's Newberry Road" to real coordinates.

**Root cause:** Agent hallucinated 3 McDonald's locations from training knowledge, violating GROUND TRUTH RULE. Then failed on Newberry Rd follow-up. Fix: engage conversationally → get enough specificity → delegate to plan_trip + geocoding.

**Notes:**
- Uses same `GOOGLE_GEOCODING_KEY` already configured on Render
- Returns plain-text itinerary summary (not full card UI) — agent formats it conversationally
- If no routes found, agent says so and suggests checking the Trip Planner tab for more options

### Phase 7 — PWA & App Store
- [ ] `tp-v2-1` **PWA** — `manifest.json` + service worker + meta tags → "Add to Home Screen" on iOS/Android
- [ ] `tp-v2-2` **App Store** — wrap PWA with Capacitor for native iOS/Android packaging; submit to Apple App Store + Google Play Store

**Long-term — App Store:**
- Wrap PWA with Capacitor (preferred) or Expo Web for native iOS/Android packaging
- Submit to Apple App Store + Google Play Store
- PWA first → validate adoption → then native wrapper


---

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
