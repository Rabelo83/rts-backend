# Task: Add PWA primitives (installable app + offline shell)

> Read [prompts/context/project-brief.md](../context/project-brief.md) first.

## Goal

Turn the current web page into an **installable PWA**. After this task, a user visiting the site on Chrome/Edge (desktop or Android) or Safari (iOS) can "Add to Home Screen" and launch the app standalone. The app shell works offline. **No push notifications yet** — that's a separate task ([add-web-push.md](./add-web-push.md)).

## Why this matters

Without PWA primitives the app competes with RideRTS and Go RTS on equal footing (all three are "a website"). With them, we match native-app install UX and unlock push alerts in the next task.

## Scope — what you may touch

- `public_html/` (HTML, JS, CSS, new manifest, new icons, new service worker)
- `app.py` or a new `routes/pwa.py` blueprint (only to serve a dynamic `manifest.json` and optionally the service worker from a safe path)
- `requirements.txt` only if you add a library (you shouldn't need one)
- Tests under `tests/` for any new Flask routes

Do **not** touch: agent code (`routes/agent_*.py`), agent tools, GTFS code, `Backend Basics/`, `scripts/`, the existing admin dashboard.

## Deliverables

### 1. Dynamic `manifest.json`

Serve `manifest.json` via Flask at `/manifest.json`. It must **read values from config, not hardcode them**.

Config source (choose whichever exists at task time):
- **Preferred:** `agency_config.yaml` loaded via `utils/agency_config.py` (being built in a sibling task).
- **Fallback if `agency_config.yaml` does not yet exist:** create `public_html/branding.json` with the keys below, and `TODO`-comment the Flask route to switch to `agency_config.yaml` when available.

Keys the manifest must interpolate:
```json
{
  "name": "{agency.full_name} — Bus Tracker",
  "short_name": "{agency.short_name} Bus",
  "description": "AI-powered transit assistant for {agency.city} riders.",
  "start_url": "/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "{branding.background_color}",
  "theme_color": "{branding.primary_color}",
  "lang": "{languages.default}",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
  ]
}
```

Add unit test `tests/test_pwa_manifest.py` asserting the route returns 200, correct `Content-Type: application/manifest+json`, and the name field equals `{agency.full_name} — Bus Tracker`.

### 2. Icons

Create `public_html/icons/` with placeholder icons:
- `icon-192.png` (192×192, any-maskable safe zone)
- `icon-512.png` (512×512, any-maskable safe zone)
- `apple-touch-icon.png` (180×180)
- `favicon.ico`

Since you cannot render images, generate them programmatically with Pillow (already in `requirements.txt`) during the task: a solid fill in `branding.primary_color` with the `agency.short_name` letters centered in white. Include a short script `scripts/generate_placeholder_icons.py` so icons can be regenerated per agency later. Commit the generated PNGs.

### 3. Service worker

`public_html/service-worker.js`:
- Registered from `public_html/frontend.js` on `window.load`
- Scope: `/`
- Strategy:
  - **App shell** (HTML, CSS, JS, icons, manifest): cache-first with network fallback, versioned by a build constant `SW_VERSION = 'v1'` — bump to invalidate
  - **API calls** (`/api/*`): network-only, **never cache** (real-time data must not be stale)
  - **Navigation fallback** when offline: serve cached `/` shell and let JS render an "offline — showing last-known info" banner
- Install event: precache the shell list
- Activate event: delete old caches whose names don't start with `SW_VERSION`
- Fetch event: routes requests to the right strategy
- No push/notification handlers yet — placeholder `// TODO(add-web-push): push + notificationclick` comment

### 4. Install flow

In `public_html/frontend.js`:
- Capture `beforeinstallprompt`, prevent default, stash the event.
- Render a new "Install app" button (CSS class `btn-install`) that is hidden by default and unhidden when the event fires.
- On click: call `event.prompt()`, `await event.userChoice`, hide the button regardless of outcome.
- Listen for `appinstalled` and log to console (hook for analytics later).
- iOS (Safari) can't use `beforeinstallprompt`. Detect iOS Safari and, on first visit after 3 seconds, show a one-time dismissable tip: "Tap Share → Add to Home Screen to install." Persist dismissal in `localStorage`.

### 5. iOS meta tags and theme

In `public_html/index.html` (and `chat.html`, `wizard.html`, `dashboard.html` — check all entry HTMLs):
- `<link rel="manifest" href="/manifest.json">`
- `<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">`
- `<meta name="apple-mobile-web-app-capable" content="yes">`
- `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">`
- `<meta name="apple-mobile-web-app-title" content="{agency.short_name} Bus">` (read from config at render time if possible; otherwise `TODO`-comment)
- `<meta name="theme-color" content="{branding.primary_color}">`

### 6. Offline banner

In `frontend.js`, listen to `online`/`offline` events and toggle a top banner (`#offline-banner`) that says "You're offline. Showing last known information." Style it with the existing CSS.

## Success criteria

All of the following must be true when the task is marked done:

1. `pytest` passes (new tests included).
2. In Chrome DevTools → Application → Manifest: no errors, all icon sizes shown, "Installable" badge green.
3. In DevTools → Service Workers: SW registered, scope `/`, active.
4. Throttle network to Offline in DevTools, reload: the app shell loads from cache; a visible "offline" banner appears.
5. An "Install app" button appears in Chrome when the browser considers the site installable.
6. On iOS Safari (emulated user-agent acceptable), the "Add to Home Screen" tip renders once and can be dismissed.
7. `grep -r "Gainesville\|go-rts\|RTS" public_html/manifest.json public_html/service-worker.js public_html/branding.json 2>/dev/null` returns only values that came from the config template — no stray hardcodes. Same for the Python manifest route.
8. `git status` is clean after commit.

## Verification report to send back

- Screenshot of DevTools → Application → Manifest (all green)
- Screenshot of DevTools → Application → Service Workers (activated)
- Screenshot of offline reload working
- `git diff --stat` output
- `pytest -q` output

## Commit message format

```
feat: add PWA primitives (manifest, icons, service worker, install flow)

- Dynamic /manifest.json reads branding from agency config
- Service worker caches app shell, network-only for /api/*, offline banner
- Install button on Chrome/Edge, Add-to-Home-Screen tip on iOS Safari
- Placeholder icons generated from branding.primary_color + short_name
- Unblocks web-push in the next task (add-web-push.md)

Co-Authored-By: <Model Name> <noreply@...>
```

## Do NOT

- Do not add push notification code. That's [add-web-push.md](./add-web-push.md).
- Do not touch agent code or agent tools.
- Do not rewrite the frontend (no Vite, no Svelte, no framework migration). Vanilla JS only.
- Do not cache API responses. Real-time data staleness would cause riders to miss buses.
- Do not hardcode "Gainesville", "RTS", or any agency-specific string in any new file. Use the config source.
