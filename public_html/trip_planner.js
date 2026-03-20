/**
 * trip_planner.js  v2
 * Trip Planner — time modes, smart ranking display, ft/mi, ETA badge.
 */

/* ── Tab switching ────────────────────────────────────────────────────── */

window.switchTab = function switchTab(tab) {
  const chatEls = ['chat-messages', 'starter-questions', 'chat-end-row', 'chat-input-row'];
  const tripEl  = document.getElementById('trip-panel');
  const isChat  = tab === 'chat';

  chatEls.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('hidden', !isChat);
  });

  if (tripEl) tripEl.classList.toggle('hidden', isChat);

  document.querySelectorAll('.tab-btn').forEach(btn => {
    const active = btn.dataset.tab === tab;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active);
  });
};


/* ── Init ─────────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  const panel = document.getElementById('trip-panel');
  if (!panel) return;
  panel.innerHTML = buildFormHTML();
  initAutocomplete('origin-input', 'origin-ac');
  initAutocomplete('dest-input',   'dest-ac');
  document.getElementById('trip-form').addEventListener('submit', onSubmit);
  initTimeModeToggle();
});


/* ── Form HTML ────────────────────────────────────────────────────────── */

function buildFormHTML() {
  return `
    <form id="trip-form" class="trip-form" autocomplete="off" onsubmit="return false">

      <div class="trip-field">
        <label for="origin-input">From</label>
        <input id="origin-input" class="trip-input" type="text"
               placeholder="Starting address or place…" autocomplete="off" />
        <div id="origin-ac" class="ac-dropdown hidden"></div>
      </div>

      <div class="trip-field">
        <label for="dest-input">To</label>
        <input id="dest-input" class="trip-input" type="text"
               placeholder="Destination address or place…" autocomplete="off" />
        <div id="dest-ac" class="ac-dropdown hidden"></div>
      </div>

      <!-- Time mode toggle -->
      <div class="time-mode-row">
        <button type="button" class="time-mode-btn active" data-mode="now">Leave Now</button>
        <button type="button" class="time-mode-btn" data-mode="depart">Departing At</button>
        <button type="button" class="time-mode-btn" data-mode="arrive">Arriving At</button>
      </div>
      <div id="time-picker-row" class="hidden trip-datetime-row">
        <input id="trip-date" class="trip-input trip-date-input" type="date" />
        <input id="trip-time" class="trip-input trip-time-input" type="time" />
      </div>

      <button type="submit" class="trip-submit" id="trip-submit">Find Routes</button>
    </form>

    <div id="trip-results"></div>
  `;
}


/* ── Time mode toggle ─────────────────────────────────────────────────── */

function initTimeModeToggle() {
  document.querySelectorAll('.time-mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.time-mode-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const picker = document.getElementById('time-picker-row');
      if (btn.dataset.mode === 'now') {
        picker.classList.add('hidden');
      } else {
        picker.classList.remove('hidden');
        // Default to current time
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, '0');
        const mm = String(now.getMinutes()).padStart(2, '0');
        document.getElementById('trip-time').value = `${hh}:${mm}`;
        // Default date to today
        const yyyy = now.getFullYear();
        const mo = String(now.getMonth() + 1).padStart(2, '0');
        const dd = String(now.getDate()).padStart(2, '0');
        document.getElementById('trip-date').value = `${yyyy}-${mo}-${dd}`;
      }
    });
  });
}

function getActiveMode() {
  const btn = document.querySelector('.time-mode-btn.active');
  return btn ? btn.dataset.mode : 'now';
}


/* ── Autocomplete ─────────────────────────────────────────────────────── */

const _acState = {};

function initAutocomplete(inputId, dropdownId) {
  const input = document.getElementById(inputId);
  const drop  = document.getElementById(dropdownId);
  if (!input || !drop) return;

  let timer = null;

  input.addEventListener('input', () => {
    const q = input.value.trim();
    _acState[inputId] = null;
    clearTimeout(timer);
    if (q.length < 3) { drop.classList.add('hidden'); drop.innerHTML = ''; return; }
    timer = setTimeout(() => fetchSuggestions(q, input, drop, inputId), 280);
  });

  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !drop.contains(e.target)) {
      drop.classList.add('hidden');
    }
  });
}

async function fetchSuggestions(q, input, drop, inputId) {
  try {
    const res  = await fetch(`/api/geocode/autocomplete?q=${encodeURIComponent(q)}`);
    const list = await res.json();
    if (!list.length) { drop.classList.add('hidden'); drop.innerHTML = ''; return; }

    drop.innerHTML = list.map(item =>
      `<div class="ac-item" data-lat="${item.lat}" data-lon="${item.lon}"
            data-display="${escHtml(item.display)}">${escHtml(item.display)}</div>`
    ).join('');
    drop.classList.remove('hidden');

    drop.querySelectorAll('.ac-item').forEach(el => {
      el.addEventListener('click', () => {
        input.value = el.dataset.display;
        _acState[inputId] = { lat: parseFloat(el.dataset.lat), lon: parseFloat(el.dataset.lon), display: el.dataset.display };
        drop.classList.add('hidden');
        drop.innerHTML = '';
      });
    });
  } catch {
    drop.classList.add('hidden');
  }
}


/* ── Form submit ──────────────────────────────────────────────────────── */

async function onSubmit(e) {
  e.preventDefault();
  const btn     = document.getElementById('trip-submit');
  const results = document.getElementById('trip-results');

  const originVal = (document.getElementById('origin-input').value || '').trim();
  const destVal   = (document.getElementById('dest-input').value   || '').trim();

  if (!originVal || !destVal) {
    results.innerHTML = '<div class="trip-error">Please enter both a starting point and destination.</div>';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Searching…';
  results.innerHTML = '';

  const body = {};

  const os = _acState['origin-input'];
  const ds = _acState['dest-input'];
  if (os && os.display === originVal) { body.origin_lat = os.lat; body.origin_lon = os.lon; }
  else body.origin_address = originVal;
  if (ds && ds.display === destVal)   { body.dest_lat = ds.lat;   body.dest_lon = ds.lon; }
  else body.dest_address = destVal;

  const mode    = getActiveMode();
  const timeVal = (document.getElementById('trip-time') || {}).value || '';
  const dateVal = (document.getElementById('trip-date') || {}).value || '';
  if (mode === 'depart' && timeVal) body.depart_after = timeVal;
  if (mode === 'arrive' && timeVal) body.arrive_by    = timeVal;
  if (dateVal) body.date = dateVal;

  try {
    const res  = await fetch('/api/trip/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (data.error) {
      results.innerHTML = `<div class="trip-error">${escHtml(data.error)}</div>`;
    } else if (!data.itineraries || !data.itineraries.length) {
      results.innerHTML = '<div class="trip-error">No routes found between these locations right now.</div>';
    } else {
      renderResults(data, results);
    }
  } catch {
    results.innerHTML = '<div class="trip-error">Could not connect to the server. Please try again.</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Find Routes';
  }
}


/* ── Results rendering ────────────────────────────────────────────────── */

function renderResults(data, container) {
  const nowMin  = nowMinutes();
  const isArrive = data.mode === 'arrive';

  let html = '';

  // Reduced Service banner
  const svc = data.service_label || '';
  if (svc && svc !== 'Weekday') {
    html += `<div class="trip-service-banner">
      &#x26A0;&#xFE0F; RTS is on <strong>${escHtml(svc)}</strong> — fewer trips may be available.
    </div>`;
  }

  html += `<div class="trip-results-label">
    ${data.itineraries.length} option${data.itineraries.length > 1 ? 's' : ''} found
    ${isArrive ? ' &nbsp;<span style="opacity:.5;font-size:.7rem">arriving by selected time</span>' : ''}
  </div>`;

  data.itineraries.forEach(itin => {
    const buses      = itin.legs.filter(l => l.type === 'bus');
    const xfers      = itin.legs.filter(l => l.type === 'transfer');
    const firstBus   = buses[0];
    const lastBus    = buses[buses.length - 1];
    const totalMin   = Math.round(itin.total_min);
    const isTransfer = itin.type === 'transfer';

    // ETA badge: minutes until first departure
    let etaHtml = '';
    if (firstBus && !isArrive) {
      const diffMin = firstBus.depart_min - nowMin;
      if (diffMin >= 0 && diffMin <= 45) {
        const rtDot = firstBus.realtime ? '<span class="rt-dot-inline"></span>' : '';
        const label = diffMin === 0 ? 'DUE' : `in ${diffMin} min`;
        etaHtml = `<span class="eta-badge">${rtDot}${label}</span>`;
      }
    }

    html += `<div class="itin-card">
      <div class="itin-header">
        <div class="itin-badges">
          ${buses.map(l => `<span class="badge badge-route">${escHtml(l.route)}</span>`).join('')}
          ${isTransfer ? '<span class="badge badge-transfer">1 transfer</span>' : ''}
          ${itin.realtime ? '<span class="badge badge-realtime"><span class="rt-dot"></span>Live</span>' : ''}
          ${isTransfer && itin.same_side ? '<span class="badge badge-same-side">Same side</span>' : ''}
        </div>
        <div class="itin-time-col">
          <span class="itin-total">${totalMin} min</span>
          ${etaHtml}
          ${firstBus ? `<span class="itin-dep-arr">${isArrive ? 'arr ' + escHtml(lastBus.arrive) : 'dep ' + escHtml(firstBus.depart)}</span>` : ''}
        </div>
      </div>
      <div class="itin-legs">
        ${renderLegs(itin)}
      </div>
    </div>`;
  });

  container.innerHTML = html;
}

function renderLegs(itin) {
  let html = '';

  if (itin.walk_to_stop && itin.walk_to_stop.walk_min > 0) {
    html += `<div class="leg-walk">
      ${walkIcon()}
      <span>Walk ${fmtDist(itin.walk_to_stop.distance_m)} to <strong>${escHtml(itin.walk_to_stop.stop_name)}</strong></span>
      <span class="leg-walk-time">${itin.walk_to_stop.walk_min} min</span>
    </div>`;
  }

  itin.legs.forEach(leg => {
    if (leg.type === 'bus') {
      const rtDot = leg.realtime ? '<span class="rt-dot-inline"></span>' : '';
      html += `<div class="leg-bus">
        <div class="leg-bus-route">${escHtml(leg.route)}</div>
        <div class="leg-bus-info">
          <div class="leg-bus-headsign">${escHtml(leg.headsign || leg.route_name || '')}</div>
          <div class="leg-bus-times">${rtDot}${escHtml(leg.depart)} &rarr; ${escHtml(leg.arrive)} &nbsp;<span class="leg-ride-min">${leg.ride_min} min</span></div>
        </div>
      </div>`;
    } else if (leg.type === 'transfer') {
      const shelter   = leg.has_shelter ? ' &#9924;' : '';
      const sideLabel = leg.same_side ? 'Stay on same side' : '&#x26A0; Cross street';
      html += `<div class="leg-transfer">
        ${transferIcon()}
        <span>Transfer at <strong>${escHtml(leg.at_stop_name)}</strong>${shelter} &mdash; ${leg.wait_min} min wait</span>
        <span class="leg-side-note">${sideLabel}</span>
      </div>`;
    }
  });

  if (itin.walk_from_stop && itin.walk_from_stop.walk_min > 0) {
    html += `<div class="leg-walk">
      ${walkIcon()}
      <span>Walk ${fmtDist(itin.walk_from_stop.distance_m)} from <strong>${escHtml(itin.walk_from_stop.stop_name)}</strong></span>
      <span class="leg-walk-time">${itin.walk_from_stop.walk_min} min</span>
    </div>`;
  }

  return html;
}


/* ── Helpers ──────────────────────────────────────────────────────────── */

function nowMinutes() {
  const n = new Date();
  return n.getHours() * 60 + n.getMinutes();
}

function fmtDist(meters) {
  const feet = Math.round(meters * 3.28084);
  if (feet < 500) return `${feet} ft`;
  return `${(meters * 0.000621371).toFixed(1)} mi`;
}

function walkIcon() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="flex-shrink:0;opacity:.6">
    <path d="M13.5 5.5c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zM9.8 8.9L7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3C14.8 12 16.8 13 19 13v-2c-1.9 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1L6 8.3V13h2V9.6l1.8-.7z"/>
  </svg>`;
}

function transferIcon() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="flex-shrink:0;opacity:.5">
    <path d="M16 17.01V10h-2v7.01h-3L15 21l4-3.99h-3zM9 3L5 6.99h3V14h2V6.99h3L9 3z"/>
  </svg>`;
}

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
