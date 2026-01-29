console.log("HELLO FROM NEW APP.JS v2");

// Point frontend to same-domain backend
const API_BASE = "/api";

const routeSelect = document.getElementById("routeSelect");
const directionSelect = document.getElementById("directionSelect");
const stopSelect = document.getElementById("stopSelect");
const predictionsList = document.getElementById("predictionsList");

async function fetchJSON(url) {
  const res = await fetch(url);
  return res.json();
}

async function loadRoutes() {
  // call Render, not local PHP
  const data = await fetchJSON(`${API_BASE}/routes`);
  routeSelect.innerHTML = "";
  data.routes.forEach(r => {
    const opt = document.createElement("option");
    opt.value = r.id;
    opt.textContent = `${r.id} - ${r.name}`;
    routeSelect.appendChild(opt);
  });

  // after we load routes, load directions for the first route
  await loadDirections();
}

async function loadDirections() {
  const route_id = routeSelect.value;
  const data = await fetchJSON(
    `${API_BASE}/directions?route_id=${encodeURIComponent(route_id)}`
  );

  directionSelect.innerHTML = "";
  data.directions.forEach(d => {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.name;
    directionSelect.appendChild(opt);
  });

  // after we load directions, load stops
  await loadStops();
}

async function loadStops() {
  const route_id = routeSelect.value;
  const direction_id = directionSelect.value;
  const data = await fetchJSON(
    `${API_BASE}/stops?route_id=${encodeURIComponent(route_id)}&direction_id=${encodeURIComponent(direction_id)}`
  );

  stopSelect.innerHTML = "";
  data.stops.forEach(s => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.name;
    stopSelect.appendChild(opt);
  });

  // after we load stops, load predictions
  await loadPredictions();
}

async function loadPredictions() {
  const stop_id = stopSelect.value;
  const data = await fetchJSON(
    `${API_BASE}/predictions?stop_id=${encodeURIComponent(stop_id)}`
  );

  predictionsList.innerHTML = "";

  if (!data.predictions.length) {
    const li = document.createElement("li");
    li.textContent = "No upcoming buses.";
    predictionsList.appendChild(li);
    return;
  }

  data.predictions.forEach(p => {
    const li = document.createElement("li");
    li.innerHTML = `
      <strong>${p.minutes} min</strong> — Route ${p.route} toward ${p.destination}
      <br /><small>Bus #${p.vehicle_id} • ${p.arrival_time}</small>
    `;
    predictionsList.appendChild(li);
  });
}

// when user changes dropdowns
routeSelect.addEventListener("change", loadDirections);
directionSelect.addEventListener("change", loadStops);
stopSelect.addEventListener("change", loadPredictions);

// boot up
loadRoutes();
