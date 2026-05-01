/**
 * public_html/service-worker.js
 * RTS PWA — App Shell Service Worker
 *
 * Strategy:
 *   App shell (HTML, CSS, JS, icons, manifest) → cache-first, network fallback
 *   /api/*                                      → network-only (real-time data)
 *   Navigation offline fallback                 → cached "/" shell + offline banner
 *
 * Bump SW_VERSION to force clients to discard old caches on next visit.
 *
 * TODO(add-web-push): push + notificationclick handlers go here when
 * add-web-push.md is implemented.
 */

const SW_VERSION = 'v10';  // map.js predictions field mapping + system-wide chat tool
const CACHE_NAME = `${SW_VERSION}-shell`;

/** Files that form the installable app shell. */
const SHELL_URLS = [
  '/',
  '/chat',
  '/static/style.css',
  '/static/pwa.css',
  '/static/frontend.js',
  '/static/chat_v2.js',
  '/static/trip_planner.js',
  '/static/map.js',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png',
];

// ── Redirect sanitizer ───────────────────────────────────────────────────────
// iOS Safari refuses to serve cached responses whose `redirected` flag is true
// ("Response served by service worker has redirections"). Strip the flag by
// re-materializing the response body into a fresh Response object before
// caching or returning it as a navigation response.
async function stripRedirected(response) {
  if (!response || !response.redirected) return response;
  const body = await response.clone().blob();
  return new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
}

// ── Install: precache the shell ──────────────────────────────────────────────

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async (cache) => {
      // Manual fetch + sanitize per URL instead of cache.addAll, so we can
      // strip redirected flags that iOS won't tolerate on replay.
      await Promise.all(
        SHELL_URLS.map(async (url) => {
          try {
            const res = await fetch(url, { redirect: 'follow', cache: 'reload' });
            if (!res || !res.ok) return;
            const clean = await stripRedirected(res);
            await cache.put(url, clean);
          } catch (_) {
            // best-effort precache — skip failures
          }
        })
      );
    })
  );
  // Take control immediately rather than waiting for the next navigation.
  self.skipWaiting();
});

// ── Activate: delete stale caches ───────────────────────────────────────────

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !k.startsWith(SW_VERSION))
          .map((k) => caches.delete(k))
      )
    )
  );
  // Claim all open clients so the new SW controls them immediately.
  self.clients.claim();
});

// ── Fetch: route to the right strategy ──────────────────────────────────────

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // 1. API calls → network-only (never cache real-time data)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(request));
    return;
  }

  // 2. App shell → cache-first, network fallback
  event.respondWith(
    (async () => {
      const cached = await caches.match(request);
      if (cached) {
        // Cached responses can still carry the redirected flag on iOS.
        // Always sanitize before returning as a navigation response.
        return stripRedirected(cached);
      }

      try {
        const networkResponse = await fetch(request);

        if (
          request.method === 'GET' &&
          networkResponse.ok &&
          url.origin === self.location.origin
        ) {
          // Sanitize before caching so replays never trip iOS.
          const clean = await stripRedirected(networkResponse.clone());
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clean));
        }

        // Also sanitize the response we return for navigations.
        return request.mode === 'navigate'
          ? stripRedirected(networkResponse)
          : networkResponse;
      } catch (_) {
        // 3. Navigation offline fallback → serve cached "/" so the JS shell
        //    boots and the offline banner can be displayed by frontend.js.
        if (request.mode === 'navigate') {
          const fallback = await caches.match('/');
          if (fallback) return stripRedirected(fallback);
          return new Response(
            '<h1>Offline</h1><p>Please reconnect to use the transit assistant.</p>',
            { headers: { 'Content-Type': 'text/html' } }
          );
        }
        // Non-navigation resources that aren't cached — return nothing useful
        return new Response('', { status: 503, statusText: 'Offline' });
      }
    })()
  );
});

// ── Push notification handlers (add-web-push) ───────────────────────────────

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
