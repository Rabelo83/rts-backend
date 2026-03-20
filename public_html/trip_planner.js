/**
 * trip_planner.js  v1
 * Trip Planner tab — autocomplete address inputs, trip results rendering.
 */

/* ── Tab switching (shared with chat) ─────────────────────────────────── */

window.switchTab = function switchTab(tab) {
  const chatEls  = ['chat-messages', 'starter-questions', 'chat-end-row', 'chat-input-row'];
  const tripEl   = document.getElementById('trip-panel');
  const isChat   = tab === 'chat';

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


/* ── Trip planner init ────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  const panel = document.getElementById('trip-panel');
  if (!panel) return;
  panel.innerHTML = buildFormHTML();
  initAutocomplete('origin-input', 'origin-ac');
  initAutocomplete('dest-input',   'dest-ac');
  document.getElementById('trip-form').addEventListener('submit', onSubmit);
});


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

      <button type="submit" class="trip-submit" id="trip-submit">Find Routes</button>
    </form>

    <div id="trip-results"></div>
  `;
}


/* ── Autocomplete ─────────────────────────────────────────────────────── */

const _acState = {};   // inputId → { lat, lon, display }

function initAutocomplete(inputId, dropdownId) {
  const input = document.getElementById(inputId);
  const drop  = document.getElementById(dropdownId);
  if (!input || !drop) return;

  let debounceTimer = null;

  input.addEventListener('input', () => {
    const q = input.value.trim();
    _acState[inputId] = null;   // clear resolved coords on edit
    clearTimeout(debounceTimer);
    if (q.length < 3) { drop.classList.add('hidden'); drop.innerHTML = ''; return; }
    debounceTimer = setTimeout(() => fetchSuggestions(q, input, drop, inputId), 280);
  });

  // Close dropdown when clicking outside
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

    drop.innerHTML = list.map((item, i) =>
      `<div class="ac-item" data-i="${i}" data-lat="${item.lat}" data-lon="${item.lon}"
            data-display="${escHtml(item.display)}">${escHtml(item.display)}</div>`
    ).join('');
    drop.classList.remove('hidden');

    drop.querySelectorAll('.ac-item').forEach(el => {
      el.addEventListener('click', () => {
        input.value = el.dataset.display;
        _acState[inputId] = {
          lat:     parseFloat(el.dataset.lat),
          lon:     parseFloat(el.dataset.lon),
          display: el.dataset.display,
        };
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

  const originState = _acState['origin-input'];
  const destState   = _acState['dest-input'];

  if (originState && originState.display === originVal) {
    body.origin_lat = originState.lat;
    body.origin_lon = originState.lon;
  } else {
    body.origin_address = originVal;
  }

  if (destState && destState.display === destVal) {
    body.dest_lat = destState.lat;
    body.dest_lon = destState.lon;
  } else {
    body.dest_address = destVal;
  }

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
  const hasRealtime = data.itineraries.some(it => it.realtime);

  let html = `<div class="trip-results-label">
    ${data.itineraries.length} option${data.itineraries.length > 1 ? 's' : ''} found
    ${hasRealtime ? ' &nbsp;<span class="badge badge-realtime" style="vertical-align:middle"><span class="live-dot" style="display:inline-block;width:5px;height:5px;border-radius:50%;background:#34d399"></span> Live</span>' : ''}
  </div>`;

  data.itineraries.forEach((itin, idx) => {
    const firstBus = itin.legs.find(l => l.type === 'bus');
    const lastBus  = [...itin.legs].reverse().find(l => l.type === 'bus');
    const routes   = itin.legs.filter(l => l.type === 'bus').map(l => l.route);
    const isTransfer = itin.type === 'transfer';
    const totalMin = Math.round(itin.total_min);

    html += `<div class="itin-card">
      <div class="itin-header">
        <div class="itin-badges">
          ${routes.map(r => `<span class="badge badge-route">${escHtml(r)}</span>`).join('')}
          ${isTransfer ? '<span class="badge badge-transfer">1 transfer</span>' : ''}
          ${itin.realtime ? '<span class="badge badge-realtime"><span class="rt-dot"></span>Live</span>' : ''}
          ${isTransfer && itin.same_side ? '<span class="badge badge-same-side">Same side</span>' : ''}
        </div>
        <div class="itin-time">
          ${totalMin} min
          ${firstBus ? `<br><span>${firstBus.depart}</span>` : ''}
        </div>
      </div>
      <div class="itin-legs">
        ${renderLegs(itin, data)}
      </div>
    </div>`;
  });

  container.innerHTML = html;
}

function renderLegs(itin, data) {
  let html = '';

  // Walk to first stop
  if (itin.walk_to_stop && itin.walk_to_stop.walk_min > 0) {
    html += `<div class="leg-walk">
      ${walkIcon()}
      <span>Walk ${itin.walk_to_stop.walk_min} min to <strong>${escHtml(itin.walk_to_stop.stop_name)}</strong> (${itin.walk_to_stop.distance_m}m)</span>
    </div>`;
  }

  itin.legs.forEach(leg => {
    if (leg.type === 'bus') {
      const rtDot = leg.realtime ? '<span class="rt-dot"></span>' : '';
      html += `<div class="leg-bus">
        <div class="leg-bus-route">${escHtml(leg.route)}</div>
        <div class="leg-bus-info">
          <div class="leg-bus-headsign">${escHtml(leg.headsign || leg.route_name || '')}</div>
          <div class="leg-bus-times">${rtDot}${escHtml(leg.depart)} &rarr; ${escHtml(leg.arrive)} (${leg.ride_min} min)</div>
        </div>
      </div>`;
    } else if (leg.type === 'transfer') {
      const shelter = leg.has_shelter
        ? '<span class="shelter-icon" title="Shelter available">&#9924;</span>' : '';
      const sideNote = leg.same_side ? 'Stay on same side' : 'Cross the street';
      html += `<div class="leg-transfer">
        ${transferIcon()}
        <span>Transfer at <strong>${escHtml(leg.at_stop_name)}</strong> — wait ${leg.wait_min} min${shelter}</span>
        <span style="margin-left:auto;font-size:0.70rem;color:#94a3b8">${sideNote}</span>
      </div>`;
    }
  });

  // Walk from last stop
  if (itin.walk_from_stop && itin.walk_from_stop.walk_min > 0) {
    html += `<div class="leg-walk">
      ${walkIcon()}
      <span>Walk ${itin.walk_from_stop.walk_min} min from <strong>${escHtml(itin.walk_from_stop.stop_name)}</strong> (${itin.walk_from_stop.distance_m}m)</span>
    </div>`;
  }

  return html;
}


/* ── Inline SVG icons ─────────────────────────────────────────────────── */

function walkIcon() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
    <path d="M13.5 5.5c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zM9.8 8.9L7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3C14.8 12 16.8 13 19 13v-2c-1.9 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1L6 8.3V13h2V9.6l1.8-.7z"/>
  </svg>`;
}

function transferIcon() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
    <path d="M16 17.01V10h-2v7.01h-3L15 21l4-3.99h-3zM9 3L5 6.99h3V14h2V6.99h3L9 3z"/>
  </svg>`;
}


/* ── Utility ──────────────────────────────────────────────────────────── */

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
