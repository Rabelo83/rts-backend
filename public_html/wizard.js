// RTS Wizard-only UI (chat disabled - deployment phase)
const WIZ = {
  base: '',
  etaThreshold: 45,
  state: {
    intent: null, // eta | schedule
    serviceDay: null, // weekdays | weekends
    knowsStop: null,
    stopId: null,
    route: null,
    direction: null,
    stop: null,
    timeframe: null,
  },
  stack: [],
};

function w(id){ return document.getElementById(id); }

function resetWizard(){
  WIZ.state = {
    intent: null,
    serviceDay: null,
    knowsStop: null,
    stopId: null,
    route: null,
    direction: null,
    stop: null,
    timeframe: null,
  };
  WIZ.stack = [];
  renderStepIntent();
  setOutput('');
}

function setOutput(html){
  const out = w('wizard-output');
  if(out){ out.innerHTML = html || ''; }
}

function pushStep(step){
  WIZ.stack.push(step);
}

function goBack(){
  if(!WIZ.stack.length){
    renderStepIntent();
    return;
  }
  const prev = WIZ.stack.pop();
  prev();
}

function renderStepIntent(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>What do you need?</h4>
      <div class="wizard-grid">
        <button class="wizard-btn" data-intent="eta">When is my next bus?</button>
        <button class="wizard-btn" data-intent="schedule">View bus schedule</button>
      </div>
    </div>
  `;
  el.querySelectorAll('[data-intent]').forEach(btn => {
    btn.addEventListener('click', () => {
      WIZ.state.intent = btn.dataset.intent;
      pushStep(renderStepIntent);
      if(WIZ.state.intent === 'schedule'){
        renderStepServiceDay();
      } else {
        renderStepKnowStop();
      }
    });
  });
}

function renderStepServiceDay(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>Weekdays or weekends?</h4>
      <div class="wizard-grid">
        <button class="wizard-btn" data-day="weekdays">Weekdays</button>
        <button class="wizard-btn" data-day="weekends">Weekends</button>
      </div>
    </div>
  `;
  el.querySelectorAll('[data-day]').forEach(btn => {
    btn.addEventListener('click', () => {
      WIZ.state.serviceDay = btn.dataset.day;
      pushStep(renderStepServiceDay);
      renderStepKnowStop();
    });
  });
}

function renderStepKnowStop(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>Do you know your 4-digit Stop ID?</h4>
      <div class="wizard-grid">
        <button class="wizard-btn" data-know="yes">Yes</button>
        <button class="wizard-btn" data-know="no">No</button>
      </div>
    </div>
  `;
  el.querySelectorAll('[data-know]').forEach(btn => {
    btn.addEventListener('click', () => {
      WIZ.state.knowsStop = btn.dataset.know;
      pushStep(renderStepKnowStop);
      if(WIZ.state.knowsStop === 'yes'){
        renderStepStopId();
      } else {
        renderStepRoute();
      }
    });
  });
}

function renderStepStopId(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>Enter stop ID</h4>
      <div class="wizard-row">
        <input id="wiz-stop-id" type="text" inputmode="numeric" placeholder="e.g. 0073" />
        <button class="wizard-btn primary" id="wiz-stop-submit">Continue</button>
      </div>
      <p class="wizard-hint">Use the 3-4 digits on the stop sign.</p>
    </div>
  `;
  w('wiz-stop-submit').addEventListener('click', () => {
    const raw = w('wiz-stop-id').value || '';
    const stop = normalizeStopId(raw);
    if(!stop){
      setOutput('<div class="wizard-error">Please enter a valid 3–4 digit stop ID.</div>');
      return;
    }
    WIZ.state.stopId = stop;
    pushStep(renderStepStopId);
    if(WIZ.state.intent === 'eta'){
      fetchEta(stop);
    } else {
      renderStepTime();
    }
  });
}

async function renderStepRoute(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>Select a route</h4>
      <div class="wizard-grid" id="wiz-routes">Loading routes…</div>
    </div>
  `;
  const container = w('wiz-routes');
  try{
    const res = await fetch(`${WIZ.base}/api/routes`);
    const data = await res.json();
    container.innerHTML = '';
    (data.routes || []).forEach(r => {
      const b = document.createElement('button');
      b.className = 'wizard-btn';
      b.textContent = `Route ${r.id}`;
      b.addEventListener('click', () => {
        WIZ.state.route = r.id;
        pushStep(renderStepRoute);
        renderStepDirection();
      });
      container.appendChild(b);
    });
  } catch (e){
    container.innerHTML = 'Unable to load routes.';
  }
}

async function renderStepDirection(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>Choose a direction</h4>
      <div class="wizard-grid" id="wiz-directions">Loading directions…</div>
    </div>
  `;
  const container = w('wiz-directions');
  try{
    const res = await fetch(`${WIZ.base}/api/directions?route_id=${encodeURIComponent(WIZ.state.route)}`);
    const data = await res.json();
    container.innerHTML = '';
    (data.directions || []).forEach(d => {
      const b = document.createElement('button');
      b.className = 'wizard-btn';
      const name = (d.name || d.dir || d.id || '').replace(/\s+/g, ' ').trim();
      b.textContent = name;
      b.addEventListener('click', () => {
        WIZ.state.direction = d.id || d.dir || d.name;
        pushStep(renderStepDirection);
        renderStepStop();
      });
      container.appendChild(b);
    });
  } catch (e){
    container.innerHTML = 'Unable to load directions.';
  }
}

async function renderStepStop(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>Select a stop</h4>
      <div class="wizard-grid" id="wiz-stops">Loading stops…</div>
    </div>
  `;
  const container = w('wiz-stops');
  try{
    const params = new URLSearchParams({ route_id: WIZ.state.route });
    if(WIZ.state.direction){ params.set('direction_id', WIZ.state.direction); }
    const res = await fetch(`${WIZ.base}/api/stops?${params.toString()}`);
    const data = await res.json();
    container.innerHTML = '';
    (data.stops || []).forEach(s => {
      const b = document.createElement('button');
      b.className = 'wizard-btn';
      b.innerHTML = `<span class="stop-name">${s.name}</span> <span class="stop-id">${s.id}</span>`;
      b.addEventListener('click', () => {
        WIZ.state.stopId = s.id;
        WIZ.state.stop = s.name;
        pushStep(renderStepStop);
        if(WIZ.state.intent === 'eta'){
          fetchEta(WIZ.state.stopId);
        } else {
          renderStepTime();
        }
      });
      container.appendChild(b);
    });
  } catch (e){
    container.innerHTML = 'Unable to load stops.';
  }
}

function renderStepTime(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>How do you want to set the time?</h4>
      <div class="wizard-grid">
        <button class="wizard-btn" id="wiz-specific">Specific date & time</button>
        <button class="wizard-btn" id="wiz-window">Time window</button>
      </div>
    </div>
  `;
  w('wiz-specific').addEventListener('click', () => renderSpecificDateTime());
  w('wiz-window').addEventListener('click', () => renderTimeWindow());
}

function renderSpecificDateTime(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>Pick a date and time</h4>
      <div class="wizard-row">
        <input id="wiz-dt" type="datetime-local" />
        <button class="wizard-btn primary" id="wiz-dt-submit">Use this date/time</button>
      </div>
    </div>
  `;
  w('wiz-dt-submit').addEventListener('click', () => {
    const v = w('wiz-dt').value;
    if(!v){
      setOutput('<div class="wizard-error">Please enter a valid date/time.</div>');
      return;
    }
    WIZ.state.timeframe = formatDateTime(v);
    fetchSchedule();
  });
}

function renderTimeWindow(){
  const el = w('wizard-steps');
  el.innerHTML = `
    <div class="wizard-card">
      <h4>Choose a time window</h4>
      <div class="wizard-row">
        <input id="wiz-date" type="date" />
        <input id="wiz-start" type="time" />
        <input id="wiz-end" type="time" />
      </div>
      <button class="wizard-btn primary" id="wiz-window-submit">Use this time window</button>
    </div>
  `;
  w('wiz-window-submit').addEventListener('click', () => {
    const start = w('wiz-start').value;
    const end = w('wiz-end').value;
    if(!start || !end){
      setOutput('<div class="wizard-error">Please enter a valid time window.</div>');
      return;
    }
    const date = w('wiz-date').value;
    const dateText = date ? ` on ${date}` : '';
    WIZ.state.timeframe = `between ${formatTime(start)} and ${formatTime(end)}${dateText}`;
    fetchSchedule();
  });
}

async function fetchEta(stopId){
  setOutput('<div class="wizard-note">Checking real-time arrivals…</div>');
  try{
    const res = await fetch(`${WIZ.base}/api/predictions?stop_id=${encodeURIComponent(stopId)}`);
    const data = await res.json();
    if(!res.ok){
      setOutput(`<div class="wizard-error">${data.error_message || 'Stop not found.'}</div><div class="wizard-hint">These are the available routes. Pick one to see all stops and locate your stop ID.</div>`);
      renderStepRoute();
      return;
    }
    const preds = data.predictions || [];
    if(!preds.length){
      autoScheduleFallback();
      return;
    }
    const upcoming = preds.filter(p => {
      const mins = p.minutes;
      if(String(mins).toUpperCase() === 'DUE') return true;
      const n = parseInt(mins, 10);
      return !isNaN(n) && n <= WIZ.etaThreshold;
    });
    if(!upcoming.length){
      autoScheduleFallback();
      return;
    }
    setOutput(renderPredictions(upcoming));
  } catch (e){
    setOutput('<div class="wizard-error">Unable to load predictions.</div>');
  }
}

function autoScheduleFallback(){
  WIZ.state.intent = 'schedule';
  WIZ.state.timeframe = 'now';
  fetchSchedule();
}

async function fetchSchedule(){
  const parts = ['Schedule'];
  if(WIZ.state.serviceDay) parts.push(WIZ.state.serviceDay);
  if(WIZ.state.route) parts.push(`Route ${WIZ.state.route}`);
  if(WIZ.state.direction) parts.push(WIZ.state.direction);
  if(WIZ.state.stop) parts.push(`at ${WIZ.state.stop}`);
  else if(WIZ.state.stopId) parts.push(`stop ${WIZ.state.stopId}`);
  if(WIZ.state.timeframe) parts.push(WIZ.state.timeframe);
  const message = parts.join(' ');
  setOutput('<div class="wizard-note">Checking schedule…</div>');
  try{
    const res = await fetch(`${WIZ.base}/api/agent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    const data = await res.json();
    setOutput(`<pre class="wizard-result">${data.answer || 'No response.'}</pre>`);
  } catch (e){
    setOutput('<div class="wizard-error">Unable to load schedule.</div>');
  }
}

function renderPredictions(preds){
  const rows = preds.map(p => {
    const mins = String(p.minutes).toUpperCase() === 'DUE' ? 'DUE' : `${p.minutes} min`;
    return `<li><strong>Route ${p.route}</strong> to ${p.destination} — ${mins}</li>`;
  }).join('');
  const label = WIZ.state.stopId ? `Next buses: Stop ID ${WIZ.state.stopId}` : 'Next buses:';
  return `<div class="wizard-note">${label}</div><ul class="wizard-list">${rows}</ul>`;
}

function normalizeStopId(raw){
  const digits = String(raw || '').replace(/[^0-9]/g, '');
  if(!digits || digits.length > 4) return null;
  return digits.padStart(4, '0');
}

function formatDateTime(value){
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  const dateStr = dt.toISOString().slice(0, 10);
  const timeStr = value.split('T')[1] || '';
  return `${dateStr} at ${formatTime(timeStr)}`;
}

function formatTime(value){
  if (!value) return '';
  const parts = value.split(':');
  let hh = parseInt(parts[0], 10);
  const mm = parts[1] || '00';
  const ap = hh >= 12 ? 'pm' : 'am';
  hh = hh % 12;
  if (hh === 0) hh = 12;
  return `${hh}:${mm} ${ap}`;
}

window.addEventListener('DOMContentLoaded', () => {
  const backBtn = w('wizard-back');
  const resetBtn = w('wizard-reset');
  if(backBtn){ backBtn.addEventListener('click', goBack); }
  if(resetBtn){ resetBtn.addEventListener('click', resetWizard); }
  renderStepIntent();
});
