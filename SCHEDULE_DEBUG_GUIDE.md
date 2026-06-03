# Schedule Timetable — Debug Guide & GTFS Update Checklist

This document captures every bug found and fixed in the timetable system,
the exact debugging steps used to find each one, and the checklist to follow
when updating the GTFS file.

---

## Architecture Quick Reference

```
Browser → GET /api/schedule/route/<id>/timetable?service=weekday&direction=...
             ↓
         schedule_api.py  (Flask route, thin wrapper)
             ↓
         schedule_service.get_route_timetable()
             ↓
         Backend Basics/db/rts_gtfs.sqlite   ← the DB used by schedule service
```

**Key file:** `routes/schedule_service.py` — `get_route_timetable()` starting around line 1288.

**Important:** There are TWO SQLite files in this project:
| File | Used by | In git? |
|---|---|---|
| `Backend Basics/db/rts_gtfs.sqlite` | `schedule_service.py` (timetable, chat agent) | YES |
| `data/rts_gtfs.sqlite` | `utils/gtfs_engine.py` (RAPTOR trip planner) | NO — upload manually to Render |

When you update GTFS data, you must update **both** files.

---

## How `get_route_timetable()` Works

1. **Find service IDs** — maps slug (`weekday`, `saturday`, etc.) to GTFS `service_id` values.
2. **Find available directions** — `SELECT DISTINCT trip_headsign, direction_id`.
3. **Pick a representative trip** — the trip with the most stops for that direction/service.
4. **Select 8 key stops** — `_select_key_stops()` picks evenly-spaced stops from the rep trip, always including first and last.
5. **Re-order by majority** — Option B: re-sorts the 8 key stops by their average `stop_sequence` across ALL trips in that direction (so column order matches what most trips actually do).
6. **Fetch all trips** — ordered by first departure time.
7. **Build time map** — for each trip, find the time at each key stop. For stops that appear multiple times (lollipop routes), picks the occurrence closest to the rep trip's sequence.
8. **Safety net** — nulls out any time that goes backwards relative to the previous column.

---

## Bug Log

### Bug 1 — `stop_sequence` TEXT sort breaks everything
**Deployed:** commit `7e9c663`

**Symptom:**
- Wrong stops in timetable columns — e.g. Route 1 showed Hilton Garden Inn as last column instead of Butler Plaza Transfer Station
- The terminal stop of a route was missing from the grid entirely
- Stops near sequence 9 (text-sort maximum) appeared as "last" column

**Root cause:**
GTFS SQLite stores `stop_sequence` as `TEXT`. All `ORDER BY stop_sequence`,
`MIN(stop_sequence)`, `MAX(stop_sequence)` calls sorted alphabetically:

```
"1", "10", "11", "12" ... "19", "2", "20" ... "28", "3" ... "9"
```

For a 28-stop route:
- Alphabetic "last" = `"9"` → Tigert Hall (seq 9) becomes "last" stop
- `_select_key_stops` always includes first and last → Butler Plaza TS (seq 28) dropped
- Butler Plaza TS lands at text-position 20 out of 28 and is skipped by evenly-spaced algorithm

**How we found it:**
1. User reported Route 1 showing Hilton Garden Inn as last column instead of Butler Plaza TS
2. Ran `get_route_timetable('1', 'weekday', 'To Butler Plaza')` locally — reproduced the issue
3. Added debug script to print rep trip + selected indices:
```python
rep_stops = conn.execute(
    "SELECT ... ORDER BY st.stop_sequence", (rep_id,)
).fetchall()
# Printed the raw order — saw seq "1","10","11"... proving text sort
```
4. `_select_key_stops` with n=28 picks indices `{0, 4, 8, 12, 15, 19, 23, 27}`.
   With text sort, index 27 = seq `"9"` (Tigert Hall), not seq `"28"` (Butler Plaza TS).

**Fix:**
Add `CAST(stop_sequence AS INTEGER)` to every ORDER BY, MIN, and MAX on
`stop_sequence` in `schedule_service.py`:

```sql
-- Before (wrong):
ORDER BY st.stop_sequence
MIN(stop_sequence)
MAX(stop_sequence)

-- After (correct):
ORDER BY CAST(st.stop_sequence AS INTEGER)
MIN(CAST(stop_sequence AS INTEGER))
MAX(CAST(stop_sequence AS INTEGER))
```

Lines fixed: 1446, 1491, 1519, 1663, and the `trip_bounds` CTE near line 611.

**Verification:**
```python
from routes.schedule_service import get_route_timetable
r = get_route_timetable('1', 'weekday', 'To Butler Plaza')
# Last stop must be Butler Plaza Transfer Station (1493)
assert r['stops'][-1]['stop_id'] == '1493'
# First row times must be ascending
times = [t for t in r['rows'][0]['times'] if t]
assert times == sorted(times)
```

---

### Bug 2 — `TypeError: unsupported operand type(s) for -: 'str' and 'str'`
**Deployed:** commit `955035c`

**Symptom:**
All schedule pages showed "Could not load schedule. Please try again." (red error message).
Every route broken simultaneously.

**Root cause:**
The lollipop-route fix (commit `d434c98`) stored `stop_sequence` values from
DB rows directly as dict values without casting:
```python
key_stop_target_seqs = {r["stop_id_padded"]: r["stop_sequence"] for r in selected}
```
Then later tried to compute:
```python
diff = abs(seq - target)  # both strings → TypeError
```

**How we found it:**
```bash
python -c "
from routes.schedule_service import get_route_timetable
get_route_timetable('5', 'weekday', None)
"
# Traceback: TypeError at line 1500: abs(seq - target)
```

**Fix:**
```python
# Cast at collection time:
key_stop_target_seqs = {r["stop_id_padded"]: int(r["stop_sequence"]) for r in selected}
# Cast at use time:
seq = int(tr["stop_sequence"])
```

---

### Bug 3 — Backwards times in timetable rows
**Deployed:** commit `81e8d7c`

**Symptom:**
Route 3 row showed: `6:00, 6:10, 6:13, 6:15, 6:21, 6:26, 6:04, 6:07`
Last two columns (Williams Elementary, Lincoln Estate) had times earlier than middle columns.

**Root cause (two layers):**

**Layer A — Wrong column ordering (Option B fix):**
The representative trip (most stops) determined column order. Some routes have
variant trip patterns where a minority of trips visit stops in a different order
than the majority. Column order matched the rep trip but not most trips.

For Route 3: the rep trip visited Williams/Lincoln late (seq ~45), but many trips
visit them early. Columns were ordered with Williams/Lincoln near the end, but
times for those trips showed early values there.

**Layer B — No safety net for residual mismatches:**
Even after majority-ordering columns, a minority-pattern trip still shows
backwards times for the columns where its order disagrees with the majority.

**How we found it:**
User provided the raw times from the UI. Noted that Williams Elementary (6:04)
and Lincoln Estate (6:07) were both earlier than Brakes 4 Less (6:26) to their
left, despite appearing in later columns.

**Fix:**
1. **Option B — Majority column ordering:** After `_select_key_stops` picks the
   8 stops, re-sort them by `AVG(stop_sequence)` across all trips in that direction.
   Columns now appear in the order that most trips follow.

2. **Option A — Null out backwards times:** While building each row, walk
   left-to-right and null any time that is less than the previous valid time:
```python
last_secs = -1
for sid in key_stop_ids:
    raw = trip_times.get(sid)
    secs = _gtfs_secs(raw)
    if secs is not None and secs >= last_secs:
        times.append(format_time_12h(raw))
        last_secs = secs
    else:
        times.append(None)  # hide backwards time
```

---

### Bug 4 — Dropdown never closed on Schedules tab
**Deployed:** commit `6bee5b7`

**Symptom:** Clicking a route chip opened the dropdown but it never closed.

**Root cause:** `hidden` attribute was overridden by `display: flex` in CSS.
The dropdown used `element.hidden = true` but the CSS rule `.sched-route-dropdown { display: flex }`
made the `hidden` attribute ineffective.

**Fix:** Changed dropdown open/close to use a CSS class `.open` instead of the
`hidden` attribute. CSS gates `display` on `.open`:
```css
.sched-route-dropdown { display: none; }
.sched-route-dropdown.open { display: flex; }
```

---

## Diagnostic Scripts

Run these whenever you suspect a timetable issue. All run locally against
`Backend Basics/db/rts_gtfs.sqlite`.

### Check what stops a route's timetable returns
```python
import sys; sys.path.insert(0, '.')
from routes.schedule_service import get_route_timetable

r = get_route_timetable('1', 'weekday', 'To Butler Plaza')
print('Stops:', [(s['stop_id'], s['stop_name']) for s in r['stops']])
for row in r['rows'][:3]:
    print('Row:', row['times'])
```

### Check what the rep trip looks like (raw DB query)
```python
import sqlite3
conn = sqlite3.connect('Backend Basics/db/rts_gtfs.sqlite')
conn.row_factory = sqlite3.Row

rep = conn.execute('''
    SELECT t.trip_id, COUNT(st.stop_id) AS sc
    FROM trips t
    JOIN routes r ON r.route_id = t.route_id
    JOIN stop_times st ON st.trip_id = t.trip_id
    WHERE r.route_short_name = '1'
      AND t.trip_headsign = 'To Butler Plaza'
      AND t.service_id = 'Weekday'
    GROUP BY t.trip_id ORDER BY sc DESC LIMIT 1
''').fetchone()
print('Rep trip:', rep['trip_id'], '| stops:', rep['sc'])

stops = conn.execute('''
    SELECT st.stop_sequence, s.stop_id_padded, s.stop_name
    FROM stop_times st JOIN stops s ON s.stop_id = st.stop_id
    WHERE st.trip_id = ?
    ORDER BY CAST(st.stop_sequence AS INTEGER)
''', (rep['trip_id'],)).fetchall()
for s in stops:
    print(f"  seq={s['stop_sequence']:>3}  {s['stop_id_padded']}  {s['stop_name']}")
```

### Check trip count and stop count distribution
```python
import sqlite3
from collections import Counter

conn = sqlite3.connect('Backend Basics/db/rts_gtfs.sqlite')
conn.row_factory = sqlite3.Row

rows = conn.execute('''
    SELECT t.trip_headsign, COUNT(st.stop_id) AS sc
    FROM trips t
    JOIN routes r ON r.route_id = t.route_id
    JOIN stop_times st ON st.trip_id = t.trip_id
    WHERE r.route_short_name = '1'
      AND t.service_id = 'Weekday'
    GROUP BY t.trip_id
''').fetchall()

by_dir = {}
for r in rows:
    by_dir.setdefault(r['trip_headsign'], []).append(r['sc'])

for headsign, counts in by_dir.items():
    print(f'{headsign}: {len(counts)} trips')
    print(f'  stop counts: {sorted(Counter(counts).items())}')
    print(f'  min={min(counts)} max={max(counts)} median={sorted(counts)[len(counts)//2]}')
```

### Check raw stop_sequence type in DB
```python
import sqlite3
conn = sqlite3.connect('Backend Basics/db/rts_gtfs.sqlite')
row = conn.execute(
    "SELECT stop_sequence, typeof(stop_sequence) FROM stop_times LIMIT 1"
).fetchone()
print('stop_sequence value:', row[0], '| type:', row[1])
# If type is "text" — always use CAST(stop_sequence AS INTEGER) in queries
```

---

## GTFS File Update Checklist

When RTS provides a new GTFS export, follow this checklist:

### 1. Prepare the new GTFS SQLite

The app expects a SQLite file, not raw GTFS CSV files. Convert using the
existing import pipeline (or ask how to run it if unsure).

The file needs these tables: `agency`, `calendar`, `calendar_dates`,
`fare_attributes`, `fare_rules`, `feed_info`, `routes`, `shapes`, `stops`,
`stop_times`, `trips`, `bus_stops`, `fuzzy_lookup`.

It also needs a `stop_id_padded` column on the `stops` table (zero-padded
4-digit string). If missing, add it:
```sql
ALTER TABLE stops ADD COLUMN stop_id_padded TEXT;
UPDATE stops SET stop_id_padded =
  CASE WHEN LENGTH(stop_id) < 4
       THEN PRINTF('%04d', CAST(stop_id AS INTEGER))
       ELSE stop_id
  END;
```

### 2. Verify `stop_sequence` type
```sql
SELECT stop_sequence, typeof(stop_sequence) FROM stop_times LIMIT 1;
```
If the result is `text` — that is expected and fine. The code already uses
`CAST(stop_sequence AS INTEGER)` everywhere. If somehow it becomes `integer`
in a future GTFS version, the CASTs are harmless.

### 3. Replace the local schedule DB
```
Backend Basics/db/rts_gtfs.sqlite   ← replace this with the new file
```
Commit it to git. This deploys automatically to Render.

### 4. Replace the RAPTOR engine DB
```
data/rts_gtfs.sqlite   ← NOT in git, upload manually to Render
```
In Render dashboard → your service → **Disks** or **Shell** tab →
upload/replace `data/rts_gtfs.sqlite`. Then redeploy (or it picks up on
next deploy).

### 5. Smoke test after deploy (~3 min after push)

Hit these endpoints to verify:
```
GET /api/gtfs-info                         → check stops/trips count looks right
GET /api/health                            → 200 OK
GET /api/schedule/route/1/timetable?service=weekday&direction=To Butler Plaza
    → last stop should be Butler Plaza Transfer Station (1493)
GET /api/schedule/route/1/timetable?service=weekday&direction=To Downtown Station
    → first stop should be Butler Plaza Transfer Station (1493)
GET /api/schedule/route/5/timetable?service=weekday
    → times in first row should be ascending
```

### 6. Verify no backwards times
Run locally before deploying:
```python
import sys; sys.path.insert(0, '.')
from routes.schedule_service import get_route_timetable

routes_to_check = ['1', '2', '3', '5', '8', '9', '10', '20', '75']
for route in routes_to_check:
    r = get_route_timetable(route, 'weekday', None)
    if not r or not r.get('rows'):
        print(f'Route {route}: NO DATA')
        continue
    # Check first 5 rows for backwards times
    bad = 0
    for row in r['rows'][:5]:
        times = [t for t in row['times'] if t is not None]
        # convert to comparable strings (HH:MM format sorts correctly within AM/PM)
        # just check None ratio as a proxy
        null_ratio = row['times'].count(None) / len(row['times'])
        if null_ratio > 0.3:
            bad += 1
    status = 'WARN (many nulls)' if bad else 'OK'
    print(f'Route {route} [{r["direction"]}]: {status} | stops: {[s["stop_id"] for s in r["stops"]]}')
```

### 7. Check terminal stops match headsigns
For each route, verify that:
- The **last column** of the outbound direction matches the destination in the headsign
- The **first column** of the inbound direction is that same terminal stop

If not, it usually means the TEXT-sort bug has reappeared (maybe a new GTFS
file stores `stop_sequence` differently) — re-check step 2.

### 8. Check for new service types
If the new GTFS has new `service_id` values (e.g. a "Holiday" service):
- Check `_SLUG_TO_SERVICE_IDS` in `schedule_service.py` — add the new slug if needed
- Check `SVC_LABELS` in `schedules.js` — add a display label

### 9. Check stop_id_padded consistency
The schedule service and the frontend use padded 4-digit stop IDs (e.g. `0001`
not `1`). If the new GTFS uses different stop IDs, the `stop_id_padded` column
values must still be consistent with what the frontend and agent tools reference.

---

## Key Invariants to Preserve

These are things the code assumes — if they break, timetables will silently
produce wrong results:

| Invariant | Why it matters |
|---|---|
| `ORDER BY CAST(stop_sequence AS INTEGER)` everywhere | stop_sequence is TEXT; alphabetic sort is wrong for 10+ stops |
| `_select_key_stops` always includes index 0 and index n-1 | Guarantees first and last stop of route appear in timetable |
| `key_stop_target_seqs` values are `int` | Used in `abs(seq - target)` arithmetic |
| `_gtfs_secs()` used for backwards-time detection | Handles GTFS times > 24:00 (overnight trips) |
| Both SQLite files updated on GTFS refresh | Schedule service and RAPTOR engine use separate files |
