console.log("HELLO FROM NEW FRONTEND.JS v4");

// Base URL for backend (same domain)
const API_BASE = "/api";

// Grab elements
const routeSelect = document.getElementById("routeSelect");
const directionSelect = document.getElementById("directionSelect");
const stopSelect = document.getElementById("stopSelect");

const stopIdInput = document.getElementById("stopIdInput");
const stopIdBtn = document.getElementById("stopIdBtn");

const predictionsList = document.getElementById("predictionsList");

const refreshBtn = document.getElementById("refreshBtn");
const clearBtn = document.getElementById("clearBtn");

// ----- helpers -----

// Auto-refresh timer
let autoRefreshTimer = null;

function startAutoRefresh() {
  stopAutoRefresh();
  autoRefreshTimer = setInterval(handleRefresh, 30000); // 30 seconds
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

function setPredictionsMessage(msg, isLoading = false) {
  predictionsList.innerHTML = "";
  const li = document.createElement("li");
  li.className = "empty-state";
  if (isLoading) {
    li.innerHTML = `<span class="loading-spinner">🔄</span> ${msg}`;
  } else {
    li.textContent = msg;
  }
  predictionsList.appendChild(li);
}

function renderPredictions(predictions) {
  predictionsList.innerHTML = "";

  if (!predictions || !predictions.length) {
    const li = document.createElement("li");
    li.className = "empty-state";
    li.textContent = "🚌 No buses scheduled soon for this stop.";
    predictionsList.appendChild(li);
    stopAutoRefresh();
    return;
  }

  predictions.forEach(p => {
    const li = document.createElement("li");
    const mins = (p.minutes || "").toString().toUpperCase();
    const minsLabel = mins === "DUE" ? "Due" : `${mins} min`;

    // Add delay indicator
    const delayBadge = p.delayed
      ? '<span class="delay-badge">Delayed</span>'
      : '';

    li.innerHTML = `
      <div class="arrival-card ${p.delayed ? 'delayed' : ''}">
        <div class="arrival-main">
          <div class="arrival-route">Route ${p.route}</div>
          <div class="arrival-dest">to ${p.destination}</div>
        </div>
        <div class="arrival-min">${minsLabel}</div>
      </div>
      <div class="arrival-meta">
        Bus #${p.vehicle_id || "—"} · ${p.arrival_time || "—"}${delayBadge}
      </div>
    `;
    predictionsList.appendChild(li);
  });

  // Start auto-refresh when predictions are showing
  startAutoRefresh();
}

// disable / enable helpers for dropdown flow
function disableSelect(sel, placeholderText) {
  sel.disabled = true;
  sel.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = "";
  opt.textContent = placeholderText;
  sel.appendChild(opt);
}

function enableSelect(sel, placeholderText) {
  sel.disabled = false;
  sel.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = "";
  opt.textContent = placeholderText;
  sel.appendChild(opt);
}

// normalize Stop ID to 4 digits
function normalizeStopId(raw) {
  if (!raw) return "";
  let digits = raw.replace(/\D/g, ""); // keep only numbers
  if (digits.length > 4) return "";
  digits = digits.padStart(4, "0"); // "5" -> "0005"
  return digits;
}

// ----- main flows -----

// initRoutes: called on page load
async function initRoutes() {
  // lock direction + stop first
  disableSelect(directionSelect, "Select Direction");
  disableSelect(stopSelect, "Select Stop");

  // default message
  setPredictionsMessage("Choose a stop or enter a Stop ID to see arrivals.");

  // fetch routes
  let data;
  try {
    data = await fetchJSON(`${API_BASE}/routes`);
  } catch (e) {
    setPredictionsMessage("Unable to load routes right now. Please try again.");
    return;
  }

  // fill routeSelect
  routeSelect.disabled = false;
  routeSelect.innerHTML = "";

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select a Route";
  routeSelect.appendChild(placeholder);

  data.routes.forEach(r => {
    const opt = document.createElement("option");
    opt.value = r.id;
    opt.textContent = `${r.id} - ${r.name}`;
    routeSelect.appendChild(opt);
  });
}

// when user changes route
async function handleRouteChange() {
  const route_id = routeSelect.value;

  if (!route_id) {
    disableSelect(directionSelect, "Select Direction");
    disableSelect(stopSelect, "Select Stop");
    setPredictionsMessage("Choose a stop or enter a Stop ID to see arrivals.");
    return;
  }

  let data;
  try {
    data = await fetchJSON(
      `${API_BASE}/directions?route_id=${encodeURIComponent(route_id)}`
    );
  } catch (e) {
    setPredictionsMessage("Unable to load directions right now. Please try again.");
    return;
  }

  enableSelect(directionSelect, "Select Direction");

  data.directions.forEach(d => {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.name;
    directionSelect.appendChild(opt);
  });

  // reset below
  disableSelect(stopSelect, "Select Stop");
  setPredictionsMessage("Choose a stop or enter a Stop ID to see arrivals.");
}

// when user changes direction
async function handleDirectionChange() {
  const route_id = routeSelect.value;
  const direction_id = directionSelect.value;

  if (!direction_id) {
    disableSelect(stopSelect, "Select Stop");
    setPredictionsMessage("Choose a stop or enter a Stop ID to see arrivals.");
    return;
  }

  let data;
  try {
    data = await fetchJSON(
      `${API_BASE}/stops?route_id=${encodeURIComponent(route_id)}&direction_id=${encodeURIComponent(direction_id)}`
    );
  } catch (e) {
    setPredictionsMessage("Unable to load stops right now. Please try again.");
    return;
  }

  enableSelect(stopSelect, "Select Stop");

  data.stops.forEach(s => {
    const rawId = (s.id || "").toString();
    const prettyId = rawId.padStart(4, "0");
    const opt = document.createElement("option");

    opt.value = rawId; // real stop ID
    opt.textContent = `Bus Stop ${prettyId} - ${s.name}`;

    stopSelect.appendChild(opt);
  });

  setPredictionsMessage("Choose a stop or enter a Stop ID to see arrivals.");
}

// when user changes stop
async function handleStopChange() {
  const stop_id = stopSelect.value;

  if (!stop_id) {
    setPredictionsMessage("Choose a stop or enter a Stop ID to see arrivals.");
    stopAutoRefresh();
    return;
  }

  setPredictionsMessage("Loading arrivals...", true);

  try {
    const data = await fetchJSON(
      `${API_BASE}/predictions?stop_id=${encodeURIComponent(stop_id)}`
    );
    renderPredictions(data.predictions);
  } catch (e) {
    setPredictionsMessage("⚠️ Unable to load predictions. Tap Refresh to try again.");
    stopAutoRefresh();
  }
}

// quick Stop ID lookup button
async function handleStopIdLookup() {
  const raw = stopIdInput.value.trim();
  const normalized = normalizeStopId(raw);

  if (!normalized) {
    setPredictionsMessage("Please enter a valid Stop ID (up to 4 digits).");
    stopAutoRefresh();
    return;
  }

  setPredictionsMessage("Loading arrivals...", true);

  try {
    const data = await fetchJSON(
      `${API_BASE}/predictions?stop_id=${encodeURIComponent(normalized)}`
    );
    renderPredictions(data.predictions);
  } catch (e) {
    setPredictionsMessage("⚠️ Unable to load predictions. Tap Refresh to try again.");
    stopAutoRefresh();
  }
}

// refresh button:
// 1. if dropdown stop is selected -> refresh that stop
// 2. else if stopIdInput has something -> refresh using that
// 3. else -> show default message
async function handleRefresh() {
  const selectedStop = stopSelect.value;
  const typedStopRaw = stopIdInput.value.trim();
  const normalizedTyped = normalizeStopId(typedStopRaw);

  if (selectedStop) {
    try {
      const data = await fetchJSON(
        `${API_BASE}/predictions?stop_id=${encodeURIComponent(selectedStop)}`
      );
      renderPredictions(data.predictions);
      return;
    } catch (e) {
      setPredictionsMessage("Unable to load predictions right now. Please try again.");
      return;
    }
  }

  if (normalizedTyped) {
    try {
      const data = await fetchJSON(
        `${API_BASE}/predictions?stop_id=${encodeURIComponent(normalizedTyped)}`
      );
      renderPredictions(data.predictions);
      return;
    } catch (e) {
      setPredictionsMessage("Unable to load predictions right now. Please try again.");
      return;
    }
  }

  setPredictionsMessage("Choose a stop or enter a Stop ID to see arrivals.");
}

// clear button: reset entire UI state
async function handleClear() {
  // reset selects
  routeSelect.selectedIndex = 0; // back to "Select a Route"
  disableSelect(directionSelect, "Select Direction");
  disableSelect(stopSelect, "Select Stop");

  // clear manual stop id
  stopIdInput.value = "";

  // reset prediction box
  setPredictionsMessage("Choose a stop or enter a Stop ID to see arrivals.");

  // Stop auto-refresh
  stopAutoRefresh();
}

// ----- listeners -----
routeSelect.addEventListener("change", handleRouteChange);
directionSelect.addEventListener("change", handleDirectionChange);
stopSelect.addEventListener("change", handleStopChange);

stopIdBtn.addEventListener("click", handleStopIdLookup);

// Enter key support for Stop ID input
stopIdInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    handleStopIdLookup();
  }
});

// Character count feedback
stopIdInput.addEventListener("input", (e) => {
  const digits = e.target.value.replace(/\D/g, "");
  const helperText = document.querySelector(".helper-text");
  if (helperText) {
    if (digits.length === 0) {
      helperText.textContent = "Tip: You can enter 773 and we'll search 0773.";
      helperText.classList.remove("error");
    } else if (digits.length > 4) {
      helperText.textContent = "⚠️ Stop ID must be 4 digits or less.";
      helperText.classList.add("error");
    } else {
      helperText.textContent = `${digits.length}/4 digits entered`;
      helperText.classList.remove("error");
    }
  }
});

refreshBtn.addEventListener("click", handleRefresh);
clearBtn.addEventListener("click", handleClear);

// ----- boot -----
initRoutes();

// ═══════════════════════════════════════════════════════════════════════════
// PWA PRIMITIVES
// All logic is scoped to this IIFE so it doesn't pollute the module scope.
// ═══════════════════════════════════════════════════════════════════════════
(function initPWA() {

  // ── 1. Service-worker registration ─────────────────────────────────────
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/service-worker.js', { scope: '/' })
        .then((reg) => {
          console.log('[PWA] SW registered, scope:', reg.scope);
        })
        .catch((err) => {
          console.warn('[PWA] SW registration failed:', err);
        });
    });
  }

  // ── 2. Install button (Chrome / Edge desktop + Android) ────────────────
  let _deferredInstallPrompt = null;
  const btnInstall = document.getElementById('btn-install');

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    _deferredInstallPrompt = e;
    if (btnInstall) {
      btnInstall.classList.add('visible');  // pwa.css shows it via flex
    }
  });

  if (btnInstall) {
    btnInstall.addEventListener('click', async () => {
      if (!_deferredInstallPrompt) return;
      _deferredInstallPrompt.prompt();
      await _deferredInstallPrompt.userChoice;
      _deferredInstallPrompt = null;
      btnInstall.classList.remove('visible');
    });
  }

  window.addEventListener('appinstalled', () => {
    console.log('[PWA] App installed');
    // TODO(add-web-push): fire analytics event here
  });

  // ── 3. iOS Safari "Add to Home Screen" tip ─────────────────────────────
  const IOS_TIP_KEY = 'pwa_ios_tip_dismissed';
  const iosTip = document.getElementById('ios-tip');
  const iosTipClose = document.getElementById('ios-tip-close');

  function _isIosSafari() {
    const ua = navigator.userAgent;
    const isIos = /iphone|ipad|ipod/i.test(ua);
    // Safari on iOS: has "Safari" but NOT "CriOS" / "FxiOS" / "EdgiOS"
    const isSafari = /safari/i.test(ua) && !/crios|fxios|edgios/i.test(ua);
    return isIos && isSafari;
  }

  if (iosTip && _isIosSafari() && !localStorage.getItem(IOS_TIP_KEY)) {
    // Show after 3 s on first visit only
    setTimeout(() => {
      iosTip.classList.add('visible');
    }, 3000);

    if (iosTipClose) {
      iosTipClose.addEventListener('click', () => {
        iosTip.classList.remove('visible');
        localStorage.setItem(IOS_TIP_KEY, '1');
      });
    }
  }

  // ── 4. Online / offline banner ──────────────────────────────────────────
  const offlineBanner = document.getElementById('offline-banner');

  function _updateOnlineState() {
    if (!offlineBanner) return;
    if (navigator.onLine) {
      offlineBanner.hidden = true;
    } else {
      offlineBanner.hidden = false;
    }
  }

  window.addEventListener('online',  _updateOnlineState);
  window.addEventListener('offline', _updateOnlineState);
  // Run once on load in case the page loaded while offline
  _updateOnlineState();

}());

