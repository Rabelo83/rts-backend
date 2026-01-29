// ====== CONFIG ======
const BASE = '';

// ====== CHAT STATE ======
const HISTORY_KEY = 'rts_chat_history_v1';
const chatHistory = [];

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
  } catch (_) {
    // ignore corrupted history
  }
}

function saveHistory(){
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(chatHistory.slice(-50)));
  } catch (_) {
    // ignore storage errors
  }
}

// ====== DOM ======
function el(id){ return document.getElementById(id); }

function appendBubble(text, who='user'){
  const wrap = el('chat-messages');
  const li = document.createElement('div');
  li.className = who === 'user' ? 'bubble user' : 'bubble bot';
  li.textContent = text;
  wrap.appendChild(li);
  wrap.scrollTop = wrap.scrollHeight;
}

async function sendMessage(){
  const input = el('chat-input');
  const msg = input.value.trim();
  if(!msg) return;
  input.value = '';
  appendBubble(msg, 'user');
  chatHistory.push({ role: 'user', content: msg });
  saveHistory();

  // disable UI while sending
  el('chat-send').disabled = true;

  try {
    const res = await fetch(`${BASE}/api/agent`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        message: msg,
        history: chatHistory,
        messages: chatHistory
      })
    });
    const data = await res.json();
    const answer = data.answer || data.error || 'No response.';
    appendBubble(answer, 'bot');
    chatHistory.push({ role: 'assistant', content: answer });
    saveHistory();
  } catch (e) {
    appendBubble('Network error talking to the agent.', 'bot');
    chatHistory.push({ role: 'assistant', content: 'Network error talking to the agent.' });
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

  if(!toggle || !panel || !send || !input){
    console.error('Chat elements missing in DOM.');
    return;
  }

  loadHistory();
  if(chatHistory.length){
    chatHistory.forEach(m => {
      appendBubble(m.content, m.role === 'user' ? 'user' : 'bot');
    });
  }

  toggle.addEventListener('click', () => {
    panel.classList.toggle('open');
  });
  send.addEventListener('click', sendMessage);
  input.addEventListener('keydown', (e) => {
    if(e.key === 'Enter' && !e.shiftKey){
      e.preventDefault();
      sendMessage();
    }
  });
});
