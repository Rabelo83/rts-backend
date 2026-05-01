/**
 * map.js — Live bus map.
 *
 * Lazy-initialized when the user first taps the "Live Map" tab. Uses MapLibre GL
 * for vector rendering, OpenFreeMap for tiles (free, no key, ships to production).
 *
 * Differentiation move: the bottom sheet on every tap-bus / tap-stop carries an
 * "Ask the Assistant" button that switches to the chat tab with a question
 * pre-loaded — bridging the visual surface and the AI surface without crowding
 * either one.
 */
(() => {
  'use strict';

  const TILE_STYLE       = 'https://tiles.openfreemap.org/styles/liberty';
  const VEHICLE_POLL_MS  = 10_000;
  const GAINESVILLE      = { center: [-82.345, 29.65], zoom: 11.7 };

  let map           = null;
  let initStarted   = false;
  let pollTimer     = null;
  let routes        = [];           // [{route_id, short_name, long_name, color}]
  let activeRouteId = null;         // null = show all
  let routeDetail   = null;         // currently loaded route detail (polylines + stops)
  let stopMarkers   = [];           // MapLibre Markers for stops
  let busMarkers    = new Map();    // vehicle_id → Marker
  let routeInfoCache = new Map();   // route_id → route overview
  let currentRouteInfoId = null;    // last route shown in the top info panel

  // ── Public entry point (called by switchTab) ──────────────────────────────
  window.initMap = function initMap() {
    if (initStarted) {
      // Tab re-shown — MapLibre needs a resize after the panel becomes visible.
      if (map) requestAnimationFrame(() => map.resize());
      return;
    }
    initStarted = true;
    bootstrap().catch(err => {
      console.error('[map] init failed:', err);
      const canvas = document.getElementById('map-canvas');
      if (canvas) canvas.innerHTML =
        `<div style="padding:30px;color:#94a3b8;text-align:center">Map failed to load. ${err.message || ''}</div>`;
    });
  };

  // ── Public: deep-link from another surface (future use) ───────────────────
  window.closeMapSheet = () => {
    const sheet = document.getElementById('map-sheet');
    if (sheet) sheet.classList.add('hidden');
  };

  window.closeRouteInfo = () => {
    hideRouteInfo();
  };

  window.reopenRouteInfo = () => {
    if (currentRouteInfoId) showRouteInfo(currentRouteInfoId);
  };

  // ── Init sequence ─────────────────────────────────────────────────────────
  async function bootstrap() {
    if (typeof maplibregl === 'undefined') {
      throw new Error('MapLibre GL not loaded');
    }

    map = new maplibregl.Map({
      container: 'map-canvas',
      style:     TILE_STYLE,
      center:    GAINESVILLE.center,
      zoom:      GAINESVILLE.zoom,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    document.getElementById('map-locate')?.addEventListener('click', centerOnUser);

    await new Promise(resolve => map.once('load', resolve));

    routes = await fetchJSON('/api/map/routes').then(d => d.routes || []);
    renderRouteRail(routes);

    await pollVehicles();
    pollTimer = setInterval(pollVehicles, VEHICLE_POLL_MS);

    // Stop polling when the page is hidden — saves battery and BusTime calls.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = null;
      } else if (!pollTimer) {
        pollVehicles();
        pollTimer = setInterval(pollVehicles, VEHICLE_POLL_MS);
      }
    });
  }

  // ── Route chip rail ───────────────────────────────────────────────────────
  function renderRouteRail(routes) {
    const rail = document.getElementById('map-route-rail');
    if (!rail) return;
    const chips = [
      `<button class="map-chip active" data-route="" aria-pressed="true">All</button>`,
      ...routes.map(r => `
        <button class="map-chip" data-route="${escAttr(r.route_id)}" aria-pressed="false">
          <span class="map-chip-dot" style="background:${r.color}"></span>
          ${escHTML(r.short_name)}
        </button>
      `),
    ];
    rail.innerHTML = chips.join('');
    rail.addEventListener('click', e => {
      const btn = e.target.closest('.map-chip');
      if (!btn) return;
      selectRoute(btn.dataset.route || null);
    });
  }

  async function selectRoute(routeId) {
    activeRouteId = routeId;

    document.querySelectorAll('.map-chip').forEach(c => {
      const isActive = (c.dataset.route || null) === routeId;
      c.classList.toggle('active', isActive);
      c.setAttribute('aria-pressed', isActive);
    });

    clearRouteOverlay();

    if (routeId) {
      try {
        showRouteInfo(routeId);
        routeDetail = await fetchJSON(`/api/map/route/${encodeURIComponent(routeId)}`);
        drawRoutePolylines(routeDetail);
        drawRouteStops(routeDetail);
        fitToRoute(routeDetail);
      } catch (err) {
        console.warn('[map] route detail failed:', err);
      }
    } else {
      routeDetail = null;
      currentRouteInfoId = null;
      hideRouteInfo();
      map.flyTo({ center: GAINESVILLE.center, zoom: GAINESVILLE.zoom });
    }

    redrawVehicleFilter();
  }

  // ── Route polylines + stops (vector layers) ───────────────────────────────
  function drawRoutePolylines(detail) {
    const features = (detail.shapes || []).map(s => ({
      type: 'Feature',
      properties: { direction: s.direction, headsign: s.headsign },
      geometry: {
        type: 'LineString',
        coordinates: s.points.map(p => [p[1], p[0]]), // [lat,lon] → [lon,lat]
      },
    }));
    if (!features.length) return;
    if (map.getSource('route-line')) {
      map.getSource('route-line').setData({ type: 'FeatureCollection', features });
    } else {
      map.addSource('route-line', { type: 'geojson', data: { type: 'FeatureCollection', features } });
      map.addLayer({
        id: 'route-line-layer',
        type: 'line',
        source: 'route-line',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': detail.color || '#60a5fa',
          'line-width': 4,
          'line-opacity': 0.85,
        },
      });
    }
    map.setPaintProperty('route-line-layer', 'line-color', detail.color || '#60a5fa');
  }

  function drawRouteStops(detail) {
    (detail.stops || []).forEach(s => {
      if (s.lat == null || s.lon == null) return;
      const el = document.createElement('div');
      el.className = 'map-stop route-stop';
      el.title = `${s.stop_name} (Stop ${s.stop_id})`;
      el.addEventListener('click', e => {
        e.stopPropagation();
        showStopSheet(s);
      });
      const marker = new maplibregl.Marker({ element: el }).setLngLat([s.lon, s.lat]).addTo(map);
      stopMarkers.push(marker);
    });
  }

  function clearRouteOverlay() {
    stopMarkers.forEach(m => m.remove());
    stopMarkers = [];
    if (map.getLayer('route-line-layer')) map.removeLayer('route-line-layer');
    if (map.getSource('route-line'))      map.removeSource('route-line');
  }

  function fitToRoute(detail) {
    const all = (detail.shapes || []).flatMap(s => s.points);
    if (!all.length) return;
    const lats = all.map(p => p[0]);
    const lons = all.map(p => p[1]);
    map.fitBounds(
      [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
      { padding: 50, duration: 700 }
    );
  }

  // ── Vehicle polling ───────────────────────────────────────────────────────
  async function pollVehicles() {
    let data;
    try {
      data = await fetchJSON('/api/map/vehicles');
    } catch (err) {
      console.warn('[map] vehicle poll failed:', err);
      return;
    }
    const vehicles = data.vehicles || [];
    const seen = new Set();

    vehicles.forEach(v => {
      if (v.lat == null || v.lon == null || !v.vehicle_id) return;
      seen.add(v.vehicle_id);
      const filtered = activeRouteId && v.route !== activeRouteId;

      let marker = busMarkers.get(v.vehicle_id);
      if (!marker) {
        const el = buildBusEl(v);
        el.addEventListener('click', e => {
          e.stopPropagation();
          showBusSheet(v);
        });
        marker = new maplibregl.Marker({ element: el }).setLngLat([v.lon, v.lat]).addTo(map);
        busMarkers.set(v.vehicle_id, marker);
      } else {
        marker.setLngLat([v.lon, v.lat]);
        updateBusEl(marker.getElement(), v);
      }
      marker.getElement().style.display = filtered ? 'none' : '';
    });

    // Drop markers for buses no longer reported
    for (const [vid, marker] of busMarkers) {
      if (!seen.has(vid)) {
        marker.remove();
        busMarkers.delete(vid);
      }
    }
  }

  function buildBusEl(v) {
    const el = document.createElement('div');
    el.className = 'map-bus';
    el.innerHTML = `
      <div class="map-bus-body">
        <div class="map-bus-sign">
          <div class="map-bus-number"></div>
        </div>
        <div class="map-bus-windshield"></div>
        <div class="map-bus-headlight left"></div>
        <div class="map-bus-headlight right"></div>
        <div class="map-bus-bumper"></div>
      </div>
    `;
    updateBusEl(el, v);
    return el;
  }

  function updateBusEl(el, v) {
    const color = colorForRoute(v.route);
    el.dataset.route = v.route || '';
    const body = el.querySelector('.map-bus-body');
    const number = el.querySelector('.map-bus-number');
    if (body) body.style.background = color;
    if (number) number.textContent = v.route || '?';
    let arrow = el.querySelector('.map-bus-arrow');
    if (!arrow) {
      arrow = document.createElement('div');
      arrow.className = 'map-bus-arrow';
      el.appendChild(arrow);
    }
    const hdg = parseFloat(v.heading);
    if (!Number.isNaN(hdg)) {
      arrow.style.transform = `translateX(-50%) rotate(${hdg}deg)`;
      arrow.style.opacity = '1';
    } else {
      arrow.style.opacity = '0';
    }
  }

  function redrawVehicleFilter() {
    for (const [, marker] of busMarkers) {
      const el = marker.getElement();
      const route = (el.dataset.route || '').trim();
      el.style.display = (activeRouteId && route !== activeRouteId) ? 'none' : '';
    }
  }

  function colorForRoute(routeId) {
    const r = routes.find(r => r.route_id === routeId || r.short_name === routeId);
    return (r && r.color) || '#60a5fa';
  }

  function formatStopId(stopId) {
    const digits = String(stopId ?? '').replace(/\D/g, '');
    if (!digits) return String(stopId ?? '');
    return digits.padStart(4, '0');
  }

  // ── Bottom sheet: bus / stop details + Ask-the-Assistant deep link ────────
  function showBusSheet(v) {
    const route = routes.find(r => r.route_id === v.route);
    const routeName = route ? `Route ${route.short_name} — ${route.long_name}` : `Route ${v.route}`;
    const headsign  = v.destination ? `Heading: ${escHTML(v.destination)}` : '';
    const delayed   = v.delayed ? '<span style="color:#fbbf24">⚠ Reported delayed</span>' : '';

    if (v.route) showRouteInfo(v.route);

    const askMsg = `Where is bus ${v.vehicle_id} on Route ${v.route} right now and when will it reach me?`;

    renderSheet(`
      <h3>Bus ${escHTML(v.vehicle_id || '')}</h3>
      <div class="meta">${escHTML(routeName)}</div>
      ${headsign ? `<div class="pred-row"><span>Destination</span><span>${escHTML(v.destination)}</span></div>` : ''}
      ${v.speed != null ? `<div class="pred-row"><span>Speed</span><span>${escHTML(v.speed)} mph</span></div>` : ''}
      ${delayed ? `<div class="pred-row"><span></span><span>${delayed}</span></div>` : ''}
      <div class="map-sheet-actions">
        <button class="map-sheet-btn" onclick="window.askAssistantFromMap(${JSON.stringify(askMsg).replace(/"/g, '&quot;')})">Ask the Assistant</button>
      </div>
    `);
  }

  async function showRouteInfo(routeId) {
    const panel = document.getElementById('map-route-info');
    const body  = document.getElementById('map-route-info-body');
    if (!panel || !body || !routeId) return;

    currentRouteInfoId = routeId;
    panel.classList.remove('hidden');
    setRouteInfoReopenVisible(false);
    setRouteInfoScrollHintVisible(false);
    const route = routes.find(r => r.route_id === routeId || r.short_name === routeId);
    const label = route ? `Route ${route.short_name}` : `Route ${routeId}`;
    body.innerHTML = `<h3>${escHTML(label)}</h3><div class="meta">Loading route summary…</div>`;
    updateRouteInfoScrollHint();

    try {
      let info = routeInfoCache.get(routeId);
      if (!info) {
        info = await fetchJSON(`/api/map/route/${encodeURIComponent(routeId)}/overview`);
        routeInfoCache.set(routeId, info);
      }
      renderRouteInfo(info);
    } catch (err) {
      console.warn('[map] route overview failed:', err);
      body.innerHTML = `<h3>${escHTML(label)}</h3><div class="meta">Route summary unavailable right now.</div>`;
      updateRouteInfoScrollHint();
    }
  }

  function hideRouteInfo() {
    const panel = document.getElementById('map-route-info');
    if (panel) panel.classList.add('hidden');
    setRouteInfoScrollHintVisible(false);
    setRouteInfoReopenVisible(Boolean(currentRouteInfoId));
  }

  function setRouteInfoReopenVisible(show) {
    const btn = document.getElementById('map-route-info-reopen');
    if (btn) btn.classList.toggle('hidden', !show);
  }

  function setRouteInfoScrollHintVisible(show) {
    const hint = document.getElementById('map-route-scroll-hint');
    if (hint) hint.classList.toggle('hidden', !show);
  }

  function updateRouteInfoScrollHint() {
    const panel = document.getElementById('map-route-info');
    if (!panel || panel.classList.contains('hidden')) return;
    setRouteInfoScrollHintVisible(false);
    requestAnimationFrame(() => {
      const hasOverflow = panel.scrollHeight > panel.clientHeight + 6;
      const nearBottom = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 18;
      setRouteInfoScrollHintVisible(hasOverflow && !nearBottom);
      panel.onscroll = () => {
        const atBottom = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 18;
        setRouteInfoScrollHintVisible(hasOverflow && !atBottom);
      };
    });
  }

  function renderRouteInfo(info) {
    const body = document.getElementById('map-route-info-body');
    if (!body) return;

    const title = `Route ${info.route || ''}${info.route_name ? ` — ${info.route_name}` : ''}`;
    const directions = (info.directions || []).length
      ? (info.directions || []).map(d => `
          <div class="route-summary-card">
            <div class="route-summary-head">${escHTML(d.headsign || 'Direction')}</div>
            <div class="route-summary-line"><span>First bus</span><strong>${escHTML(d.first || 'n/a')}</strong></div>
            <div class="route-summary-line"><span>Last bus</span><strong>${escHTML(d.last || 'n/a')}</strong></div>
            <div class="route-summary-line"><span>Frequency</span><strong>${escHTML(d.frequency || 'varies')}</strong></div>
          </div>
        `).join('')
      : `<div class="route-summary-card"><div class="route-summary-line"><span>No scheduled service today.</span><span></span></div></div>`;

    const serviceRows = Object.entries(info.schedule_by_service_type || {}).map(([label, hours]) => `
      <div class="route-service-row">
        <span>${escHTML(label)}</span>
        <strong>${escHTML(hours.first || 'n/a')} – ${escHTML(hours.last || 'n/a')}</strong>
      </div>
    `).join('');

    body.innerHTML = `
      <h3>${escHTML(title)}</h3>
      <div class="meta">${escHTML(info.day_label || 'Today')} · ${info.runs_today ? 'Runs today' : 'No service today'}</div>
      <div class="route-summary-grid">${directions}</div>
      ${serviceRows ? `<div class="map-sheet-section">Service hours</div>${serviceRows}` : ''}
    `;
    updateRouteInfoScrollHint();
  }

  async function showStopSheet(stop) {
    const stopIdLabel = formatStopId(stop.stop_id);
    renderSheet(`
      <h3>${escHTML(stop.stop_name)}</h3>
      <div class="map-sheet-stop-meta">
        <span class="map-stop-id-badge">Stop ID ${escHTML(stopIdLabel)}</span>
        <div class="meta">Loading arrivals…</div>
      </div>
    `);
    let preds = [];
    let scheduled = [];
    let scheduleServiceDay = '';
    try {
      // /api/predictions normalizes BusTime fields to {route, direction,
      // destination, minutes, vehicle_id, arrival_time, delayed, ...}.
      const data = await fetchJSON(`/api/predictions?stop_id=${encodeURIComponent(stop.stop_id)}`);
      preds = (data && data.predictions) || [];
    } catch {
      preds = [];
    }
    try {
      const data = await fetchJSON(`/api/map/stop/${encodeURIComponent(stop.stop_id)}/schedule?limit=6`);
      scheduled = (data && data.departures) || [];
      scheduleServiceDay = (data && data.service_day_label) || '';
    } catch {
      scheduled = [];
      scheduleServiceDay = '';
    }
    const askMsg = `What's coming up at stop ${stopIdLabel} (${stop.stop_name})?`;

    const fmtEta = m => {
      if (m == null || m === '') return '?';
      const s = String(m);
      if (s.toUpperCase() === 'DUE') return 'DUE';
      const n = parseInt(s, 10);
      return Number.isFinite(n) ? `${n} min` : s;
    };

    const liveHTML = preds.length
      ? `<div class="map-sheet-section">Live ETAs</div>` + preds.slice(0, 5).map(p => {
          const route = p.route || '';
          const dest  = p.destination || '';
          const eta   = fmtEta(p.minutes);
          const dly   = p.delayed ? ' ⚠' : '';
          return `<div class="pred-row"><span>Route ${escHTML(route)} → ${escHTML(dest)}${dly}</span><span>${escHTML(eta)}</span></div>`;
        }).join('')
      : `<div class="map-sheet-section">Live ETAs</div><div class="pred-row"><span>No live predictions right now.</span><span></span></div>`;

    const scheduleHeading = scheduled.length
      ? `Scheduled next${scheduleServiceDay ? ` · ${scheduleServiceDay}` : ''}`
      : 'Scheduled next';

    const scheduleHTML = scheduled.length
      ? `<div class="map-sheet-section">${escHTML(scheduleHeading)}</div>` + scheduled.map(s => {
          const route = s.route || '';
          const dest  = s.headsign || '';
          const time  = s.time_label || s.time || '';
          return `<div class="pred-row"><span>Route ${escHTML(route)} → ${escHTML(dest)}</span><span>${escHTML(time)}</span></div>`;
        }).join('')
      : `<div class="map-sheet-section">${escHTML(scheduleHeading)}</div><div class="pred-row"><span>No scheduled departures found soon.</span><span></span></div>`;

    renderSheet(`
      <h3>${escHTML(stop.stop_name)}</h3>
      <div class="map-sheet-stop-meta">
        <span class="map-stop-id-badge">Stop ID ${escHTML(stopIdLabel)}</span>
        <div class="meta">${scheduleServiceDay ? `Next service ${escHTML(scheduleServiceDay)}` : 'Upcoming stop departures'}</div>
      </div>
      ${liveHTML}
      ${scheduleHTML}
      <div class="map-sheet-actions">
        <button class="map-sheet-btn" onclick="window.askAssistantFromMap(${JSON.stringify(askMsg).replace(/"/g, '&quot;')})">Ask the Assistant</button>
        <button class="map-sheet-btn ghost" onclick="window.planTripFromMap(${JSON.stringify(stop.stop_name).replace(/"/g, '&quot;')})">Plan trip from here</button>
      </div>
    `);
  }

  function renderSheet(html) {
    const sheet = document.getElementById('map-sheet');
    const body  = document.getElementById('map-sheet-body');
    if (!sheet || !body) return;
    body.innerHTML = html;
    sheet.classList.remove('hidden');
  }

  // ── Deep-links from sheet to chat / trip planner ──────────────────────────
  window.askAssistantFromMap = function(message) {
    if (typeof window.switchTab === 'function') window.switchTab('chat');
    window.closeMapSheet();
    const input = document.getElementById('chat-input');
    if (input) {
      input.value = message;
      input.focus();
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };

  window.planTripFromMap = function(stopName) {
    if (typeof window.switchTab === 'function') window.switchTab('trip');
    window.closeMapSheet();
    const origin = document.getElementById('origin-input');
    if (origin) {
      origin.value = stopName;
      origin.focus();
      origin.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };

  // ── Geolocation ───────────────────────────────────────────────────────────
  function centerOnUser() {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      pos => map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom: 14, duration: 700 }),
      err => console.warn('[map] geolocation:', err.message),
      { enableHighAccuracy: false, timeout: 5000, maximumAge: 30_000 }
    );
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  async function fetchJSON(url) {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(`HTTP ${r.status} on ${url}`);
    return r.json();
  }

  function escHTML(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }
  function escAttr(s) { return escHTML(s).replace(/"/g, '&quot;'); }
})();
