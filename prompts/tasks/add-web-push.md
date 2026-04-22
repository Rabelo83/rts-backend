# Task: Add web push notifications + favorite-route alerts

> Read [prompts/context/project-brief.md](../context/project-brief.md) first. Depends on [add-pwa-primitives.md](./add-pwa-primitives.md) being complete (service worker must exist).

## Goal

Turn the PWA into the **commercial wedge**. A rider saves "Route 20 from Stop 0173 at 7:30 AM weekdays" as a favorite. Before 7:30 AM each weekday, a background worker checks real-time predictions. If the bus is delayed past threshold, the rider's phone buzzes: "Your Route 20 is 6 min late at Stop 173 — leave by 7:36 AM." This is the one feature Go RTS and RideRTS do not have, and it is the reason someone installs this app and never opens theirs again.

## Scope — what you may touch

- Python: new `routes/push.py`, new `routes/favorites.py`, new `utils/push_sender.py`, new `utils/alert_scheduler.py`, new DB schema file, minimal changes to `app.py` to register blueprints and start the scheduler
- `requirements.txt` to add `pywebpush` and `APScheduler`
- Frontend: `public_html/frontend.js` (subscribe flow, favorites UI), `public_html/service-worker.js` (push + notificationclick handlers — the TODO left by the PWA task)
- Tests under `tests/`

Do **not** touch: agent code (`routes/agent_*.py`), agent tools, GTFS ingestion, the existing admin dashboard beyond a simple "Active subscriptions count" readout.

## Deliverables

### 1. VAPID keys

- Generate a VAPID key pair once. Commit a helper `scripts/generate_vapid_keys.py` that prints keys to stdout (do not commit the keys themselves).
- Read keys from env vars: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (mailto or URL, read from `agency_config.yaml` contact email; fallback env var).
- Document required env vars in `.env.local.example`.

### 2. DB schema

Add tables to the app's session/favorites SQLite (NOT the GTFS DB):

```sql
CREATE TABLE user_identities (
  anon_uuid TEXT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  language TEXT
);

CREATE TABLE push_subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  anon_uuid TEXT NOT NULL REFERENCES user_identities(anon_uuid),
  endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  user_agent TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE favorites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  anon_uuid TEXT NOT NULL REFERENCES user_identities(anon_uuid),
  route_id TEXT NOT NULL,
  stop_id TEXT NOT NULL,
  departure_hhmm TEXT NOT NULL,                -- "07:30"
  days_of_week TEXT NOT NULL,                  -- "mon,tue,wed,thu,fri"
  delay_threshold_min INTEGER NOT NULL DEFAULT 3,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE alert_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  favorite_id INTEGER NOT NULL REFERENCES favorites(id),
  fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  delay_min INTEGER,
  outcome TEXT                                  -- "sent", "failed", "no_subscription", "deduped"
);

CREATE INDEX idx_fav_active ON favorites(active, days_of_week, departure_hhmm);
CREATE INDEX idx_alert_dedupe ON alert_log(favorite_id, fired_at);
```

Place the schema in `db/push_schema.sql`. Add a migration runner helper.

### 3. Endpoints

In `routes/push.py`:
- `GET /api/push/vapid-public-key` → `{ "key": "<url-safe-b64>" }`
- `POST /api/push/subscribe` — body: `{ anon_uuid, subscription: { endpoint, keys: { p256dh, auth } }, user_agent }`. Upsert by endpoint. Returns 204.
- `DELETE /api/push/unsubscribe` — body: `{ endpoint }`. Returns 204.

In `routes/favorites.py`:
- `POST /api/favorites` — body: `{ anon_uuid, route_id, stop_id, departure_hhmm, days_of_week, delay_threshold_min? }`. Returns the created favorite.
- `GET /api/favorites?anon_uuid=...` → list of favorites.
- `PATCH /api/favorites/<id>` — partial update (active flag, threshold, etc.).
- `DELETE /api/favorites/<id>`.

All endpoints: validate `anon_uuid` is a well-formed UUIDv4. Rate-limit subscribe to 10/min per IP using Flask-Limiter (already in requirements).

### 4. Background scheduler

In `utils/alert_scheduler.py`:

- APScheduler BackgroundScheduler starts in `app.py` guarded by `if os.getenv("ENABLE_ALERT_SCHEDULER", "true").lower() == "true":` so tests can disable it.
- Runs every 60 seconds.
- For each active favorite whose `departure_hhmm` is within the **next 20 minutes** AND today matches `days_of_week`:
  - Call the existing real-time predictions tool (`rts_api.get_predictions_for_route_stop(route_id, stop_id)` or equivalent — reuse agent tool internals via a Python-level function, not HTTP).
  - Compute `delay_min = realtime_eta - scheduled_eta`. If `delay_min >= delay_threshold_min`:
    - Look up all `push_subscriptions` for this `anon_uuid`.
    - Send push via `pywebpush.webpush()` with payload `{ title, body, url, route_id, stop_id }`.
    - Log outcome in `alert_log`.
  - Dedupe: do not fire twice for the same `favorite_id` within 30 minutes (query `alert_log`).
- On 410 Gone from the push service, delete the subscription (it's dead).
- On any other error, log and continue.

**Language:** push body text must be in the user's `language` (from `user_identities`). Use small templated strings in `utils/push_sender.py` keyed by language — reads from `agency_config.yaml` if present. Default to English.

### 5. Frontend — subscription flow

In `public_html/frontend.js`:

- On app init, ensure there is an `anon_uuid` in `localStorage` — generate one with `crypto.randomUUID()` if missing and `POST` to a new `/api/identity` endpoint to register it.
- Add a "Notify me" toggle in the UI near the chat input.
- When toggled on:
  1. `Notification.requestPermission()`.
  2. If granted: `navigator.serviceWorker.ready`, then `reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: <fetched from /api/push/vapid-public-key> })`.
  3. POST the subscription to `/api/push/subscribe`.
- When toggled off: unsubscribe locally and `DELETE /api/push/unsubscribe`.
- Handle permission-denied gracefully (show one-line help text, don't nag).

### 6. Frontend — favorites UI

Minimal first pass (iterate later):
- A "Favorites" section in the chat UI with:
  - Form: route dropdown (from existing `/api/routes`), stop input (reuse `search_stops` endpoint), time picker, day-of-week toggles, threshold slider (1–10 min).
  - List: existing favorites with delete button.
- Keep it simple and thumb-friendly. No fancy framework.

### 7. Service worker push handling

Extend `public_html/service-worker.js` (replace the PWA task's TODO):

```js
self.addEventListener('push', event => {
  const data = event.data?.json() ?? {};
  const title = data.title ?? 'Bus alert';
  const options = {
    body: data.body,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: data.url ?? '/' },
    tag: data.tag ?? 'transit-alert',
    renotify: true,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = event.notification.data?.url ?? '/';
  event.waitUntil(clients.openWindow(url));
});
```

### 8. Admin dashboard readout

Add one card to the existing dashboard showing:
- Active subscriptions count
- Active favorites count
- Alerts sent in last 24 hours (from `alert_log`)

No new pages or routes beyond what's needed for that card.

## Success criteria

1. `pytest` passes. New tests cover: subscribe endpoint, favorite CRUD, scheduler dedupe logic, push payload templating, VAPID key env loading.
2. End-to-end manual test (document how to run in the report):
   - Open app in Chrome, toggle "Notify me" on, grant permission.
   - Create a favorite for a route+stop you know will have data.
   - Use `curl` or a helper script to synthetically inject a "delayed" real-time response and trigger the scheduler.
   - A notification appears. Clicking it opens the app to the right page.
3. `grep -r "Gainesville\|go-rts" routes/push.py routes/favorites.py utils/push_sender.py utils/alert_scheduler.py` returns nothing.
4. Scheduler gracefully handles: no subscriptions, dead endpoints (410), rate-limited push service, missing real-time data.
5. Disabling `ENABLE_ALERT_SCHEDULER` stops all scheduled work (for tests / dev).

## Verification report to send back

- End-to-end manual test notes (subscribe → favorite → synthetic delay → push received)
- `pytest -q` output
- Dashboard screenshot with the new card
- `git diff --stat`

## Commit message format

```
feat: web push alerts for favorite routes

- VAPID + pywebpush backend, subscribe/unsubscribe endpoints
- Favorites CRUD (route + stop + time + days + threshold)
- APScheduler background job checks real-time every 60s against active
  favorites within 20 min of scheduled departure; fires push when delay
  exceeds user threshold; dedupes 30 min per favorite
- Service worker push + notificationclick handlers
- Frontend subscribe flow + favorites form + "Notify me" toggle
- Admin dashboard card: active subs, favorites, 24h alert count

This is the product wedge vs Go RTS / RideRTS — proactive delay alerts
before the rider leaves home.

Co-Authored-By: <Model Name> <noreply@...>
```

## Do NOT

- Do not hardcode "Gainesville", "RTS", phone numbers, or any agency content. All user-facing strings go through the language templates, which read from `agency_config.yaml`.
- Do not poll real-time from the frontend. The scheduler handles that server-side so battery + data stay clean.
- Do not store user email/phone/name. Anon UUID only. Identity upgrade is a separate future task.
- Do not add a "send test notification" endpoint in production without auth — it's a push-spam vector.
- Do not cache real-time predictions. Always fresh at scheduler tick time.
- Do not silently swallow push errors. Log them to `alert_log.outcome`.
