// ====== CONFIG ======
const BASE = '';

// ====== CHAT STATE ======
const HISTORY_KEY = 'rts_chat_history_v1';
const SESSION_KEY = 'rts_chat_session';
const SESSION_TIMEOUT_MS = 5 * 60 * 1000;
const chatHistory = [];
let sessionId = null;
let inactivityTimer = null;

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

// Make it easy to verify the script loaded
window.RTS_CHAT_OK = true;

// ====== EVENTS ======
window.addEventListener('DOMContentLoaded', () => {
  const toggle = el('chat-toggle');
  const panel  = el('chat-panel');
  const send   = el('chat-send');
  const input  = el('chat-input');
  const endBtn = el('chat-end');

  if(!toggle || !panel || !send || !input){
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
    const isOpen = panel.classList.toggle('open');
    toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  }

  toggle.addEventListener('click', toggleChat);
  toggle.addEventListener('keydown', (e) => {
    if(e.key === 'Enter' || e.key === ' '){
      e.preventDefault();
      toggleChat();
    }
  });
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
