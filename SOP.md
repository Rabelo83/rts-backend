# RTS Backend SOP (Operations + Documentation)

## Purpose
This backend powers the RTS virtual agent and the web UI. It provides:
- Real-time ETAs (Bustime API)
- Deterministic schedule answers (local GTFS SQLite DB)
- A single-domain frontend + API on Render

## Architecture (high level)
- Flask app (app.py) serves:
  - Web UI at /
  - Static assets at /static
  - API routes under /api
- Deterministic schedule queries use a local GTFS SQLite DB:
  - Backend Basics/db/rts_gtfs.sqlite
- Real-time ETAs use the Bustime API via rts_api.py

## Key API Endpoints
- GET /api/health
  - Verifies backend status and GTFS DB availability
- POST /api/agent
  - Chat endpoint (questions about schedules and real-time ETAs)
- GET /api/routes
- GET /api/directions?route_id=##
- GET /api/stops?route_id=##&direction_id=INBOUND
- GET /api/predictions?stop_id=####
- POST /api/schedule/debug (optional, debug only)

## GTFS Schedule DB
The schedule engine reads from:
- Backend Basics/db/rts_gtfs.sqlite

Build command (Render):
```
pip install -r requirements.txt && python "Backend Basics/db/build_gtfs_db.py"
```

## Environment Variables (Render)
Required:
- BUS_API_KEY: Bustime API key
- RTPIDATAFEED: usually "bustime"

Optional:
- OPENAI_API_KEY: enable LLM rephrasing / intent (optional)
- OPENAI_MODEL: default gpt-4o-mini
- HUMANIZE_ENABLED: true/false (default true)
- HUMANIZE_MODEL: default gpt-4o-mini
- CHAT_LOG_ENABLED: true/false (default false)
- SCHEDULE_DEBUG: true/false (default false)

## Chat Logging (optional)
If CHAT_LOG_ENABLED=true:
- Logs are written to: data/chat_logs.sqlite
- Table: chat_logs(id, ts_utc, message, response)

## Schedule Debug Endpoint (optional)
Enable:
- Set SCHEDULE_DEBUG=true

Call:
```
POST /api/schedule/debug
Content-Type: application/json
{
  "question": "What is the first route 75 leaving Butler Plaza on Saturdays?",
  "route": "75",
  "kind": "first"
}
```

Response includes parsed values:
- route, stop_id, date_iso, date_compact, time, kind

## Deployment SOP (Render)
1) Push to main on GitHub.
2) Render auto-deploys (or click Deploy Latest Commit).
3) Verify build command:
   - pip install -r requirements.txt && python "Backend Basics/db/build_gtfs_db.py"
4) Verify health:
   - GET /api/health
   - backend_basics.available should be true
5) Smoke test:
   - Schedule: "First route 75 leaving Butler Plaza on Saturdays"
   - Realtime: "ETA Route 43 at stop 0001"

## Troubleshooting
If schedule answers fail:
- Check /api/health
  - backend_basics.available must be true
- If false:
  - Confirm build command uses local GTFS folder
  - Confirm Backend Basics/db/rts_gtfs.sqlite exists in Render

If chat returns wrong route/stop:
- Use /api/schedule/debug to see parsed route/stop/date
- Ensure route/stop combination exists in GTFS

If API crashes:
- Check Render logs for KeyError or missing DB

## Concurrency
Gunicorn sync workers handle one request per worker at a time.
To increase concurrency, update startCommand:
```
gunicorn server:app --workers 3 --timeout 60
```

## File Map (common)
- app.py: Flask app setup and route registration
- routes/agent_service.py: agent logic
- routes/schedule_service.py: deterministic GTFS schedule lookup
- routes/bustime.py: real-time endpoints
- public_html/: frontend UI
- Backend Basics/db/: GTFS DB, scripts, answering layer

---

## GTFS Seasonal Update Checklist

Run this every time RTS provides a new GTFS zip (Spring → Summer, Summer → Fall, etc.).

### 1. Inspect the new feed before building the DB

```bash
# From the extracted GTFS folder:
cat feed_info.txt          # confirm feed_start_date / feed_end_date
cat calendar.txt           # list all service_id names and their date windows
cat routes.txt | cut -d',' -f1,3   # list route_short_names
```

**What to look for:**
- Feed date window makes sense (no overlap with old feed, no gap)
- All expected routes are present (compare count vs previous feed)
- Note every `service_id` value — these are the names the code hardcodes

### 2. Check for service_id naming changes (most common breakage point)

Open `routes/schedule_service.py` and verify these two places match the new service_id values:

**`get_active_service_label()` (~line 227)**
This function pattern-matches service_id strings to return human labels.
Currently recognises: `Reduced_Service`, anything containing `"Reduced"`, `Weekday`, `Mon-Thur`, `Saturday`, `Sunday`.
If the new feed introduces a new name (e.g. `Reduced-Mo-Th`, `Summer-Weekday`), add it here.

**`get_route_first_last_by_service_type()` `_label` dict (~line 282)**
This maps raw service_id → display label used in agent responses.
Add any new service_id values that should appear as "Weekday", "Reduced Service", etc.

**`agent_tools.py` `get_service_differences()` (~line 1273)**
Maps user-facing strings ("reduced") → service_id for DB queries.
If reduced-service service_ids changed, update the mapping here too.

### 3. Check for dropped routes

```bash
# diff route lists between old and new feed:
cut -d',' -f1,3 Backend\ Basics/RTSGTFS_<OLD>/routes.txt | sort > /tmp/old_routes.txt
cut -d',' -f1,3 Backend\ Basics/RTSGTFS_<NEW>/routes.txt | sort > /tmp/new_routes.txt
diff /tmp/old_routes.txt /tmp/new_routes.txt
```

Dropped routes are expected (e.g. UF routes go away in summer). No code change needed — the engine handles missing routes gracefully. Just note them so you don't debug phantom "route not found" issues post-deploy.

### 4. Build and deploy

```bash
# Build the DB locally first:
python "Backend Basics/db/build_gtfs_db.py"

# Spot-check the result:
sqlite3 "Backend Basics/db/rts_gtfs.sqlite" "SELECT * FROM feed_info;"
sqlite3 "Backend Basics/db/rts_gtfs.sqlite" "SELECT COUNT(*) FROM trips;"

# Copy to data/ for the app:
cp "Backend Basics/db/rts_gtfs.sqlite" data/rts_gtfs.sqlite
```

Then push to GitHub → Render auto-deploys.

### 5. Post-deploy smoke tests

After deploy, hit these in order:

1. `GET /api/health` — `gtfs_engine.loaded` must be `true`
2. `GET /api/gtfs-info` — confirm stop/trip counts match what you saw locally
3. Ask the agent: *"What time does Route 1 start on weekdays?"* — should return a time, not an error
4. Ask the agent: *"Does Route 75 run on Sundays?"* — tests Sunday service label
5. If summer: ask about a route that was dropped (e.g. Route 55 in summer 2026) — agent should say it doesn't run, not crash

### History of service_id names by season

| Feed | Weekday ID | Reduced ID | Saturday | Sunday |
|---|---|---|---|---|
| Spring 2026 (V6) | `Weekday` | `Reduced_Service` | `Saturday` | `Sunday` |
| Summer 2026 (V1) | `Weekday` | `Reduced-Mo-Th`, `Reduced-Fr` | `Saturday` | `Sunday` |

---

## Live ETA + Schedule Gap-Fill (How It Works)

BusTime only returns predictions for buses arriving within ~45 minutes. At a multi-route stop,
this means some routes appear live while others (no bus in the next 45 min) are silently absent.

### The gap-fill pattern

Both the chat agent and the map stop sheet follow this logic:

1. **Fetch live predictions** from BusTime (`/api/predictions?stop_id=...`)
2. **Fetch scheduled departures** from GTFS (`/api/map/stop/<id>/schedule` or `get_schedule_all_routes`)
3. **Merge**: show live ETA for any route that has a prediction; show next scheduled time for any route that doesn't

The schedule lookup searches **up to 14 days forward** so routes with no service today (e.g. weekend-only routes on a weekday) still surface with their next departure and a day label ("Tomorrow", "Sat May 9", etc.).

### Where each piece lives

| Surface | Live ETAs | Gap-fill | Merge point |
|---|---|---|---|
| Chat agent | `_tool_get_realtime_predictions` in `routes/agent_tools.py` | `_gap_fill_with_schedule()` (same file) | Inside `_tool_get_realtime_predictions` |
| Map stop sheet | `/api/predictions` endpoint | `/api/map/stop/<id>/schedule` → `_find_next_stop_schedule()` in `routes/map_api.py` | `showStopSheet()` in `public_html/map.js` |

### What the agent receives

Gap-fill entries in the predictions list have:
- `"source": "scheduled"` (live entries have `"source": "live"`)
- `"scheduled_time"`: formatted time string (e.g. `"3:15 PM"`)
- `"scheduled_day"`: only present when the departure is NOT today (e.g. `"Tomorrow"`, `"Sat May 9"`)
- `"minutes": null` (no live countdown)

The agent prompt instructs Claude to always include the day label when present.

### What the map displays

- Live routes → "Live ETAs" section with countdown in minutes
- Gap-fill routes → "Also at this stop" section (when mixed with live) or "Next scheduled · [day]" (schedule-only)
- Both fetched in parallel (`Promise.allSettled`) so neither blocks the other

### Per-route forward look-ahead (important)

Both the agent and map walk **day by day** and accumulate results **per route**. They do NOT stop at the first day that has any trips — they keep going until a full day passes with no new routes added (up to 14 days).

This means:
- A route whose last bus passed at 6 PM will still appear with tomorrow's time and a day label ("Tomorrow" or "Sat May 9")
- Weekend-only routes surface on weekday stop sheets with their next Saturday/Sunday time
- The label appears inline per route: "Route 43 → To Santa Fe (Tomorrow)  6:01 AM"

**Common pitfall when debugging**: if you revert to stopping at the first non-empty day (the old `if rows: return ...` pattern), routes that finished service today will silently disappear from the stop sheet even though they still serve that stop.

### If something looks wrong

- Agent shows routes missing at a busy stop → check `_gap_fill_with_schedule()` in `agent_tools.py`
- Map shows only live ETAs with no scheduled fill → check `showStopSheet()` in `map.js` (look for `Promise.allSettled`)
- Gap-fill returns no results at all → check `get_schedule_all_routes()` in `schedule_service.py`; the stop_id must match `stop_id_padded` in GTFS (4-digit zero-padded)
- A route disappears from the stop sheet in the evening → the per-route look-ahead is broken; check termination logic in `_find_next_stop_schedule()` and `_gap_fill_with_schedule()`