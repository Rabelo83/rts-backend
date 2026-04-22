/**
 * public_html/push_flow.js
 * Web push subscription flow, identity management, and favorites UI.
 * Loaded as a <script defer> on pages that have the favorites panel.
 *
 * No agency-specific strings are hardcoded here.
 * All config-dependent text comes from push notification payloads (server-side).
 */

(function initPushFlow() {
  'use strict';

  const ANON_KEY = 'pwa_anon_uuid';
  const SUB_KEY  = 'pwa_push_subscribed';

  // ── 1. Anon identity ─────────────────────────────────────────────────────

  function getOrCreateUUID() {
    let uuid = localStorage.getItem(ANON_KEY);
    if (!uuid) {
      uuid = crypto.randomUUID();
      localStorage.setItem(ANON_KEY, uuid);
    }
    return uuid;
  }

  const anonUUID = getOrCreateUUID();

  // Register identity on load (upsert)
  fetch('/api/identity', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ anon_uuid: anonUUID, language: navigator.language?.slice(0, 2) || 'en' }),
  }).catch(() => {}); // non-critical

  // ── 2. VAPID key ──────────────────────────────────────────────────────────

  let _vapidKey = null;

  async function getVapidKey() {
    if (_vapidKey) return _vapidKey;
    const res = await fetch('/api/push/vapid-public-key');
    if (!res.ok) throw new Error('VAPID key unavailable');
    const { key } = await res.json();
    _vapidKey = key;
    return key;
  }

  function _urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from(raw, c => c.charCodeAt(0));
  }

  // ── 3. Subscribe / Unsubscribe ────────────────────────────────────────────

  async function subscribePush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return null;
    const reg = await navigator.serviceWorker.ready;
    const key = await getVapidKey();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _urlBase64ToUint8Array(key),
    });
    await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        anon_uuid: anonUUID,
        subscription: sub.toJSON(),
        user_agent: navigator.userAgent.slice(0, 256),
      }),
    });
    localStorage.setItem(SUB_KEY, '1');
    return sub;
  }

  async function unsubscribePush() {
    if (!('serviceWorker' in navigator)) return;
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      const endpoint = sub.endpoint;
      await sub.unsubscribe();
      await fetch('/api/push/unsubscribe', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint }),
      }).catch(() => {});
    }
    localStorage.removeItem(SUB_KEY);
  }

  // ── 4. Notify-me toggle ───────────────────────────────────────────────────

  const notifyTrack = document.querySelector('.notify-track');
  const deniedHint  = document.getElementById('notify-denied-hint');

  function _setTrack(on) {
    if (!notifyTrack) return;
    notifyTrack.classList.toggle('is-on', on);
  }

  // Sync track with current permission state on load
  async function _syncToggleState() {
    if (!('PushManager' in window)) return;
    const reg = await navigator.serviceWorker?.ready.catch(() => null);
    if (!reg) return;
    const sub = await reg.pushManager.getSubscription().catch(() => null);
    _setTrack(!!sub);
  }
  _syncToggleState();

  if (notifyTrack) {
    notifyTrack.addEventListener('click', async () => {
      const isOn = notifyTrack.classList.contains('is-on');
      if (isOn) {
        await unsubscribePush();
        _setTrack(false);
      } else {
        const perm = await Notification.requestPermission();
        if (perm === 'granted') {
          await subscribePush().catch(e => console.warn('[PWA] subscribe failed', e));
          _setTrack(true);
          if (deniedHint) deniedHint.classList.remove('visible');
        } else {
          _setTrack(false);
          if (deniedHint) deniedHint.classList.add('visible');
        }
      }
    });
  }

  // ── 5. Favorites panel toggle ─────────────────────────────────────────────

  const favPanel  = document.getElementById('favorites-panel');
  const favHeader = document.querySelector('.fav-panel-header');

  if (favHeader && favPanel) {
    favHeader.addEventListener('click', () => {
      favPanel.classList.toggle('open');
    });
  }

  // ── 6. Day-of-week buttons ────────────────────────────────────────────────

  document.querySelectorAll('.dow-btn').forEach(btn => {
    btn.addEventListener('click', () => btn.classList.toggle('active'));
  });

  // Threshold slider label
  const slider   = document.getElementById('fav-threshold');
  const sliderLbl = document.getElementById('fav-threshold-val');
  if (slider && sliderLbl) {
    slider.addEventListener('input', () => {
      sliderLbl.textContent = `${slider.value} min`;
    });
  }

  // ── 7. Favorites CRUD ─────────────────────────────────────────────────────

  const favList = document.getElementById('fav-list');
  const favForm = document.getElementById('fav-form');

  async function loadFavorites() {
    if (!favList) return;
    try {
      const res = await fetch(`/api/favorites?anon_uuid=${encodeURIComponent(anonUUID)}`);
      const items = await res.json();
      renderFavorites(items);
    } catch (e) {
      favList.innerHTML = '<div class="fav-empty">Could not load favorites.</div>';
    }
  }

  function renderFavorites(items) {
    if (!favList) return;
    if (!items.length) {
      favList.innerHTML = '<div class="fav-empty">No saved routes yet.</div>';
      return;
    }
    favList.innerHTML = items.map(fav => `
      <div class="fav-item" data-id="${fav.id}">
        <div class="fav-item-info">
          <div class="fav-route-label">🚌 Route ${_esc(fav.route_id)} · Stop ${_esc(fav.stop_id)}</div>
          <div class="fav-meta">${_esc(fav.departure_hhmm)} · ${_esc(fav.days_of_week)} · ≥${fav.delay_threshold_min} min delay</div>
        </div>
        <div class="fav-item-actions">
          <button class="fav-active-toggle ${fav.active ? 'on' : ''}"
                  title="${fav.active ? 'Active' : 'Paused'}"
                  onclick="_favToggleActive(${fav.id}, ${fav.active ? 0 : 1})">
            ${fav.active ? 'ON' : 'OFF'}
          </button>
          <button class="btn-delete-fav" onclick="_favDelete(${fav.id})" title="Delete">🗑</button>
        </div>
      </div>
    `).join('');
  }

  function _esc(v) {
    return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  window._favToggleActive = async function(id, newActive) {
    await fetch(`/api/favorites/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ anon_uuid: anonUUID, active: newActive }),
    }).catch(() => {});
    loadFavorites();
  };

  window._favDelete = async function(id) {
    await fetch(`/api/favorites/${id}?anon_uuid=${encodeURIComponent(anonUUID)}`, {
      method: 'DELETE',
    }).catch(() => {});
    loadFavorites();
  };

  if (favForm) {
    favForm.addEventListener('submit', async e => {
      e.preventDefault();
      const route_id = document.getElementById('fav-route')?.value.trim();
      const stop_id  = document.getElementById('fav-stop')?.value.trim();
      const departure_hhmm = document.getElementById('fav-time')?.value;
      const activeDays = [...document.querySelectorAll('.dow-btn.active')]
        .map(b => b.dataset.day).join(',');
      const threshold = slider?.value || 3;

      if (!route_id || !stop_id || !departure_hhmm || !activeDays) {
        alert('Please fill in all fields and select at least one day.');
        return;
      }

      const btn = favForm.querySelector('.btn-save-fav');
      if (btn) btn.disabled = true;

      try {
        const res = await fetch('/api/favorites', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            anon_uuid: anonUUID,
            route_id, stop_id, departure_hhmm,
            days_of_week: activeDays,
            delay_threshold_min: parseInt(threshold),
          }),
        });
        if (res.ok) {
          favForm.reset();
          document.querySelectorAll('.dow-btn').forEach(b => b.classList.remove('active'));
          loadFavorites();
        } else {
          const err = await res.json().catch(() => ({}));
          alert(`Error: ${err.error || 'Could not save favorite.'}`);
        }
      } catch (err) {
        alert('Network error. Please try again.');
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  // Initial load
  loadFavorites();

}());
