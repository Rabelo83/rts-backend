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

const SW_VERSION = 'v4';  // bumped for messenger-grade polish — forces cache refresh
const CACHE_NAME = `${SW_VERSION}-shell`;

/** Files that form the installable app shell. */
const SHELL_URLS = [
  '/',
  '/chat',
  '/wizard',
  '/static/style.css',
  '/static/pwa.css',
  '/static/frontend.js',
  '/static/chat_v2.js',
  '/static/wizard.js',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png',
];

// ── Install: precache the shell ──────────────────────────────────────────────

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS))
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
    caches.match(request).then((cached) => {
      if (cached) return cached;

      return fetch(request)
        .then((networkResponse) => {
          // Cache successful GET responses for shell assets
          if (
            request.method === 'GET' &&
            networkResponse.ok &&
            url.origin === self.location.origin
          ) {
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) =>
              cache.put(request, responseToCache)
            );
          }
          return networkResponse;
        })
        .catch(() => {
          // 3. Navigation offline fallback → serve cached "/" so the JS shell
          //    boots and the offline banner can be displayed by frontend.js.
          if (request.mode === 'navigate') {
            return caches.match('/') || new Response(
              '<h1>Offline</h1><p>Please reconnect to use the transit assistant.</p>',
              { headers: { 'Content-Type': 'text/html' } }
            );
          }
          // Non-navigation resources that aren't cached — return nothing useful
          return new Response('', { status: 503, statusText: 'Offline' });
        });
    })
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
