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