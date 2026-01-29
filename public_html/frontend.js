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

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

function setPredictionsMessage(msg) {
  predictionsList.innerHTML = "";
  const li = document.createElement("li");
  li.innerHTML = msg;
  predictionsList.appendChild(li);
}

function renderPredictions(predictions) {
  predictionsList.innerHTML = "";

  if (!predictions || !predictions.length) {
    const li = document.createElement("li");
    li.textContent = "No buses scheduled soon for this stop.";
    predictionsList.appendChild(li);
    return;
  }

  predictions.forEach(p => {
    const li = document.createElement("li");
    li.innerHTML = `
      <strong>${p.minutes} min</strong> - Route ${p.route} toward ${p.destination}
      <br />
      <small>Bus #${p.vehicle_id} * ${p.arrival_time}</small>
    `;
    predictionsList.appendChild(li);
  });
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
    return;
  }

  try {
    const data = await fetchJSON(
      `${API_BASE}/predictions?stop_id=${encodeURIComponent(stop_id)}`
    );
    renderPredictions(data.predictions);
  } catch (e) {
    setPredictionsMessage("Unable to load predictions right now. Please try again.");
  }
}

// quick Stop ID lookup button
async function handleStopIdLookup() {
  const raw = stopIdInput.value.trim();
  const normalized = normalizeStopId(raw);

  if (!normalized) {
    setPredictionsMessage("Please enter a valid Stop ID (up to 4 digits).");
    return;
  }

  try {
    const data = await fetchJSON(
      `${API_BASE}/predictions?stop_id=${encodeURIComponent(normalized)}`
    );
    renderPredictions(data.predictions);
  } catch (e) {
    setPredictionsMessage("Unable to load predictions right now. Please try again.");
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
}

// ----- listeners -----
routeSelect.addEventListener("change", handleRouteChange);
directionSelect.addEventListener("change", handleDirectionChange);
stopSelect.addEventListener("change", handleStopChange);

stopIdBtn.addEventListener("click", handleStopIdLookup);

refreshBtn.addEventListener("click", handleRefresh);
clearBtn.addEventListener("click", handleClear);

// ----- boot -----
initRoutes();
