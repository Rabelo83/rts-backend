# Task: Build the rider-facing Schedules tab

> Read [prompts/context/project-brief.md](../context/project-brief.md) first, then [prompts/context/STATE-OF-PLAY.md](../context/STATE-OF-PLAY.md).

## Goal

Add a fourth top-level tab to the main app:

`Chat | Plan a Trip | Live Map | Schedules`

This tab should give riders a **non-chat**, **easy-to-scan**, **mobile-friendly** way to understand route schedules. It must be built from the GTFS-backed database already in the app. It must **not** be a literal RTS PDF clone.

The product intent is:
- cool enough to feel like a real app feature, not an admin table
- functional enough that a rider can answer “when does this bus run?” without using chat
- simple enough that someone who has never seen GTFS can still understand it

## Important context

We already have a first-step schedule drill-in inside Live Map:
- route summary drawer → `View full schedule`
- tapped bus sheet → `View route schedule`

That is **not** the final UX. Keep it working, but build the proper schedule experience as its own tab.

## Non-goals

- Do not replace or redesign the Chat, Plan a Trip, or Live Map tabs
- Do not ingest or render the RTS PDF files
- Do not generate schedule times from the model
- Do not preload all schedule data for all routes when the app loads
- Do not turn this into a generic admin table UI

## Product direction

The rider flow should be:

1. Open `Schedules`
2. Pick a route
3. Pick a service day:
   - `Weekday`
   - `Saturday`
   - `Sunday`
   - `Reduced` if available
4. Pick a direction
5. See a timetable that is actually readable

### Recommended presentation

For v1, prefer a **timetable grid** over simple departure chips:
- rows = trip runs / departure sets
- columns = rider-meaningful stops
- user can scan across a row and understand how the trip moves through the route

Recommended v1 scope:
- start with **key stops** per direction, not every stop on the route
- if the structure works well, add `Show full route timetable` later

## Scope — what you may touch

- `public_html/chat.html`
- `public_html/frontend.js`
- `public_html/style.css` or schedule styles in the main app surface if that is where the tab system already lives
- `routes/schedule_api.py` and/or a new schedule-tab-specific API surface if needed
- `routes/schedule_service.py`
- tests under `tests/`
- docs / handoff files only as needed

Try not to touch:
- `routes/agent_*.py`
- `routes/agent_tools.py`
- trip planner logic
- live map behavior except for preserving the current drill-in

## Backend requirements

Use the GTFS DB as the only source of truth.

You will likely need a backend shape closer to:

`route + service day + direction -> ordered stop columns + time rows`

The current route schedule helper is useful, but it is not enough for the target UX because it only groups origin departures by direction.

You may add a new API endpoint if needed, for example something like:

- `GET /api/schedule/route/<route_id>/table?...`

Possible response shape:

```json
{
  "route": "1",
  "route_name": "Downtown Station to Butler Plaza",
  "service_label": "Weekday",
  "direction": "To Butler Plaza",
  "stops": [
    {"stop_id": "0001", "stop_name": "Rosa Parks RTS Downtown Station", "is_key_stop": true},
    {"stop_id": "0473", "stop_name": "Reitz Union", "is_key_stop": true},
    {"stop_id": "0773", "stop_name": "Butler Plaza Transfer Station", "is_key_stop": true}
  ],
  "rows": [
    {"trip_id": "....", "times": ["6:30 AM", "6:40 AM", "7:00 AM"]},
    {"trip_id": "....", "times": ["7:06 AM", "7:16 AM", "7:36 AM"]}
  ]
}
```

If “key stops” are not already available in the data model, make a reasonable first-pass heuristic and document it clearly.

## Frontend requirements

The tab should feel intentional, not like a debug page.

Recommended UI:
- route card / picker at the top
- segmented day selector
- direction selector
- timetable card/grid below
- sticky headers where helpful
- mobile-friendly horizontal scroll if the grid is wider than the screen
- clean empty/loading/error states

Things to optimize for:
- fast comprehension
- good tap targets
- no clutter
- readable on phone first, desktop second

Avoid:
- giant unstyled HTML tables
- showing every possible control before a route is selected
- forcing the rider to read dense paragraphs

## Implementation suggestions

- Preserve the current top-tab architecture in `chat.html`
- Add a new panel for `Schedules`
- Lazy-load the data only when the tab is opened and the rider has selected route/day/direction
- Reuse schedule helpers where possible, but do not contort the UI around the existing map drill-in payload if a better backend shape is needed

## Success criteria

1. The app has a visible fourth tab: `Schedules`
2. Existing tabs still work
3. A rider can select route/day/direction and see a DB-backed timetable without using chat
4. The schedule view is understandable on mobile
5. No schedule times are invented by the model
6. Tests pass

## Verification

At minimum:
- `pytest` for the touched backend/frontend-related tests
- manual check that Route 1 or Route 10 renders a readable timetable
- verify the current Live Map schedule drill-in still works

## Report back with

- what backend shape you chose for the timetable
- what files changed
- screenshots or a concise description of the final UI
- tests run
- any follow-up limitations, especially around “key stops” vs “full route timetable”

## Suggested commit message format

```text
feat(schedule): add rider-facing schedules tab

- add fourth top-level Schedules tab to main app
- render GTFS-backed route timetable by service day and direction
- keep existing chat, trip planner, and live map behavior intact
- preserve live map schedule drill-in while adding the proper non-chat schedule surface

Co-Authored-By: <Model Name> <noreply@...>
```
