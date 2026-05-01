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
  let selectedRouteIds = new Set(); // empty = show all
  let routeDetailCache = new Map(); // route_id → route detail (polylines + stops)
  let routeOverlayRun = 0;          // guards async multi-route overlay updates
  let stopMarkers   = [];           // MapLibre Markers for stops
  let busMarkers    = new Map();    // vehicle_id → Marker
  let routeInfoCache = new Map();   // route_id → route overview
  let currentRouteInfoId = null;    // last route shown in the top info panel
  let routeRailExpanded = false;    // compact by default; expands into wrapped route tray
  let userMarker    = null;         // MapLibre Marker for "you are here"

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
    document.getElementById('map-route-toggle')?.addEventListener('click', () => {
      setRouteRailExpanded(!routeRailExpanded);
      if (routeRailExpanded) hideRouteInfo(false);
      else setRouteInfoReopenVisible(Boolean(currentRouteInfoId));
    });
    map.on('click', collapseRouteRailFromMap);

    const searchForm = document.getElementById('map-stop-search-form');
    if (searchForm) searchForm.addEventListener('submit', onStopSearchSubmit);
    window.addEventListener('resize', syncMapControlOffset);

    await new Promise(resolve => map.once('load', resolve));

    routes = await fetchJSON('/api/map/routes').then(d => d.routes || []);
    renderRouteRail(routes);
    syncMapControlOffset();

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
      `<button class="map-chip map-chip-all active" data-route="" aria-pressed="true">All</button>`,
      ...routes.map(r => {
        const label = String(r.short_name || r.route_id || '');
        const longClass = label.length >= 3 ? ' long-route' : '';
        return `
          <button class="map-chip map-chip-route" data-route="${escAttr(r.route_id)}" aria-pressed="false" aria-label="Show Route ${escAttr(label)}">
            <span class="map-chip-bus${longClass}" style="background:${escAttr(r.color)}" aria-hidden="true">
              <span class="map-chip-bus-sign">${escHTML(label)}</span>
            </span>
          </button>
        `;
      }),
    ];
    rail.innerHTML = chips.join('');
    setRouteRailExpanded(routeRailExpanded);
    rail.addEventListener('click', e => {
      const btn = e.target.closest('.map-chip');
      if (!btn) return;
      toggleRouteSelection(btn.dataset.route || null);
    });
  }

  async function toggleRouteSelection(routeId) {
    if (!routeId) {
      selectedRouteIds.clear();
      setRouteRailExpanded(false);
    } else if (selectedRouteIds.has(routeId)) {
      selectedRouteIds.delete(routeId);
    } else {
      selectedRouteIds.add(routeId);
    }

    updateRouteChipStates();
    hideRouteInfo(false);
    await refreshSelectedRouteOverlay();
    redrawVehicleFilter();
  }

  async function ensureRouteSelected(routeId) {
    if (!routeId || selectedRouteIds.has(routeId)) return;
    selectedRouteIds.add(routeId);
    updateRouteChipStates();
    await refreshSelectedRouteOverlay();
    redrawVehicleFilter();
  }

  function updateRouteChipStates() {
    document.querySelectorAll('.map-chip').forEach(c => {
      const routeId = c.dataset.route || '';
      const isActive = routeId ? selectedRouteIds.has(routeId) : selectedRouteIds.size === 0;
      c.classList.toggle('active', isActive);
      c.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });

    const toggle = document.querySelector('.map-route-toggle');
    if (toggle) {
      const n = selectedRouteIds.size;
      const label = toggle.querySelector('.map-route-toggle-label');
      if (label) {
        label.textContent = n === 0
          ? 'Routes: All'
          : n === 1
            ? `Routes: ${[...selectedRouteIds][0]}`
            : `Routes: ${n} selected`;
      }
    }
  }

  async function refreshSelectedRouteOverlay() {
    const run = ++routeOverlayRun;
    clearRouteOverlay();

    if (!selectedRouteIds.size) {
      currentRouteInfoId = null;
      map.flyTo({ center: GAINESVILLE.center, zoom: GAINESVILLE.zoom });
      return;
    }

    try {
      const details = await Promise.all([...selectedRouteIds].map(loadRouteDetail));
      if (run !== routeOverlayRun) return;
      drawRoutePolylines(details);
      drawRouteStops(details);
      fitToRoutes(details);
    } catch (err) {
      console.warn('[map] selected route overlay failed:', err);
    }
  }

  async function loadRouteDetail(routeId) {
    if (routeDetailCache.has(routeId)) return routeDetailCache.get(routeId);
    const detail = await fetchJSON(`/api/map/route/${encodeURIComponent(routeId)}`);
    routeDetailCache.set(routeId, detail);
    return detail;
  }

  function setRouteRailExpanded(expanded) {
    routeRailExpanded = Boolean(expanded);
    const rail = document.getElementById('map-route-rail');
    if (!rail) return;
    rail.classList.toggle('expanded', routeRailExpanded);
    const toggle = document.getElementById('map-route-toggle');
    if (toggle) {
      toggle.setAttribute('aria-expanded', routeRailExpanded ? 'true' : 'false');
      const icon = toggle.querySelector('.map-route-toggle-icon');
      if (icon) icon.textContent = routeRailExpanded ? '⌃' : '⌄';
    }
    syncMapControlOffset();
  }

  function collapseRouteRailFromMap() {
    if (!routeRailExpanded) return;
    setRouteRailExpanded(false);
    setRouteInfoReopenVisible(Boolean(currentRouteInfoId));
  }

  function syncMapControlOffset() {
    const panel = document.getElementById('map-panel');
    const controls = document.querySelector('.map-controls');
    const rail = document.getElementById('map-route-rail');
    if (!panel) return;
    const offset = (controls?.offsetHeight || 0) + (rail?.offsetHeight || 0) + 12;
    panel.style.setProperty('--map-controls-offset', `${offset}px`);
  }

  // ── Route polylines + stops (vector layers) ───────────────────────────────
  function drawRoutePolylines(details) {
    const features = details.flatMap(detail => (detail.shapes || []).map(s => ({
      type: 'Feature',
      properties: {
        route_id: detail.route_id,
        color: detail.color || colorForRoute(detail.route_id),
        direction: s.direction,
        headsign: s.headsign,
      },
      geometry: {
        type: 'LineString',
        coordinates: s.points.map(p => [p[1], p[0]]), // [lat,lon] → [lon,lat]
      },
    })));
    if (!features.length) return;
    if (map.getSource('route-line')) {
      map.getSource('route-line').setData({ type: 'FeatureCollection', features });
    } else {
      map.addSource('route-line', { type: 'geojson', data: { type: 'FeatureCollection', features } });
      map.addLayer({
        id: 'route-line-casing',
        type: 'line',
        source: 'route-line',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': '#ffffff',
          'line-width': 6,
          'line-opacity': 0.36,
        },
      });
      map.addLayer({
        id: 'route-line-layer',
        type: 'line',
        source: 'route-line',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 3,
          'line-opacity': 0.62,
        },
      });
    }
  }

  function drawRouteStops(details) {
    const seenStops = new Set();
    details.flatMap(detail => detail.stops || []).forEach(s => {
      if (s.lat == null || s.lon == null) return;
      const key = String(s.stop_id || `${s.lat},${s.lon}`);
      if (seenStops.has(key)) return;
      seenStops.add(key);
      const el = document.createElement('div');
      el.className = 'map-stop route-stop';
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      el.setAttribute('aria-label', `${s.stop_name}, Stop ${formatStopId(s.stop_id)}. Show arrivals.`);
      el.appendChild(buildStopTooltip(s));
      el.addEventListener('click', e => {
        e.stopPropagation();
        showStopSheet(s);
      });
      const marker = new maplibregl.Marker({ element: el }).setLngLat([s.lon, s.lat]).addTo(map);
      stopMarkers.push(marker);
    });
  }

  function buildStopTooltip(stop) {
    const tip = document.createElement('div');
    tip.className = 'map-stop-tooltip';
    tip.innerHTML = `
      <div class="map-stop-tooltip-name">${escHTML(stop.stop_name || 'Bus stop')}</div>
      <div class="map-stop-tooltip-meta">
        <span>Stop ID ${escHTML(formatStopId(stop.stop_id))}</span>
        <strong>Tap for arrivals</strong>
      </div>
    `;
    return tip;
  }

  function clearRouteOverlay() {
    stopMarkers.forEach(m => m.remove());
    stopMarkers = [];
    if (map.getLayer('route-line-casing')) map.removeLayer('route-line-casing');
    if (map.getLayer('route-line-layer')) map.removeLayer('route-line-layer');
    if (map.getSource('route-line'))      map.removeSource('route-line');
  }

  function fitToRoutes(details) {
    const all = details.flatMap(detail => (detail.shapes || []).flatMap(s => s.points));
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
      const filtered = selectedRouteIds.size > 0 && !selectedRouteIds.has(v.route);

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
        <div class="map-bus-number"></div>
        <div class="map-bus-windows"></div>
        <div class="map-bus-wheel left"></div>
        <div class="map-bus-wheel right"></div>
      </div>
    `;
    updateBusEl(el, v);
    return el;
  }

  function updateBusEl(el, v) {
    const color = colorForRoute(v.route);
    const routeLabel = String(v.route || '?');
    el.dataset.route = v.route || '';
    el.classList.toggle('long-route', routeLabel.length >= 3);
    const body = el.querySelector('.map-bus-body');
    const number = el.querySelector('.map-bus-number');
    if (body) body.style.background = color;
    if (number) number.textContent = routeLabel;
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
      el.style.display = (selectedRouteIds.size > 0 && !selectedRouteIds.has(route)) ? 'none' : '';
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
  async function showBusSheet(v) {
    const route = routes.find(r => r.route_id === v.route);
    const routeName = route ? `Route ${route.short_name} — ${route.long_name}` : `Route ${v.route}`;
    const headsign  = v.destination ? `Heading: ${escHTML(v.destination)}` : '';
    const delayed   = v.delayed ? '<span style="color:#fbbf24">⚠ Reported delayed</span>' : '';

    if (v.route) {
      await ensureRouteSelected(v.route);
      showRouteInfo(v.route);
    }

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

    setRouteRailExpanded(false);
    hideMapSheet();
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

  function hideRouteInfo(showReopen = true) {
    const panel = document.getElementById('map-route-info');
    if (panel) panel.classList.add('hidden');
    setRouteInfoScrollHintVisible(false);
    setRouteInfoReopenVisible(showReopen && Boolean(currentRouteInfoId));
  }

  function setRouteInfoReopenVisible(show) {
    const btn = document.getElementById('map-route-info-reopen');
    if (btn) btn.classList.toggle('hidden', !show || routeRailExpanded);
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
    hideRouteInfo(false);
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
    if (!preds.length) {
      try {
        const data = await fetchJSON(`/api/map/stop/${encodeURIComponent(stop.stop_id)}/schedule?limit=6`);
        scheduled = (data && data.departures) || [];
        scheduleServiceDay = (data && data.service_day_label) || '';
      } catch {
        scheduled = [];
        scheduleServiceDay = '';
      }
    }
    const askMsg = `What's coming up at stop ${stopIdLabel} (${stop.stop_name})?`;

    const fmtEta = m => {
      if (m == null || m === '') return '?';
      const s = String(m);
      if (s.toUpperCase() === 'DUE') return 'DUE';
      const n = parseInt(s, 10);
      return Number.isFinite(n) ? `${n} min` : s;
    };

    const arrivalHTML = preds.length
      ? `<div class="map-sheet-section">Live ETAs</div>` + preds.slice(0, 5).map(p => {
          const route = p.route || '';
          const dest  = p.destination || '';
          const eta   = fmtEta(p.minutes);
          const dly   = p.delayed ? ' ⚠' : '';
          return `<div class="pred-row"><span>Route ${escHTML(route)} → ${escHTML(dest)}${dly}</span><span>${escHTML(eta)}</span></div>`;
        }).join('')
      : scheduled.length
        ? `<div class="map-sheet-section">Next scheduled departure${scheduleServiceDay ? ` · ${escHTML(scheduleServiceDay)}` : ''}</div>` + scheduled.map(s => {
          const route = s.route || '';
          const dest  = s.headsign || '';
          const time  = s.time_label || s.time || '';
          return `<div class="pred-row"><span>Route ${escHTML(route)} → ${escHTML(dest)}</span><span>${escHTML(time)}</span></div>`;
        }).join('')
        : `<div class="map-sheet-section">Next scheduled departure</div><div class="pred-row"><span>No scheduled departures found soon.</span><span></span></div>`;

    renderSheet(`
      <h3>${escHTML(stop.stop_name)}</h3>
      <div class="map-sheet-stop-meta">
        <span class="map-stop-id-badge">Stop ID ${escHTML(stopIdLabel)}</span>
        <div class="meta">${preds.length ? 'Live arrivals available' : (scheduleServiceDay ? `Next service ${escHTML(scheduleServiceDay)}` : 'Scheduled fallback')}</div>
      </div>
      ${arrivalHTML}
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

  function hideMapSheet() {
    const sheet = document.getElementById('map-sheet');
    if (sheet) sheet.classList.add('hidden');
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
  window.retryMapLocation = () => centerOnUser();

  async function centerOnUser() {
    if (!navigator.geolocation) {
      renderSheet(`
        <h3>Location unavailable</h3>
        <div class="meta">This browser cannot share your location. Use the Stop ID field or tap a stop on the map.</div>
      `);
      return;
    }
    if (!window.isSecureContext) {
      renderSheet(`
        <h3>Location unavailable</h3>
        <div class="meta">Chrome only shares location on secure HTTPS pages. Open the live site with https, or use the Stop ID field.</div>
      `);
      return;
    }

    renderSheet(`
      <h3>Finding you…</h3>
      <div class="meta">Make sure location is allowed for this site or app.</div>
    `);

    try {
      const pos = await requestUserLocation({ enableHighAccuracy: true, timeout: 10000, maximumAge: 30_000 });
      onUserLocated(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy);
    } catch (err) {
      console.warn('[map] high-accuracy geolocation:', err.message);
      if (shouldRetryWithBalancedLocation(err)) {
        try {
          renderSheet(`
            <h3>Still looking…</h3>
            <div class="meta">Trying a lower-accuracy location fix that works better on laptops.</div>
          `);
          const pos = await requestUserLocation({ enableHighAccuracy: false, timeout: 15000, maximumAge: 300_000 });
          onUserLocated(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy);
          return;
        } catch (fallbackErr) {
          console.warn('[map] balanced geolocation:', fallbackErr.message);
          renderLocationFailure(fallbackErr);
          return;
        }
      }
      renderLocationFailure(err);
    }
  }

  function requestUserLocation(options) {
    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, reject, options);
    });
  }

  function shouldRetryWithBalancedLocation(err) {
    return err && (err.code === 2 || err.code === 3);
  }

  function renderLocationFailure(err) {
    renderSheet(`
      <h3>Couldn't find you</h3>
      <div class="meta">${escHTML(locationErrorMessage(err))}</div>
      <div class="map-sheet-actions">
        <button class="map-sheet-btn" onclick="window.retryMapLocation && window.retryMapLocation()">Try again</button>
        <button class="map-sheet-btn ghost" onclick="document.getElementById('map-stop-search-input')?.focus(); window.closeMapSheet && window.closeMapSheet()">Use Stop ID</button>
      </div>
    `);
  }

  function locationErrorMessage(err) {
    if (!err) return 'Location is unavailable right now. Try again, or use a Stop ID from the bus stop sign.';
    if (err.code === 1) return 'Location permission is blocked. Allow location for this site/app in your browser settings, then try again.';
    if (err.code === 2) return 'Your device could not get a GPS/location fix right now. Step near a window or try again in a moment.';
    if (err.code === 3) return 'Location took too long to respond. Try again, or use the Stop ID printed on the bus stop sign.';
    return err.message || 'Location is unavailable right now. Try again, or use a Stop ID from the bus stop sign.';
  }

  async function onUserLocated(lat, lon, accuracy) {
    placeUserMarker(lat, lon);
    map.flyTo({ center: [lon, lat], zoom: 15, duration: 700 });

    // Show a quick "looking nearby" sheet so the user gets immediate feedback
    // while we fetch nearby stops. This answers the "what happens once the
    // map gets my location?" question — the nearest stops + their predictions.
    renderSheet(`
      <h3>You're here</h3>
      <div class="meta">${accuracy ? `Accuracy ±${Math.round(accuracy)}m · ` : ''}Finding the closest bus stops…</div>
    `);

    let nearby = [];
    try {
      const data = await fetchJSON(`/api/map/nearby-stops?lat=${lat}&lon=${lon}&radius_m=500&limit=5`);
      nearby = data.stops || [];
    } catch (err) {
      console.warn('[map] nearby-stops failed:', err);
    }

    if (!nearby.length) {
      renderSheet(`
        <h3>You're here</h3>
        <div class="meta">No bus stops within ~500m. Try panning the map.</div>
      `);
      return;
    }

    const list = nearby.map(s => {
      const distLabel = s.distance_m != null
        ? `${Math.round(s.distance_m)} m · ~${Math.max(1, Math.round(s.walk_min || (s.distance_m / 80)))} min walk`
        : '';
      const stopArg = JSON.stringify({
        stop_id:   s.stop_id,
        stop_name: s.stop_name,
        lat:       s.lat,
        lon:       s.lon,
      }).replace(/"/g, '&quot;');
      return `
        <button class="map-nearby-row" onclick="window.openStopFromNearby(${stopArg})">
          <div class="map-nearby-name">${escHTML(s.stop_name)}</div>
          <div class="map-nearby-meta">Stop ${escHTML(formatStopId(s.stop_id))} · ${escHTML(distLabel)}</div>
        </button>
      `;
    }).join('');

    renderSheet(`
      <h3>You're here</h3>
      <div class="meta">${nearby.length} stop${nearby.length === 1 ? '' : 's'} within ~500m. Tap one for predictions.</div>
      <div class="map-nearby-list">${list}</div>
    `);
  }

  function placeUserMarker(lat, lon) {
    if (userMarker) {
      userMarker.setLngLat([lon, lat]);
      return;
    }
    const el = document.createElement('div');
    el.className = 'map-user-pin';
    el.title = 'You are here';
    userMarker = new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
  }

  // Bridge from the nearby-stops list back into the existing stop sheet flow.
  window.openStopFromNearby = function(stop) {
    if (!stop) return;
    if (stop.lat != null && stop.lon != null) {
      map.flyTo({ center: [stop.lon, stop.lat], zoom: 16, duration: 500 });
    }
    showStopSheet(stop);
  };

  // ── Stop-ID search ────────────────────────────────────────────────────────
  async function onStopSearchSubmit(e) {
    e.preventDefault();
    const input = document.getElementById('map-stop-search-input');
    if (!input) return;
    const raw = (input.value || '').trim();
    if (!raw) return;
    hideRouteInfo(false);
    const digits = raw.replace(/\D/g, '');
    if (!digits) {
      input.classList.add('is-invalid');
      renderSheet(`<h3>Enter a stop ID</h3><div class="meta">Stop IDs are printed on bus stop signs (e.g. 0773).</div>`);
      return;
    }
    input.classList.remove('is-invalid');

    renderSheet(`<h3>Looking up stop ${escHTML(formatStopId(digits))}…</h3><div class="meta">One moment.</div>`);

    // The schedule endpoint resolves the stop, returns its lat/lon, AND gives
    // us the next scheduled departures (rolled forward into tomorrow / next
    // service day if today's are exhausted). The bottom sheet then layers live
    // ETAs on top via /api/predictions — same flow as tapping a stop on the
    // map. Mirrors the chat agent's "ETA if available, else next schedule" UX.
    let stop;
    try {
      stop = await fetchJSON(`/api/map/stop/${encodeURIComponent(digits)}/schedule`);
    } catch (err) {
      input.classList.add('is-invalid');
      renderSheet(`
        <h3>Stop ${escHTML(formatStopId(digits))} not found</h3>
        <div class="meta">Check the number on the bus stop sign and try again.</div>
      `);
      return;
    }
    if (!stop || stop.lat == null || stop.lon == null) {
      input.classList.add('is-invalid');
      renderSheet(`
        <h3>Stop ${escHTML(formatStopId(digits))} not found</h3>
        <div class="meta">Check the number on the bus stop sign and try again.</div>
      `);
      return;
    }

    map.flyTo({ center: [stop.lon, stop.lat], zoom: 16, duration: 600 });
    showStopSheet({
      stop_id:   stop.stop_id,
      stop_name: stop.stop_name,
      lat:       stop.lat,
      lon:       stop.lon,
    });
    input.classList.remove('is-invalid');
    input.blur();
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
