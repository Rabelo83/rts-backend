/**
 * schedules.js — Rider-facing Schedules tab
 *
 * Lazy-initialised when the user first opens the Schedules tab.
 * Exposes window.initSchedules() which is called by switchTab() in
 * trip_planner.js. All other state is private to this IIFE.
 */
(function () {
  'use strict';

  /* ── State ────────────────────────────────────────────────────────────── */
  let _initialized   = false;
  let _routes        = [];       // [{route_id, short_name, long_name, color}]
  let _selectedRoute = null;     // short_name string ("1", "10", …)
  let _selectedSvc   = 'weekday';
  let _selectedDir   = null;

  let _routesReadyResolve;
  const _routesReady = new Promise(res => { _routesReadyResolve = res; });

  const SVC_LABELS = {
    weekday:  'Weekday',
    saturday: 'Saturday',
    sunday:   'Sunday',
    reduced:  'Reduced',
  };

  /* ── Utilities ────────────────────────────────────────────────────────── */
  function el(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /* Extract the destination from "Origin to Destination" route names.
     Shows "Butler Plaza" instead of "Downtown Station to Butler Plaza". */
  function _routeDest(longName) {
    if (!longName) return '';
    const m = longName.match(/\bto\s+(.+)$/i);
    return m ? m[1].trim() : longName;
  }

  /* ── Public entry point ───────────────────────────────────────────────── */
  window.initSchedules = async function initSchedules() {
    if (_initialized) return;
    _initialized = true;

    const panel = el('schedule-panel');
    if (!panel) return;

    panel.innerHTML = `
      <div class="sched-picker">
        <button class="sched-route-trigger" id="sched-route-trigger"
                aria-haspopup="listbox" aria-expanded="false">
          <span class="sched-trigger-content" id="sched-trigger-content">
            <span class="sched-trigger-ph">Choose a route to see its schedule</span>
          </span>
          <svg class="sched-trigger-chevron" width="18" height="18" viewBox="0 0 24 24"
               fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
            <path d="m6 9 6 6 6-6"/>
          </svg>
        </button>
        <div class="sched-route-dropdown" id="sched-route-dropdown">
          <div class="sched-search-wrap">
            <svg class="sched-search-icon" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2" aria-hidden="true">
              <circle cx="11" cy="11" r="7"/><path d="m16 16 4 4"/>
            </svg>
            <input id="sched-route-input" class="sched-search-input"
                   type="text" placeholder="Search routes…" autocomplete="off"
                   aria-label="Search routes" />
          </div>
          <div id="sched-route-list" class="sched-route-list" role="listbox">
            <div class="sched-state-msg">Loading routes…</div>
          </div>
        </div>
      </div>
      <div id="sched-detail"></div>
    `;

    el('sched-route-trigger').addEventListener('click', e => {
      e.stopPropagation();
      _toggleDropdown();
    });
    el('sched-route-input').addEventListener('input', _onSearch);
    document.addEventListener('click', _onDocClick);

    try {
      const data = await fetchJSON('/api/map/routes');
      _routes = (data.routes || []).slice().sort((a, b) => {
        const na = parseInt(a.short_name, 10) || 9999;
        const nb = parseInt(b.short_name, 10) || 9999;
        return na !== nb ? na - nb : a.short_name.localeCompare(b.short_name);
      });
      _renderRouteList(_routes);
      _routesReadyResolve();
    } catch (_e) {
      el('sched-route-list').innerHTML =
        '<div class="sched-state-msg sched-error">Could not load routes. Please try again.</div>';
    }
  };

  /* ── Dropdown helpers ─────────────────────────────────────────────────── */
  function _toggleDropdown() {
    const dd  = el('sched-route-dropdown');
    const btn = el('sched-route-trigger');
    if (!dd) return;
    const opening = !dd.classList.contains('open');
    dd.classList.toggle('open', opening);
    btn.setAttribute('aria-expanded', String(opening));
    btn.classList.toggle('open', opening);
    if (opening) {
      const inp = el('sched-route-input');
      if (inp) { inp.value = ''; _renderRouteList(_routes); inp.focus(); }
    }
  }

  function _closeDropdown() {
    const dd  = el('sched-route-dropdown');
    const btn = el('sched-route-trigger');
    if (dd)  dd.classList.remove('open');
    if (btn) { btn.setAttribute('aria-expanded', 'false'); btn.classList.remove('open'); }
  }

  function _onDocClick(e) {
    const picker = document.querySelector('.sched-picker');
    if (!picker || picker.contains(e.target)) return;
    _closeDropdown();
  }

  function _updateTrigger() {
    const content = el('sched-trigger-content');
    if (!content) return;
    if (!_selectedRoute) {
      content.innerHTML = '<span class="sched-trigger-ph">Choose a route to see its schedule</span>';
      return;
    }
    const r = _routes.find(r => r.short_name === _selectedRoute);
    if (!r) return;
    const color = esc(r.color || '#3b82f6');
    content.innerHTML = `
      <span class="sched-trigger-badge" style="background:${color}">${esc(r.short_name)}</span>
      <span class="sched-trigger-name">Route ${esc(r.short_name)} — ${esc(_routeDest(r.long_name))}</span>
    `;
  }

  /* ── Route list (inside dropdown) ────────────────────────────────────── */
  function _onSearch() {
    const q = (el('sched-route-input').value || '').trim().toLowerCase();
    const filtered = q
      ? _routes.filter(r =>
          r.short_name.toLowerCase().includes(q) ||
          (r.long_name || '').toLowerCase().includes(q))
      : _routes;
    _renderRouteList(filtered);
  }

  function _renderRouteList(routes) {
    const list = el('sched-route-list');
    if (!list) return;
    if (!routes.length) {
      list.innerHTML = '<div class="sched-state-msg">No routes match your search.</div>';
      return;
    }
    list.innerHTML = routes.map(r => {
      const active = _selectedRoute === r.short_name;
      const color  = esc(r.color || '#3b82f6');
      const dest   = esc(_routeDest(r.long_name));
      return `
        <button class="sched-route-chip${active ? ' active' : ''}"
                role="option"
                aria-selected="${active}"
                data-route="${esc(r.short_name)}"
                style="--chip-color:${color}"
                onclick="window._schedSelectRoute(${JSON.stringify(r.short_name).replace(/"/g, '&quot;')})">
          <span class="sched-chip-num">${esc(r.short_name)}</span>
          <span class="sched-chip-name">
            <span class="sched-chip-dest">${dest}</span>
            <span class="sched-chip-full">${esc(r.long_name || '')}</span>
          </span>
        </button>`;
    }).join('');
  }

  /* ── Route selection ──────────────────────────────────────────────────── */
  window._schedSelectRoute = async function _schedSelectRoute(routeId) {
    _selectedRoute = routeId;
    _selectedDir   = null;
    _selectedSvc   = 'weekday';
    _closeDropdown();
    _updateTrigger();
    await _loadTimetable(routeId, _selectedSvc, null);
  };

  /* Called from the Live Map "View route schedule" button — switches to this
     tab and pre-selects the given route once the route list is ready. */
  window.openScheduleForRoute = async function openScheduleForRoute(routeId) {
    await _routesReady;
    await window._schedSelectRoute(routeId);
  };

  /* ── Load timetable ───────────────────────────────────────────────────── */
  async function _loadTimetable(routeId, svcType, direction) {
    const detail = el('sched-detail');
    if (!detail) return;
    detail.innerHTML = '<div class="sched-state-msg">Loading schedule…<span class="sched-spinner"></span></div>';

    let url = `/api/schedule/route/${encodeURIComponent(routeId)}/timetable` +
              `?service=${encodeURIComponent(svcType)}`;
    if (direction) url += `&direction=${encodeURIComponent(direction)}`;

    let data;
    try {
      data = await fetchJSON(url);
    } catch (_e) {
      detail.innerHTML =
        '<div class="sched-state-msg sched-error">Could not load schedule. Please try again.</div>';
      return;
    }

    if (data.error) {
      detail.innerHTML = '<div class="sched-state-msg sched-error">Route not found.</div>';
      return;
    }

    _selectedSvc = svcType;
    _selectedDir = data.direction || '';

    const routeObj  = _routes.find(r => r.short_name === routeId) || {};
    const chipColor = esc(routeObj.color || '#3b82f6');

    /* Service day buttons */
    const svcBtns = (data.available_service_types || [])
      .map(st => `
        <button class="sched-svc-btn${st === _selectedSvc ? ' active' : ''}"
                onclick="window._schedSetSvc(${JSON.stringify(st).replace(/"/g, '&quot;')})">
          ${esc(SVC_LABELS[st] || st)}
        </button>`
      ).join('');

    /* Direction buttons — label comes from server */
    const dirs = data.directions || [];
    const dirBtns = dirs.map(d => {
      const headsign = typeof d === 'object' ? (d.headsign || '') : String(d);
      const dirLabel = typeof d === 'object' ? (d.label || null) : null;
      const tagCls   = dirLabel ? dirLabel.toLowerCase() : '';
      const badgeHtml = dirLabel
        ? `<span class="sched-dir-badge sched-dir-badge--${tagCls}">${dirLabel}</span>`
        : '';
      return `
        <button class="sched-dir-btn${headsign === _selectedDir ? ' active' : ''}"
                onclick="window._schedSetDir(${JSON.stringify(headsign).replace(/"/g, '&quot;')})">
          ${badgeHtml}<span class="sched-dir-headsign">${esc(headsign)}</span>
        </button>`;
    }).join('');

    detail.innerHTML = `
      <div class="sched-detail-header">
        <div class="sched-route-badge" style="--chip-color:${chipColor}">
          <span class="sched-badge-num">Route ${esc(routeId)}</span>
          <span class="sched-badge-name">${esc(data.route_name || '')}</span>
        </div>
      </div>
      ${svcBtns ? `<div class="sched-svc-row" role="group" aria-label="Service day">${svcBtns}</div>` : ''}
      ${dirBtns ? `<div class="sched-dir-row" role="group" aria-label="Direction">${dirBtns}</div>` : ''}
      <div id="sched-grid-area"></div>
    `;

    _renderGrid(data, el('sched-grid-area'));
  }

  /* ── Service type / direction switching ───────────────────────────────── */
  window._schedSetSvc = async function _schedSetSvc(svcType) {
    if (!_selectedRoute) return;
    _selectedSvc = svcType;
    _selectedDir = null;
    await _loadTimetable(_selectedRoute, svcType, null);
  };

  window._schedSetDir = async function _schedSetDir(direction) {
    if (!_selectedRoute) return;
    await _loadTimetable(_selectedRoute, _selectedSvc, direction);
  };

  /* ── Timetable grid ───────────────────────────────────────────────────── */
  function _renderGrid(data, wrap) {
    if (!wrap) return;
    const stops = data.stops || [];
    const rows  = data.rows  || [];

    if (!stops.length || !rows.length) {
      wrap.innerHTML = `
        <div class="sched-no-service">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="1.5">
            <circle cx="12" cy="12" r="9"/><path d="M12 8v4m0 4h.01"/>
          </svg>
          <p>No service scheduled for this day and direction.</p>
        </div>`;
      return;
    }

    /* First column is the origin stop — make it sticky so departure time
       stays visible as the user scrolls right through intermediate stops. */
    const headerCells = stops.map((s, i) => {
      const sticky = i === 0 ? ' sched-col-sticky' : '';
      return `
        <th class="sched-th${sticky}" title="${esc(s.stop_name)} (ID: ${esc(s.stop_id)})">
          <span class="sched-th-text">${esc(s.stop_name)}</span>
          <span class="sched-th-id">ID: ${esc(s.stop_id)}</span>
        </th>`;
    }).join('');

    const dataRows = rows.map(row => {
      const cells = row.times.map((t, i) => {
        const sticky = i === 0 ? ' sched-col-sticky' : '';
        if (t != null) return `<td class="sched-td${sticky}">${esc(t)}</td>`;
        return `<td class="sched-td sched-td-null${sticky}" aria-label="Does not stop here">—</td>`;
      }).join('');
      return `<tr>${cells}</tr>`;
    }).join('');

    wrap.innerHTML = `
      <div class="sched-scroll-hint" aria-hidden="true">← Origin fixed · Scroll right for more stops →</div>
      <div class="sched-table-wrap">
        <table class="sched-table" role="grid" aria-label="Route timetable">
          <thead><tr>${headerCells}</tr></thead>
          <tbody>${dataRows}</tbody>
        </table>
      </div>
      <p class="sched-table-note">
        Scheduled times from key stops · ${esc(rows.length)} trip${rows.length !== 1 ? 's' : ''}
      </p>
    `;
  }

}());
