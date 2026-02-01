// ====== CONFIG ======
const BASE = '';

// ====== CHAT STATE ======
const HISTORY_KEY = 'rts_chat_history_v1';
const SESSION_KEY = 'rts_chat_session';
const SESSION_TIMEOUT_MS = 5 * 60 * 1000;
const chatHistory = [];
let sessionId = null;
let inactivityTimer = null;
const chatState = {
  intent: null,
  route: null,
  direction: null,
  stopId: null,
  stopName: null,
  timeframe: null,
  wizardActive: false,
};

function loadHistory(){
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if(!raw) return;
    const parsed = JSON.parse(raw);
    if(Array.isArray(parsed)){
      parsed.forEach(m => {
        if(m && typeof m.role === 'string' && typeof m.content === 'string'){
          chatHistory.push({ role: m.role, content: m.content });
        }
      });
    }
    sessionId = localStorage.getItem(SESSION_KEY) || null;
  } catch (_) {
    // ignore corrupted history
  }
}

function saveHistory(){
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(chatHistory.slice(-50)));
    if(sessionId){
      localStorage.setItem(SESSION_KEY, sessionId);
    }
  } catch (_) {
    // ignore storage errors
  }
}

function clearHistory(){
  chatHistory.length = 0;
  try {
    localStorage.removeItem(HISTORY_KEY);
    localStorage.removeItem(SESSION_KEY);
  } catch (_) {
    // ignore storage errors
  }
  sessionId = null;
  const wrap = el('chat-messages');
  if(wrap){ wrap.innerHTML = ''; }
}

function ensureSessionId(){
  if(sessionId){
    return sessionId;
  }
  sessionId = `sess_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,8)}`;
  saveHistory();
  return sessionId;
}

function startGreeting(){
  const greeting = 'Hi! I’m the RTS virtual assistant. Type a bus stop ID (4 digits) or ask about a route and stop—for example, "Route 5 at Rosa Parks".';
  appendBubble(greeting, 'bot');
  chatHistory.push({ role: 'assistant', content: greeting });
  saveHistory();
  startWizard();
}

function scheduleInactivityTimeout(){
  if(inactivityTimer){
    clearTimeout(inactivityTimer);
  }
  inactivityTimer = setTimeout(() => {
    endSession(false);
  }, SESSION_TIMEOUT_MS);
}

function endSession(manual=true){
  clearHistory();
  if(inactivityTimer){
    clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
  const msg = manual
    ? 'Session cleared. Ask a new question anytime.'
    : 'Session ended after 5 minutes of inactivity. Start a new chat when you’re ready.';
  appendBubble(msg, 'bot');
  chatHistory.push({ role: 'assistant', content: msg });
  startGreeting();
  saveHistory();
}

// ====== DOM ======
function el(id){ return document.getElementById(id); }

function appendBubble(text, who='user'){
  const wrap = el('chat-messages');
  const bubble = document.createElement('div');
  bubble.className = who === 'user' ? 'bubble user' : 'bubble bot';
  bubble.textContent = text;
  wrap.appendChild(bubble);
  wrap.scrollTop = wrap.scrollHeight;
  return bubble;
}

function appendActionBubble(contentBuilder){
  const wrap = el('chat-messages');
  const bubble = document.createElement('div');
  bubble.className = 'bubble bot bubble-actions';
  const container = document.createElement('div');
  container.className = 'chat-buttons';
  contentBuilder(container);
  bubble.appendChild(container);
  wrap.appendChild(bubble);
  wrap.scrollTop = wrap.scrollHeight;
  return bubble;
}

async function sendMessage(){
  const input = el('chat-input');
  const msg = input.value.trim();
  if(!msg) return;
  const sid = ensureSessionId();
  input.value = '';
  appendBubble(msg, 'user');
  chatHistory.push({ role: 'user', content: msg });
  saveHistory();
  const payloadHistory = chatHistory.slice(-12);

  // disable UI while sending
  el('chat-send').disabled = true;
  const thinking = appendBubble('Thinking...', 'bot');

  try {
    const res = await fetch(`${BASE}/api/agent`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        message: msg,
        history: payloadHistory,
        messages: payloadHistory,
        session_id: sid
      })
    });
    if(!res.ok){
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    const answer = data.answer || data.error || 'No response.';
    if(thinking){ thinking.textContent = answer; }

    // Add buttons if present
    if(data.buttons && Array.isArray(data.buttons) && data.buttons.length > 0){
      const btnContainer = document.createElement('div');
      btnContainer.className = 'chat-buttons';
      data.buttons.forEach(btn => {
        const button = document.createElement('button');
        button.className = 'chat-btn';
        button.textContent = btn.label;
        button.addEventListener('click', () => {
          el('chat-input').value = btn.action;
          sendMessage();
        });
        btnContainer.appendChild(button);
      });
      thinking.appendChild(btnContainer);
    }

    chatHistory.push({ role: 'assistant', content: answer });
    saveHistory();
    scheduleInactivityTimeout();
  } catch (e) {
    const msg = 'Network error talking to the agent.';
    if(thinking){ thinking.textContent = msg; }
    chatHistory.push({ role: 'assistant', content: msg });
    saveHistory();
  } finally {
    el('chat-send').disabled = false;
    el('chat-input').focus();
  }
}

function resetWizard(){
  chatState.intent = null;
  chatState.route = null;
  chatState.direction = null;
  chatState.stopId = null;
  chatState.stopName = null;
  chatState.timeframe = null;
  chatState.wizardActive = false;
}

function startWizard(){
  resetWizard();
  chatState.wizardActive = true;
  appendBubble('Let’s get you the right info. What do you need?', 'bot');
  appendActionBubble(container => {
    [
      { label: '🚌 Next Bus ETA', intent: 'eta' },
      { label: '📅 Scheduled Departures', intent: 'schedule' },
      { label: '🧭 Route overview', intent: 'route_info' },
      { label: '💬 Just ask a question', intent: 'freeform' },
    ].forEach(option => {
      const btn = document.createElement('button');
      btn.className = 'chat-btn';
      btn.textContent = option.label;
      btn.addEventListener('click', () => handleIntentSelection(option.intent));
      container.appendChild(btn);
    });
  });
}
async function handleIntentSelection(intent){
  if(intent === 'freeform'){
    chatState.wizardActive = false;
    appendBubble('Go ahead and type your question below.', 'bot');
    return;
  }
  chatState.intent = intent;
  chatState.wizardActive = true;
  appendBubble('Great! Select a route to continue.', 'bot');
  await showRouteOptions();
}

async function showRouteOptions(){
  try{
    const res = await fetch(`${BASE}/api/routes`);
    const data = await res.json();
    const routes = (data.routes || []).slice(0, 12);
    appendActionBubble(container => {
      routes.forEach(route => {
        const btn = document.createElement('button');
        btn.className = 'chat-btn';
        btn.textContent = `Route ${route.id || route.name || ''}`.trim();
        btn.addEventListener('click', () => handleRouteSelection(route));
        container.appendChild(btn);
      });
    });
  } catch (e){
    appendBubble('Sorry, I could not load the route list. You can type your question instead.', 'bot');
    chatState.wizardActive = false;
  }
}

function handleRouteSelection(route){
  chatState.route = route.id || route.name || '';
  appendBubble(`Route selected: ${chatState.route}`, 'bot');
  if(chatState.intent === 'route_info'){
    showRouteInfoSummary();
  } else {
    showDirectionOrStopStep();
  }
}

async function showDirectionOrStopStep(){
  appendBubble('Which direction are you headed?', 'bot');
  try{
    const res = await fetch(`${BASE}/api/directions?route_id=${encodeURIComponent(chatState.route)}`);
    const data = await res.json();
    const directions = data.directions || [];
    if(directions.length === 0){
      await showStopOptions(null);
      return;
    }
    appendActionBubble(container => {
      directions.forEach(d => {
        const btn = document.createElement('button');
        btn.className = 'chat-btn';
        const id = d.id || d.dir || d.name || d;
        btn.textContent = d.name || d.dir || d;
        btn.addEventListener('click', () => {
          chatState.direction = id;
          handleDirectionSelection();
        });
        container.appendChild(btn);
      });
    });
  } catch (e){
    appendBubble('Direction list unavailable. Please type your question manually.', 'bot');
    chatState.wizardActive = false;
  }
}

function handleDirectionSelection(){
  appendBubble(`Direction selected: ${chatState.direction}`, 'bot');
  showStopOptions(chatState.direction);
}

async function showStopOptions(directionId){
  appendBubble('Pick the stop or landmark.', 'bot');
  try{
    const params = new URLSearchParams({ route_id: chatState.route });
    if(directionId){
      params.set('direction_id', directionId);
    }
    const res = await fetch(`${BASE}/api/stops?${params.toString()}`);
    const data = await res.json();
    const stops = data.stops || [];
    appendActionBubble(container => {
      stops.slice(0, 12).forEach(stop => {
        const btn = document.createElement('button');
        btn.className = 'chat-btn';
        btn.textContent = `${stop.name || 'Stop'} (${stop.id})`;
        btn.addEventListener('click', () => {
          chatState.stopId = stop.id;
          chatState.stopName = stop.name;
          appendBubble(`Stop selected: ${stop.name || stop.id}`, 'bot');
          showTimeStep();
        });
        container.appendChild(btn);
      });
    });
  } catch (e){
    appendBubble('Stop list unavailable. Please type your question manually.', 'bot');
    chatState.wizardActive = false;
  }
}

function showTimeStep(){
  if(chatState.intent === 'eta'){
    appendBubble('When do you want the next bus?', 'bot');
    appendActionBubble(container => {
      [
        { label: 'Right now', timeframe: 'now' },
        { label: 'After 15 minutes', timeframe: 'after 15 minutes' },
        { label: 'Custom time', timeframe: 'custom' },
      ].forEach(option => {
        const btn = document.createElement('button');
        btn.className = 'chat-btn';
        btn.textContent = option.label;
        btn.addEventListener('click', () => handleTimeSelection(option.timeframe));
        container.appendChild(btn);
      });
    });
  } else {
    appendBubble('When should I check the schedule?', 'bot');
    appendActionBubble(container => {
      [
        { label: 'This morning', timeframe: 'this morning' },
        { label: 'This afternoon', timeframe: 'this afternoon' },
        { label: 'Tomorrow morning', timeframe: 'tomorrow morning' },
        { label: 'Specific time', timeframe: 'custom' },
      ].forEach(option => {
        const btn = document.createElement('button');
        btn.className = 'chat-btn';
        btn.textContent = option.label;
        btn.addEventListener('click', () => handleTimeSelection(option.timeframe));
        container.appendChild(btn);
      });
    });
  }
}

function handleTimeSelection(timeframe){
  if(timeframe === 'custom'){
    const example = chatState.intent === 'eta'
      ? 'Type when you need the next bus (e.g., "after 3:45pm" or "in 20 minutes").'
      : 'Type the day/time you want (e.g., "tomorrow at 8am" or "next Monday 5pm").';
    appendBubble(example, 'bot');
    chatState.wizardActive = false;
    return;
  }
  chatState.timeframe = timeframe;
  showSummaryAndSubmit();
}

function showSummaryAndSubmit(){
  const summary = `Requesting ${chatState.intent === 'eta' ? 'ETA' : 'schedule'} for Route ${chatState.route}${chatState.direction ? ` (${chatState.direction})` : ''} at ${chatState.stopName || chatState.stopId}${chatState.timeframe ? ` (${chatState.timeframe})` : ''}.`;
  appendBubble(summary, 'bot');
  const prompt = buildFinalPrompt();
  appendBubble('Sending your request...', 'bot');
  el('chat-input').value = prompt;
  chatState.wizardActive = false;
  sendMessage();
}

function buildFinalPrompt(){
  const parts = [];
  if(chatState.intent === 'route_info'){
    return `Route ${chatState.route} overview`;
  }
  if(chatState.intent === 'eta'){
    parts.push('ETA');
  } else {
    parts.push('Schedule');
  }
  if(chatState.route){
    parts.push(`Route ${chatState.route}`);
  }
  if(chatState.direction){
    parts.push(chatState.direction);
  }
  if(chatState.stopName){
    parts.push(`at ${chatState.stopName}`);
  } else if(chatState.stopId){
    parts.push(`stop ${chatState.stopId}`);
  }
  if(chatState.timeframe && chatState.timeframe !== 'now'){
    parts.push(chatState.timeframe);
  }
  return parts.join(' ');
}

function showRouteInfoSummary(){
  appendBubble(`Looking up the overview for Route ${chatState.route}.`, 'bot');
  const prompt = buildFinalPrompt();
  el('chat-input').value = prompt;
  chatState.wizardActive = false;
  sendMessage();
}

// Make it easy to verify the script loaded
window.RTS_CHAT_OK = true;

// ====== EVENTS ======
window.addEventListener('DOMContentLoaded', () => {
  const toggle = el('chat-toggle');
  const panel  = el('chat-panel');
  const send   = el('chat-send');
  const input  = el('chat-input');
  const endBtn = el('chat-end');

  if(!panel || !send || !input){
    console.error('Chat elements missing in DOM.');
    return;
  }

  loadHistory();
  if(chatHistory.length){
    chatHistory.forEach(m => {
      appendBubble(m.content, m.role === 'user' ? 'user' : 'bot');
    });
  } else {
    startGreeting();
  }

  function toggleChat(){
    if(!toggle){ return; }
    const isOpen = panel.classList.toggle('open');
    toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  }

  if(toggle){
    toggle.addEventListener('click', toggleChat);
    toggle.addEventListener('keydown', (e) => {
      if(e.key === 'Enter' || e.key === ' '){
        e.preventDefault();
        toggleChat();
      }
    });
  } else {
    panel.classList.add('open');
  }
  send.addEventListener('click', sendMessage);
  if(endBtn){
    endBtn.addEventListener('click', () => endSession(true));
  }
  input.addEventListener('keydown', (e) => {
    if(e.key === 'Enter' && !e.shiftKey){
      e.preventDefault();
      sendMessage();
    }
  });
});
