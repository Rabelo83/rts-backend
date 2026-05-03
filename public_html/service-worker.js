/**
 * public_html/service-worker.js
 * RTS PWA — App Shell Service Worker
 *
 * Strategy:
 *   /api/*                                      → network-only (real-time data)
 *   HTML navigations (/, /chat, etc.)           → network-first; cache only on
 *                                                 success; cache fallback if
 *                                                 offline. Never precached at
 *                                                 install time because they're
 *                                                 PIN-gated and an anonymous
 *                                                 SW install fetch redirects
 *                                                 to /login, poisoning the
 *                                                 cached HTML.
 *   Static assets (CSS/JS/icons/manifest)       → cache-first, network fallback
 *
 * Bump SW_VERSION to force clients to discard old caches on next visit.
 */

const SW_VERSION = 'v13';  // map: agency-config-driven default_view (no Gainesville hardcode)
const CACHE_NAME = `${SW_VERSION}-shell`;

/**
 * Files precached at install time.
 * Important: do NOT include navigation HTML routes (/, /chat) — those are
 * PIN-gated and an anonymous SW install fetch follows the redirect to the
 * login page, then writes the login page HTML to the cache as if it were
 * the app. The fetch handler now does network-first runtime caching for
 * navigation requests instead.
 */
const SHELL_URLS = [
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

  // 2. HTML navigations → network-first
  // Cache the response only if it's a 2xx final response. If the user is
  // unauthenticated, the response is the redirect-followed login page —
  // we still serve it (so they can log in) but DON'T cache it as the app
  // shell. The cached entry only updates when a successful authenticated
  // response comes through, which avoids the "PWA shows the login page
  // forever" bug seen with cache-first + PIN-gated routes.
  const isNavigation =
    request.mode === 'navigate' ||
    (request.method === 'GET' &&
     request.destination === 'document');

  if (isNavigation) {
    event.respondWith(
      (async () => {
        try {
          const networkResponse = await fetch(request);
          // Only cache navigation responses that did NOT redirect.
          // A redirect-followed response is almost always /login, which
          // we never want to serve from cache as if it were the app.
          if (
            networkResponse.ok &&
            !networkResponse.redirected &&
            url.origin === self.location.origin
          ) {
            const clean = await stripRedirected(networkResponse.clone());
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clean));
          }
          return stripRedirected(networkResponse);
        } catch (_) {
          // Offline → try the exact request, then fall back to the most
          // recently cached navigation response (last successful /chat or /).
          const cached = await caches.match(request);
          if (cached) return stripRedirected(cached);
          const fallback =
            (await caches.match('/')) ||
            (await caches.match('/chat'));
          if (fallback) return stripRedirected(fallback);
          return new Response(
            '<h1>Offline</h1><p>Please reconnect to use the transit assistant.</p>',
            { headers: { 'Content-Type': 'text/html' } }
          );
        }
      })()
    );
    return;
  }

  // 3. Static assets → cache-first, network fallback
  event.respondWith(
    (async () => {
      const cached = await caches.match(request);
      if (cached) return cached;

      try {
        const networkResponse = await fetch(request);
        if (
          request.method === 'GET' &&
          networkResponse.ok &&
          url.origin === self.location.origin
        ) {
          caches.open(CACHE_NAME).then((cache) => cache.put(request, networkResponse.clone()));
        }
        return networkResponse;
      } catch (_) {
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
