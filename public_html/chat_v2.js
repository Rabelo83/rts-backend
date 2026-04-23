// ======================================
// RTS LIVE ASSISTANT - V2.0
// Complete rewrite with all improvements
// ======================================

// ====== CONFIGURATION ======
const CONFIG = {
  BASE_URL: '',
  // v3 (Claude) is the default. Use ?agent=v2/v4 to test other versions.
  AGENT_ENDPOINT: (() => {
    const a = new URLSearchParams(location.search).get('agent');
    if (a === 'v2') return '/api/agent/v2/stream';
    if (a === 'v4') return '/api/agent/v4/stream';
    return '/api/agent/v3/stream';
  })(),
  API_TIMEOUT: 30000, // 30 seconds
  SESSION_TIMEOUT_MS: 5 * 60 * 1000, // 5 minutes
  MAX_HISTORY: 50,
  ETA_THRESHOLD_MINUTES: 45, // If no buses within 45min, suggest schedule
};

// ====== LOCALSTORAGE KEYS ======
const STORAGE_KEYS = {
  HISTORY: 'rts_chat_history_v2',
  SESSION: 'rts_chat_session_v2',
  STATE: 'rts_chat_state_v2',
  LANGUAGE: 'rts_chat_language',
  OFFLINE_CACHE: 'rts_offline_predictions',
};

// ====== TRANSLATIONS ======
const TRANSLATIONS = {
  en: {
    greeting_morning: 'Good morning',
    greeting_afternoon: 'Good afternoon',
    greeting_evening: 'Good evening',
    greeting_night: 'Good night',
    greeting_suffix: "Let's get you the right info. What can I help you with?",
    next_bus_eta: 'Next Bus ETA',
    scheduled_departures: 'Scheduled Departures',
    route_overview: 'Route Overview',
    ask_question: 'Ask a Question',
    weekday_or_weekend: 'Is this for weekdays or weekends?',
    weekdays: 'Weekdays',
    weekends: 'Weekends',
    know_stop_id: 'Do you know your 4-digit Stop ID?',
    yes: 'Yes',
    no: 'No',
    enter_stop_id: 'Please enter your Stop ID (3-4 digits)',
    invalid_stop_id: 'Please enter a valid Stop ID (3-4 digits)',
    stop_selected: 'Stop selected',
    select_route: 'Select a route',
    select_direction: 'Which direction are you headed?',
    select_stop: 'Pick your stop or landmark',
    when_next_bus: 'When do you want the next bus?',
    right_now: 'Right now',
    after_15_min: 'After 15 minutes',
    custom_time: 'Custom time',
    when_schedule: 'How should I set the schedule time?',
    specific_datetime: 'Specific date & time',
    time_window: 'Time window',
    pick_date_time: 'Pick a date and time',
    use_date_time: 'Use this date/time',
    start_time: 'Start time',
    end_time: 'End time',
    use_time_window: 'Use this time window',
    date_optional: 'Date (optional)',
    invalid_time: 'Please enter a valid time.',
    loading: 'Loading',
    checking_bustime: 'Checking real-time bus data',
    no_buses_soon: 'No buses arriving within {minutes} minutes. Would you like to check the schedule instead?',
    check_schedule: 'Check Schedule',
    try_different_stop: 'Try Different Stop',
    network_error: 'Network error. Please check your connection.',
    session_expired: 'Session ended after 5 minutes of inactivity. Start a new chat when you\'re ready.',
    session_cleared: 'Session cleared. Ask a new question anytime.',
    back: 'Back',
    cancel: 'Cancel',
    send: 'Send',
    thinking: 'Thinking...',
    delayed: 'Delayed',
    on_time: 'On Time',
    due: 'Due',
    minutes_short: 'min',
    offline_mode: 'Offline - showing cached data',
    online_restored: 'Connection restored',
  },
  es: {
    greeting_morning: 'Buenos días',
    greeting_afternoon: 'Buenas tardes',
    greeting_evening: 'Buenas tardes',
    greeting_night: 'Buenas noches',
    greeting_suffix: '¿En qué puedo ayudarte?',
    next_bus_eta: '🚌 ¿Cuándo llega mi próximo bus?',
    scheduled_departures: '📅 Ver horario de buses',
    route_overview: 'Vista General de Ruta',
    ask_question: 'Hacer una Pregunta',
    weekday_or_weekend: '?Es para d?as de semana o fines de semana?',
    weekdays: 'D?as de semana',
    weekends: 'Fines de semana',
    know_stop_id: '¿Conoces tu ID de parada de 4 dígitos?',
    yes: 'Sí',
    no: 'No',
    enter_stop_id: 'Por favor ingresa tu ID de parada (3-4 dígitos)',
    invalid_stop_id: 'Por favor ingresa un ID de parada válido (3-4 dígitos)',
    stop_selected: 'Parada seleccionada',
    select_route: 'Selecciona una ruta',
    select_direction: '¿En qué dirección vas?',
    select_stop: 'Elige tu parada o punto de referencia',
    when_next_bus: '¿Cuándo quieres el próximo bus?',
    right_now: 'Ahora mismo',
    after_15_min: 'Después de 15 minutos',
    custom_time: 'Hora personalizada',
    when_schedule: '¿Cuándo debo verificar el horario?',
    this_morning: 'Esta mañana',
    this_afternoon: 'Esta tarde',
    tomorrow_morning: 'Mañana por la mañana',
    specific_time: 'Hora específica',
    loading: 'Cargando',
    checking_bustime: 'Verificando datos de buses en tiempo real',
    no_buses_soon: 'No hay buses llegando en {minutes} minutos. ¿Te gustaría verificar el horario?',
    check_schedule: 'Verificar Horario',
    try_different_stop: 'Probar Otra Parada',
    network_error: 'Error de red. Por favor verifica tu conexión.',
    session_expired: 'Sesión terminada después de 5 minutos de inactividad. Inicia un nuevo chat cuando estés listo.',
    session_cleared: 'Sesión limpiada. Pregunta cuando quieras.',
    back: 'Atrás',
    cancel: 'Cancelar',
    send: 'Enviar',
    thinking: 'Pensando...',
    delayed: 'Retrasado',
    on_time: 'A Tiempo',
    due: 'Llegando',
    minutes_short: 'min',
    offline_mode: 'Sin conexión - mostrando datos en caché',
    online_restored: 'Conexión restaurada',
  }
};

// ====== STATE MANAGEMENT ======
const AppState = {
  // Session
  sessionId: null,
  language: 'en',

  // History
  chatHistory: [],

  // Wizard state
  wizardActive: false,
  wizardStep: null, // 'intent', 'stop_id', 'route', 'direction', 'stop', 'time'
  wizardStack: [], // For back button functionality
  intent: null, // 'eta', 'schedule', 'route_info'
  serviceDay: null, // 'weekdays' | 'weekends'
  route: null,
  direction: null,
  stopId: null,
  stopName: null,
  timeframe: null,
  expected: null, // 'stop_id', 'timeframe', 'freeform'

  // UI state
  loading: false,
  currentRequest: null, // AbortController

  // Network state
  isOnline: navigator.onLine,
  offlineCache: {},

  // Inactivity timer
  inactivityTimer: null,
};

// ====== TRANSLATION HELPER ======
function t(key) {
  const lang = AppState.language;
  return TRANSLATIONS[lang]?.[key] || TRANSLATIONS.en[key] || key;
}

// ====== LOCALSTORAGE HELPERS ======
function loadState() {
  try {
    // Load history
    const historyRaw = localStorage.getItem(STORAGE_KEYS.HISTORY);
    if (historyRaw) {
      const parsed = JSON.parse(historyRaw);
      if (Array.isArray(parsed)) {
        AppState.chatHistory = parsed.slice(-CONFIG.MAX_HISTORY);
      }
    }

    // Load session ID
    AppState.sessionId = localStorage.getItem(STORAGE_KEYS.SESSION) || null;

    // Load wizard state
    const stateRaw = localStorage.getItem(STORAGE_KEYS.STATE);
    if (stateRaw) {
      const parsed = JSON.parse(stateRaw);
      Object.assign(AppState, {
        wizardActive: parsed.wizardActive || false,
        wizardStep: parsed.wizardStep || null,
        intent: parsed.intent || null,
        serviceDay: parsed.serviceDay || null,
        route: parsed.route || null,
        direction: parsed.direction || null,
        stopId: parsed.stopId || null,
        stopName: parsed.stopName || null,
        timeframe: parsed.timeframe || null,
      });
    }

    // Load language
    AppState.language = localStorage.getItem(STORAGE_KEYS.LANGUAGE) || 'en';

    // Load offline cache
    const cacheRaw = localStorage.getItem(STORAGE_KEYS.OFFLINE_CACHE);
    if (cacheRaw) {
      AppState.offlineCache = JSON.parse(cacheRaw);
    }
  } catch (e) {
    console.error('Error loading state:', e);
  }
}

function saveState() {
  try {
    // Save history (last 50 messages)
    localStorage.setItem(
      STORAGE_KEYS.HISTORY,
      JSON.stringify(AppState.chatHistory.slice(-CONFIG.MAX_HISTORY))
    );

    // Save session
    if (AppState.sessionId) {
      localStorage.setItem(STORAGE_KEYS.SESSION, AppState.sessionId);
    }

    // Save wizard state
    localStorage.setItem(
      STORAGE_KEYS.STATE,
      JSON.stringify({
        wizardActive: AppState.wizardActive,
        wizardStep: AppState.wizardStep,
        intent: AppState.intent,
        serviceDay: AppState.serviceDay,
        route: AppState.route,
        direction: AppState.direction,
        stopId: AppState.stopId,
        stopName: AppState.stopName,
        timeframe: AppState.timeframe,
      })
    );

    // Save language
    localStorage.setItem(STORAGE_KEYS.LANGUAGE, AppState.language);

    // Save offline cache
    localStorage.setItem(
      STORAGE_KEYS.OFFLINE_CACHE,
      JSON.stringify(AppState.offlineCache)
    );
  } catch (e) {
    console.error('Error saving state:', e);
  }
}

function clearState() {
  AppState.chatHistory = [];
  AppState.sessionId = null;
  AppState.wizardActive = false;
  AppState.wizardStep = null;
  AppState.wizardStack = [];
  AppState.intent = null;
  AppState.serviceDay = null;
  AppState.route = null;
  AppState.direction = null;
  stopId = null;
  AppState.stopName = null;
  AppState.timeframe = null;
  AppState.expected = null;

  try {
    localStorage.removeItem(STORAGE_KEYS.HISTORY);
    localStorage.removeItem(STORAGE_KEYS.SESSION);
    localStorage.removeItem(STORAGE_KEYS.STATE);
  } catch (e) {
    console.error('Error clearing state:', e);
  }

  const messagesDiv = document.getElementById('chat-messages');
  if (messagesDiv) {
    messagesDiv.innerHTML = '';
  }
}

// ====== SESSION MANAGEMENT ======
function scheduleInactivityTimeout() {
  if (AppState.inactivityTimer) {
    clearTimeout(AppState.inactivityTimer);
  }
  AppState.inactivityTimer = setTimeout(() => {
    endSession(false);
  }, CONFIG.SESSION_TIMEOUT_MS);
}

function endSession(manual = true) {
  clearState();

  if (AppState.inactivityTimer) {
    clearTimeout(AppState.inactivityTimer);
    AppState.inactivityTimer = null;
  }

  const endRow = document.getElementById('chat-end-row');
  if (endRow) endRow.setAttribute('hidden', '');

  const msg = manual ? t('session_cleared') : t('session_expired');
  appendBubble(msg, 'bot');
  AppState.chatHistory.push({ role: 'assistant', content: msg });

  // Restart wizard
  setTimeout(() => {
    startGreeting();
  }, 500);

  saveState();
}

// ====== DOM HELPERS ======
function el(id) {
  return document.getElementById(id);
}

function renderMarkdown(text) {
  // Escape HTML first to prevent XSS
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return escaped
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n\n/g, '<br><br>')
    .replace(/\n/g, '<br>');
}

const BOT_AVATAR_SVG = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M4 16c0 .88.39 1.67 1 2.22V20c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h8v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4S4 2.5 4 6v10zm3.5 1c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm9 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm1.5-6H6V6h12v5z"/></svg>';

function _formatTime(d = new Date()) {
  let h = d.getHours();
  const m = d.getMinutes();
  const ampm = h >= 12 ? 'PM' : 'AM';
  h = h % 12; if (h === 0) h = 12;
  return `${h}:${m.toString().padStart(2, '0')} ${ampm}`;
}

function appendBubble(text, who = 'user', options = {}) {
  const wrap = el('chat-messages');
  const bubble = document.createElement('div');
  bubble.className = who === 'user' ? 'bubble user' : 'bubble bot';

  if (options.loading) {
    bubble.classList.add('loading');
    bubble.innerHTML = `<span class="typing-dots"><span></span><span></span><span></span></span>`;
  } else if (who === 'bot') {
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = text;
  }

  if (options.delayed) {
    bubble.classList.add('delayed');
  }

  // Wrap in a msg-row with avatar (for bot) and timestamp.
  const row = document.createElement('div');
  row.className = `msg-row ${who}`;

  // Group consecutive same-role messages by hiding avatar/time on follow-ups.
  const prev = wrap.lastElementChild;
  if (prev && prev.classList && prev.classList.contains('msg-row') &&
      prev.classList.contains(who)) {
    row.classList.add('grouped');
  }

  if (who === 'bot') {
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.innerHTML = BOT_AVATAR_SVG;
    row.appendChild(avatar);

    const body = document.createElement('div');
    body.className = 'msg-body';
    body.appendChild(bubble);

    if (!options.loading) {
      const time = document.createElement('div');
      time.className = 'msg-time';
      time.textContent = _formatTime();
      body.appendChild(time);
    }
    row.appendChild(body);
  } else {
    row.appendChild(bubble);
    const time = document.createElement('div');
    time.className = 'msg-time';
    time.textContent = _formatTime();
    row.appendChild(time);
  }

  wrap.appendChild(row);
  wrap.scrollTop = wrap.scrollHeight;
  return bubble;
}

function appendActionBubble(contentBuilder) {
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

function appendProgressIndicator() {
  const steps = ['intent', 'service_day', 'stop_id', 'route', 'direction', 'stop', 'time'];
  const currentIndex = steps.indexOf(AppState.wizardStep);
  if (currentIndex === -1) return;

  const wrap = el('chat-messages');
  const progress = document.createElement('div');
  progress.className = 'wizard-progress';
  progress.innerHTML = steps
    .slice(0, currentIndex + 1)
    .map((step, i) => {
      const isActive = i === currentIndex;
      const isCompleted = i < currentIndex;
      return `<span class="progress-dot ${isActive ? 'active' : ''} ${
        isCompleted ? 'completed' : ''
      }"></span>`;
    })
    .join('');

  wrap.appendChild(progress);
  wrap.scrollTop = wrap.scrollHeight;
}

// ====== NETWORK HELPERS ======
async function fetchWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), CONFIG.API_TIMEOUT);

  // Store controller for cancellation
  AppState.currentRequest = controller;

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    clearTimeout(timeout);
    AppState.currentRequest = null;
    return response;
  } catch (error) {
    clearTimeout(timeout);
    AppState.currentRequest = null;
    throw error;
  }
}

function cancelCurrentRequest() {
  if (AppState.currentRequest) {
    AppState.currentRequest.abort();
    AppState.currentRequest = null;
  }
}

// ====== STOP ID VALIDATION ======
function normalizeStopId(text) {
  const digits = (text || '').replace(/[^0-9]/g, '');
  if (!digits || digits.length > 4) {
    return null;
  }
  return digits.padStart(4, '0');
}

function validateStopId(text) {
  const normalized = normalizeStopId(text);
  if (!normalized) {
    return { valid: false, normalized: null, message: t('invalid_stop_id') };
  }
  return { valid: true, normalized, message: null };
}

// ====== USER RATINGS ======
function addRatingButtons(botBubble, msgIdx, userMessage, botAnswer) {
  const row = document.createElement('div');
  row.className = 'rating-row';

  const label = document.createElement('span');
  label.className = 'rating-label';
  label.textContent = 'Useful?';
  row.appendChild(label);

  [1, -1].forEach(rating => {
    const btn = document.createElement('button');
    btn.className = 'rating-btn';
    btn.title = rating === 1 ? 'Helpful' : 'Not helpful';
    btn.textContent = rating === 1 ? '\uD83D\uDC4D' : '\uD83D\uDC4E';
    btn.addEventListener('click', () => {
      if (row.dataset.rated) return;
      row.dataset.rated = '1';
      row.querySelectorAll('.rating-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: AppState.sessionId,
          message_index: msgIdx,
          rating,
          user_message: (userMessage || '').slice(0, 200),
          answer_preview: (botAnswer || '').slice(0, 200),
        }),
      }).catch(() => {});
    });
    row.appendChild(btn);
  });

  botBubble.appendChild(row);
}

// ====== ANALYTICS ======
function trackEvent(eventName, data = {}) {
  try {
    // Send to backend analytics
    const payload = {
      event: eventName,
      timestamp: new Date().toISOString(),
      session_id: AppState.sessionId,
      language: AppState.language,
      ...data,
    };

    // Could send to backend endpoint
    // fetch('/api/analytics', { method: 'POST', body: JSON.stringify(payload) });

    console.log('[Analytics]', payload);
  } catch (e) {
    // Fail silently
  }
}

// ====== OFFLINE MODE ======
function cacheOfflineData(key, data) {
  try {
    AppState.offlineCache[key] = {
      data,
      timestamp: Date.now(),
    };
    localStorage.setItem(
      STORAGE_KEYS.OFFLINE_CACHE,
      JSON.stringify(AppState.offlineCache)
    );
  } catch (e) {
    console.error('Error caching data:', e);
  }
}

function getOfflineData(key, maxAge = 5 * 60 * 1000) {
  // 5 minutes
  const cached = AppState.offlineCache[key];
  if (!cached) return null;

  if (Date.now() - cached.timestamp > maxAge) {
    delete AppState.offlineCache[key];
    return null;
  }

  return cached.data;
}

// Listen for online/offline events
window.addEventListener('online', () => {
  AppState.isOnline = true;
  appendBubble(t('online_restored'), 'bot');
  trackEvent('network_online');
});

window.addEventListener('offline', () => {
  AppState.isOnline = false;
  appendBubble(t('offline_mode'), 'bot');
  trackEvent('network_offline');
});

// ======================================
// WIZARD FLOW (MATCHING FLOWCHART)
// ======================================

function startGreeting() {
  const hour = new Date().getHours();
  let greetingKey = 'greeting_evening';
  if (hour >= 5 && hour < 12) {
    greetingKey = 'greeting_morning';
  } else if (hour >= 12 && hour < 17) {
    greetingKey = 'greeting_afternoon';
  } else if (hour >= 17 && hour < 21) {
    greetingKey = 'greeting_evening';
  } else {
    greetingKey = 'greeting_night';
  }

  const greeting = `${t(greetingKey)}. ${t('greeting_suffix')}`;
  appendBubble(greeting, 'bot');
  AppState.chatHistory.push({ role: 'assistant', content: greeting });
  saveState();
  showStarterQuestions();
}

function showStarterQuestions() {
  const container = document.getElementById('starter-questions');
  if (!container) return;
  container.innerHTML = '';

  const starters = AppState.language === 'es'
    ? [
        'Próximo bus en Rosa Parks',
        '¿Cómo llego a Butler Plaza?',
        '¿Cuándo empieza la ruta 1 mañana?',
        '¿Qué rutas van a UF?',
      ]
    : [
        'Next bus at Rosa Parks',
        'How do I get to Butler Plaza?',
        'When does Route 1 start tomorrow?',
        'What routes go to UF?',
      ];

  starters.forEach((q) => {
    const btn = document.createElement('button');
    btn.className = 'starter-q';
    btn.textContent = q;
    btn.addEventListener('click', () => {
      dismissStarterQuestions();
      const inputField = document.getElementById('chat-input');
      if (inputField) {
        inputField.value = q;
        sendMessage();
      }
    });
    container.appendChild(btn);
  });
}

function dismissStarterQuestions() {
  const container = document.getElementById('starter-questions');
  if (container) container.innerHTML = '';
}

function showIntentSelection() {
  AppState.wizardActive = true;
  AppState.wizardStep = 'intent';
  AppState.wizardStack = [];

  appendActionBubble((container) => {
    // Simplified to match flowchart: ETA or Schedule
    const options = [
      { label: '🚌 When is my next bus?', intent: 'eta', subtitle: 'Real-time arrivals' },
      { label: '📅 View bus schedule', intent: 'schedule', subtitle: 'Planned times' },
    ];

    options.forEach((option) => {
      const btn = document.createElement('button');
      btn.className = 'chat-btn';
      btn.innerHTML = `<span class="btn-label">${option.label}</span><small class="btn-subtitle">${option.subtitle}</small>`;
      btn.addEventListener('click', () => handleIntentSelection(option.intent));
      container.appendChild(btn);
    });
  });

  trackEvent('wizard_intent_shown');
}

function handleIntentSelection(intent) {
  AppState.intent = intent;
  AppState.wizardStack.push('intent');

  trackEvent('wizard_intent_selected', { intent });

  if (intent === 'schedule') {
    AppState.wizardStep = 'service_day';
    askServiceDay();
    return;
  }

  AppState.wizardStep = 'stop_id';
  askStopIdKnown();
}

function askServiceDay() {
  appendBubble(t('weekday_or_weekend'), 'bot');
  appendActionBubble((container) => {
    const backBtn = document.createElement('button');
    backBtn.className = 'chat-btn back';
    backBtn.textContent = t('back');
    backBtn.addEventListener('click', goBack);
    container.appendChild(backBtn);

    [
      { label: t('weekdays'), value: 'weekdays' },
      { label: t('weekends'), value: 'weekends' },
    ].forEach((option) => {
      const btn = document.createElement('button');
      btn.className = 'chat-btn';
      btn.textContent = option.label;
      btn.addEventListener('click', () => {
        AppState.serviceDay = option.value;
        AppState.wizardStack.push('service_day');
        AppState.wizardStep = 'stop_id';
        askStopIdKnown();
      });
      container.appendChild(btn);
    });
  });
}

function askStopIdKnown() {
  appendBubble(t('know_stop_id'), 'bot');
  appendActionBubble((container) => {
    const addBackButton = AppState.wizardStack.length > 0;

    if (addBackButton) {
      const backBtn = document.createElement('button');
      backBtn.className = 'chat-btn back';
      backBtn.textContent = t('back');
      backBtn.addEventListener('click', goBack);
      container.appendChild(backBtn);
    }

    [
      { label: t('yes'), value: 'yes' },
      { label: t('no'), value: 'no' },
    ].forEach((option) => {
      const btn = document.createElement('button');
      btn.className = 'chat-btn';
      btn.textContent = option.label;
      btn.addEventListener('click', () => {
        if (option.value === 'yes') {
          AppState.expected = 'stop_id';
          AppState.wizardStack.push('stop_id_known');
          appendBubble(t('enter_stop_id'), 'bot');
        } else {
          AppState.wizardStack.push('stop_id_unknown');
          AppState.wizardStep = 'route';
          appendBubble(t('select_route'), 'bot');
          showRouteOptions();
        }
      });
      container.appendChild(btn);
    });
  });
}

function goBack() {
  if (AppState.wizardStack.length === 0) return;

  const previousStep = AppState.wizardStack.pop();

  // Reset to previous step
  if (previousStep === 'intent') {
    showIntentSelection();
  } else if (previousStep === 'service_day') {
    AppState.wizardStep = 'service_day';
    askServiceDay();
  } else if (previousStep === 'stop_id_known' || previousStep === 'stop_id_unknown') {
    askStopIdKnown();
  } else if (previousStep === 'route') {
    showRouteOptions();
  } else if (previousStep === 'direction') {
    showDirectionOptions();
  }

  trackEvent('wizard_back_pressed', { from: previousStep });
}

async function handleStopIdInput(stopId) {
  const validation = validateStopId(stopId);

  if (!validation.valid) {
    appendBubble(validation.message, 'bot');
    return;
  }

  AppState.stopId = validation.normalized;
  AppState.expected = null;
  AppState.wizardStack.push('stop_entered');

  appendBubble(`${t('stop_selected')}: ${AppState.stopId}`, 'bot');

  // ETA-FIRST APPROACH: Try to fetch predictions from Bustime
  await checkBustimeForStop(AppState.stopId);
}

async function checkBustimeForStop(stopId) {
  const loadingBubble = appendBubble(t('checking_bustime'), 'bot', {
    loading: true,
  });

  try {
    const response = await fetchWithTimeout(
      `${CONFIG.BASE_URL}/api/predictions?stop_id=${encodeURIComponent(stopId)}`
    );

    if (!response.ok) {
      let errMsg = t('network_error');
      let errCode = null;
      try {
        const err = await response.json();
        if (err) {
          errMsg = err.error_message || errMsg;
          errCode = err.error_code || null;
        }
      } catch (_) {
        // ignore parse errors
      }
      loadingBubble.remove();
      appendBubble(errMsg, 'bot');
      if (errCode === 'STOP_NOT_FOUND') {
        appendActionBubble((container) => {
          const routeBtn = document.createElement('button');
          routeBtn.className = 'chat-btn';
          routeBtn.textContent = t('select_route');
          routeBtn.addEventListener('click', () => {
            AppState.stopId = null;
            AppState.wizardStep = 'route';
            showRouteOptions();
          });
          const backBtn = document.createElement('button');
          backBtn.className = 'chat-btn back';
          backBtn.textContent = t('back');
          backBtn.addEventListener('click', goBack);
          container.appendChild(routeBtn);
          container.appendChild(backBtn);
        });
      }
      trackEvent('api_error', { endpoint: 'predictions', error: `HTTP ${response.status}` });
      return;
    }

    const data = await response.json();

    // Remove loading bubble
    loadingBubble.remove();

    // Cache for offline mode
    cacheOfflineData(`predictions_${stopId}`, data);

    const predictions = data.predictions || [];

    if (predictions.length === 0) {
      // No buses at all
      appendBubble(
        t('no_buses_soon').replace('{minutes}', CONFIG.ETA_THRESHOLD_MINUTES),
        'bot'
      );

      // Auto-suggest next scheduled bus for this stop
      AppState.intent = 'schedule';
      AppState.timeframe = 'now';
      submitScheduleQuery();
      return;
    }

    // Check if any buses arriving within threshold
    const upcomingBuses = predictions.filter((p) => {
      const mins = p.minutes;
      if (mins === 'DUE') return true;
      const minsNum = parseInt(mins, 10);
      return !isNaN(minsNum) && minsNum <= CONFIG.ETA_THRESHOLD_MINUTES;
    });

    if (upcomingBuses.length === 0) {
      // Buses exist but all are > 45 minutes away
      appendBubble(
        t('no_buses_soon').replace('{minutes}', CONFIG.ETA_THRESHOLD_MINUTES),
        'bot'
      );

      if (AppState.intent === 'eta') {
        appendActionBubble((container) => {
          const scheduleBtn = document.createElement('button');
          scheduleBtn.className = 'chat-btn';
          scheduleBtn.textContent = t('check_schedule');
          scheduleBtn.addEventListener('click', () => {
            AppState.intent = 'schedule';
            proceedToSchedule();
          });

          const showAnywayBtn = document.createElement('button');
          showAnywayBtn.className = 'chat-btn back';
          showAnywayBtn.textContent = 'Show All ETAs';
          showAnywayBtn.addEventListener('click', () => {
            displayPredictions(predictions);
          });

          container.appendChild(scheduleBtn);
          container.appendChild(showAnywayBtn);
        });
      } else {
        proceedToSchedule();
      }
      return;
    }

    // Display ETAs
    displayPredictions(upcomingBuses);
  } catch (error) {
    loadingBubble.remove();

    // Try offline cache
    if (!AppState.isOnline) {
      const cached = getOfflineData(`predictions_${stopId}`);
      if (cached && cached.predictions) {
        appendBubble(t('offline_mode'), 'bot');
        displayPredictions(cached.predictions);
        return;
      }
    }

    appendBubble(t('network_error'), 'bot');
    trackEvent('api_error', { endpoint: 'predictions', error: error.message });
  }
}

function displayPredictions(predictions) {
  if (!predictions || predictions.length === 0) {
    appendBubble('No predictions available.', 'bot');
    return;
  }

  predictions.forEach((p) => {
    const mins = (p.minutes || '').toString().toUpperCase();
    const minsLabel = mins === 'DUE' ? t('due') : `${mins} ${t('minutes_short')}`;
    const delayLabel = p.delayed ? ` • ${t('delayed')}` : '';

    const text = `🚌 Route ${p.route} to ${p.destination} - ${minsLabel}${delayLabel}\nBus #${
      p.vehicle_id || '—'
    } • ${p.arrival_time || '—'}`;

    appendBubble(text, 'bot', { delayed: p.delayed });
  });

  AppState.wizardActive = false;
  trackEvent('predictions_displayed', { count: predictions.length });
}

function proceedToSchedule() {
  if (!AppState.serviceDay) {
    AppState.wizardStep = 'service_day';
    askServiceDay();
    return;
  }
  // Ask for route if not known
  if (!AppState.route) {
    AppState.wizardStep = 'route';
    appendBubble(t('select_route'), 'bot');
    showRouteOptions();
  } else {
    askScheduleTime();
  }
}

async function showRouteOptions() {
  const loadingBubble = appendBubble(t('loading'), 'bot', { loading: true });

  try {
    const response = await fetchWithTimeout(`${CONFIG.BASE_URL}/api/routes`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();
    const routes = data.routes || [];

    loadingBubble.remove();

    // Cache for offline
    cacheOfflineData('routes', data);

    if (routes.length === 0) {
      appendBubble('No routes available.', 'bot');
      return;
    }

    appendActionBubble((container) => {
      container.classList.add('route-grid');
      // Add back button if applicable
      if (AppState.wizardStack.length > 0) {
        const backBtn = document.createElement('button');
        backBtn.className = 'chat-btn back';
        backBtn.textContent = t('back');
        backBtn.addEventListener('click', goBack);
        container.appendChild(backBtn);
      }

      // Show all routes
      routes.forEach((route) => {
        const btn = document.createElement('button');
        btn.className = 'chat-btn';
        btn.textContent = `Route ${route.id}`;
        btn.addEventListener('click', () => handleRouteSelection(route));
        container.appendChild(btn);
      });
    });
  } catch (error) {
    loadingBubble.remove();

    // Try offline cache
    if (!AppState.isOnline) {
      const cached = getOfflineData('routes');
      if (cached && cached.routes) {
        appendBubble(t('offline_mode'), 'bot');
        // Re-render with cached data
        // ... (similar logic)
      }
    }

    appendBubble(t('network_error'), 'bot');
    trackEvent('api_error', { endpoint: 'routes', error: error.message });
  }
}

function handleRouteSelection(route) {
  AppState.route = route.id;
  AppState.wizardStack.push('route');

  appendBubble(`Route ${AppState.route} selected`, 'bot');

  if (AppState.intent === 'route_info') {
    // Send to agent for route overview
    sendAgentMessage(`Route ${AppState.route} overview`);
    return;
  }

  // For ETA/Schedule, proceed to direction
  AppState.wizardStep = 'direction';
  showDirectionOptions();
}

async function showDirectionOptions() {
  const loadingBubble = appendBubble(t('loading'), 'bot', { loading: true });

  try {
    const response = await fetchWithTimeout(
      `${CONFIG.BASE_URL}/api/directions?route_id=${encodeURIComponent(AppState.route)}`
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();
    const directions = data.directions || [];

    loadingBubble.remove();

    if (directions.length === 0) {
      appendBubble('No directions available.', 'bot');
      return;
    }

    appendBubble(t('select_direction'), 'bot');

    appendActionBubble((container) => {
      // Add back button
      const backBtn = document.createElement('button');
      backBtn.className = 'chat-btn back';
      backBtn.textContent = t('back');
      backBtn.addEventListener('click', goBack);
      container.appendChild(backBtn);

      directions.forEach((d) => {
        const btn = document.createElement('button');
        btn.className = 'chat-btn';
        btn.textContent = d.name || d.id;
        btn.addEventListener('click', () => handleDirectionSelection(d));
        container.appendChild(btn);
      });
    });
  } catch (error) {
    loadingBubble.remove();
    appendBubble(t('network_error'), 'bot');
    trackEvent('api_error', { endpoint: 'directions', error: error.message });
  }
}

function handleDirectionSelection(direction) {
  AppState.direction = direction.id;
  AppState.wizardStack.push('direction');

  appendBubble(`Direction: ${direction.name}`, 'bot');

  // Proceed to stop selection
  AppState.wizardStep = 'stop';
  showStopOptions();
}

async function showStopOptions() {
  const loadingBubble = appendBubble(t('loading'), 'bot', { loading: true });

  try {
    const params = new URLSearchParams({
      route_id: AppState.route,
      direction_id: AppState.direction,
    });

    const response = await fetchWithTimeout(
      `${CONFIG.BASE_URL}/api/stops?${params.toString()}`
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();
    const stops = data.stops || [];

    loadingBubble.remove();

    if (stops.length === 0) {
      appendBubble('No stops available.', 'bot');
      return;
    }

    appendBubble(t('select_stop'), 'bot');

    appendActionBubble((container) => {
      // Add back button
      const backBtn = document.createElement('button');
      backBtn.className = 'chat-btn back';
      backBtn.textContent = t('back');
      backBtn.addEventListener('click', goBack);
      container.appendChild(backBtn);

      // Show first 12 stops
      stops.slice(0, 12).forEach((stop) => {
        const btn = document.createElement('button');
        btn.className = 'chat-btn';
        btn.textContent = `${stop.name} (${stop.id})`;
        btn.addEventListener('click', () => handleStopSelection(stop));
        container.appendChild(btn);
      });
    });
  } catch (error) {
    loadingBubble.remove();
    appendBubble(t('network_error'), 'bot');
    trackEvent('api_error', { endpoint: 'stops', error: error.message });
  }
}

function handleStopSelection(stop) {
  AppState.stopId = stop.id;
  AppState.stopName = stop.name;
  AppState.wizardStack.push('stop');

  appendBubble(`${t('stop_selected')}: ${stop.name}`, 'bot');

  // If ETA intent, check Bustime
  if (AppState.intent === 'eta') {
    checkBustimeForStop(AppState.stopId);
  } else {
    // Schedule intent, ask for time
    askScheduleTime();
  }
}

function askScheduleTime() {
  AppState.wizardStep = 'time';
  appendBubble(t('when_schedule'), 'bot');

  appendActionBubble((container) => {
    const backBtn = document.createElement('button');
    backBtn.className = 'chat-btn back';
    backBtn.textContent = t('back');
    backBtn.addEventListener('click', goBack);
    container.appendChild(backBtn);

    const specificBtn = document.createElement('button');
    specificBtn.className = 'chat-btn';
    specificBtn.textContent = t('specific_datetime');
    specificBtn.addEventListener('click', showSpecificDateTimeInput);
    container.appendChild(specificBtn);

    const windowBtn = document.createElement('button');
    windowBtn.className = 'chat-btn';
    windowBtn.textContent = t('time_window');
    windowBtn.addEventListener('click', showTimeWindowInput);
    container.appendChild(windowBtn);
  });
}

function handleTimeSelection(timeframe) {
  if (timeframe === 'custom') {
    AppState.expected = 'timeframe';
    appendBubble(
      'Type the day/time you want (e.g., "tomorrow at 8am" or "next Monday 5pm").',
      'bot'
    );
    return;
  }

  AppState.timeframe = timeframe;
  submitScheduleQuery();
}

function showSpecificDateTimeInput() {
  appendBubble(t('pick_date_time'), 'bot');
  appendActionBubble((container) => {
    const dt = document.createElement('input');
    dt.type = 'datetime-local';
    dt.className = 'chat-input-inline';
    dt.setAttribute('aria-label', t('pick_date_time'));

    const btn = document.createElement('button');
    btn.className = 'chat-btn';
    btn.textContent = t('use_date_time');
    btn.addEventListener('click', () => {
      if (!dt.value) {
        appendBubble(t('invalid_time'), 'bot');
        return;
      }
      const formatted = formatDateTime(dt.value);
      AppState.timeframe = formatted;
      submitScheduleQuery();
    });

    container.appendChild(dt);
    container.appendChild(btn);
  });
}

function showTimeWindowInput() {
  appendBubble(t('time_window'), 'bot');
  appendActionBubble((container) => {
    const date = document.createElement('input');
    date.type = 'date';
    date.className = 'chat-input-inline';
    date.setAttribute('aria-label', t('date_optional'));

    const start = document.createElement('input');
    start.type = 'time';
    start.className = 'chat-input-inline';
    start.setAttribute('aria-label', t('start_time'));

    const end = document.createElement('input');
    end.type = 'time';
    end.className = 'chat-input-inline';
    end.setAttribute('aria-label', t('end_time'));

    const btn = document.createElement('button');
    btn.className = 'chat-btn';
    btn.textContent = t('use_time_window');
    btn.addEventListener('click', () => {
      if (!start.value || !end.value) {
        appendBubble(t('invalid_time'), 'bot');
        return;
      }
      const startText = formatTime(start.value);
      const endText = formatTime(end.value);
      const dateText = date.value ? ` on ${date.value}` : '';
      AppState.timeframe = `between ${startText} and ${endText}${dateText}`;
      submitScheduleQuery();
    });

    container.appendChild(date);
    container.appendChild(start);
    container.appendChild(end);
    container.appendChild(btn);
  });
}

function formatDateTime(value) {
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  const dateStr = dt.toISOString().slice(0, 10);
  return `${dateStr} at ${formatTime(value.split('T')[1])}`;
}

function formatTime(value) {
  if (!value) return '';
  const parts = value.split(':');
  let hh = parseInt(parts[0], 10);
  const mm = parts[1] || '00';
  const ap = hh >= 12 ? 'pm' : 'am';
  hh = hh % 12;
  if (hh === 0) hh = 12;
  return `${hh}:${mm} ${ap}`;
}


function submitScheduleQuery() {
  // Build final query and send to agent
  const parts = ['Schedule'];
  if (AppState.serviceDay) parts.push(AppState.serviceDay);
  if (AppState.route) parts.push(`Route ${AppState.route}`);
  if (AppState.direction) parts.push(AppState.direction);
  if (AppState.stopName) parts.push(`at ${AppState.stopName}`);
  else if (AppState.stopId) parts.push(`stop ${AppState.stopId}`);
  if (AppState.timeframe) parts.push(AppState.timeframe);

  const query = parts.join(' ');
  sendAgentMessage(query);
}

function isSessionMessage(text) {
  if (!text) return false;
  const en = TRANSLATIONS.en;
  const es = TRANSLATIONS.es;
  return text === en.session_cleared || text === en.session_expired ||
         text === es.session_cleared || text === es.session_expired;
}

function isGreetingMessage(text) {
  if (!text) return false;
  const en = TRANSLATIONS.en;
  const es = TRANSLATIONS.es;
  const keys = ['greeting_morning', 'greeting_afternoon', 'greeting_evening', 'greeting_night'];
  return keys.some(k => text.startsWith(en[k]) || text.startsWith(es[k]));
}

function formatDirectionLabel(raw) {
  if (!raw) return '';
  const text = String(raw).replace(/\s+/g, ' ').trim();
  const parts = text.split(' - ');
  if (parts.length >= 2) {
    const dir = parts[0].trim().toLowerCase();
    let dest = parts.slice(1).join(' - ').trim();
    dest = dest.replace(/^to\s+/i, '').replace(/^to\s+to\s+/i, 'to ');
    const dirLabel = dir.toUpperCase();
    return `${dirLabel} ? ${dest}`;
  }
  return text;
}


// ====== AGENT API CALL (STREAMING) ======
async function sendAgentMessage(message) {
  cancelCurrentRequest();

  const inputField = el('chat-input');
  if (inputField) {
    inputField.value = '';
    inputField.dispatchEvent(new Event('input'));
  }

  appendBubble(message, 'user');
  AppState.chatHistory.push({ role: 'user', content: message });
  saveState();

  // Create bot bubble immediately — shows typing animation
  const botBubble = appendBubble('', 'bot', { loading: true });
  let streamedText = '';
  let tokenReceived = false;

  const sendBtn = el('chat-send');
  if (sendBtn) sendBtn.disabled = true;

  const scrollDown = () => {
    const wrap = el('chat-messages');
    if (wrap) wrap.scrollTop = wrap.scrollHeight;
  };

  try {
    const payload = {
      message,
      history: AppState.chatHistory.slice(-12),
      session_id: AppState.sessionId,
      language: AppState.language,
    };

    const response = await fetchWithTimeout(`${CONFIG.BASE_URL}${CONFIG.AGENT_ENDPOINT}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        let event;
        try { event = JSON.parse(raw); } catch { continue; }

        if (event.type === 'token') {
          if (!tokenReceived) {
            // Switch bubble from typing dots to live text
            botBubble.classList.remove('loading');
            botBubble.innerHTML = '';
            tokenReceived = true;
          }
          streamedText += event.text;
          botBubble.innerHTML = renderMarkdown(streamedText);
          scrollDown();
        } else if (event.type === 'done') {
          finalData = event;
        } else if (event.type === 'error') {
          throw new Error(event.text || 'Stream error');
        }
        // 'status' events keep the typing dots visible — no DOM change needed
      }
    }

    // Finalize bubble
    const finalAnswer = (finalData && finalData.answer) || streamedText || 'No response.';
    if (!tokenReceived) {
      botBubble.classList.remove('loading');
      botBubble.innerHTML = renderMarkdown(finalAnswer);
    } else {
      botBubble.innerHTML = renderMarkdown(finalAnswer); // sync in case of rounding
    }
    scrollDown();

    if (finalData) {
      if (finalData.session_id) {
        AppState.sessionId = finalData.session_id;
        saveState();
      }
      // Buttons are wizard-only — chat is free-form text, no clickable option tiles
    }

    AppState.chatHistory.push({ role: 'assistant', content: finalAnswer });
    saveState();
    scheduleInactivityTimeout();
    trackEvent('agent_message_sent', { intent: AppState.intent });

    // Rating buttons — only for real agent responses, not greetings or session messages
    if (finalData && !isGreetingMessage(finalAnswer) && !isSessionMessage(finalAnswer)) {
      addRatingButtons(botBubble, AppState.chatHistory.length - 1, message, finalAnswer);
      scrollDown(); // re-scroll after rating row is appended
    }

  } catch (error) {
    if (!tokenReceived) {
      botBubble.remove();
      appendBubble(t('network_error'), 'bot');
      AppState.chatHistory.push({ role: 'assistant', content: t('network_error') });
      saveState();
    }
    trackEvent('agent_error', { error: error.message });
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    if (inputField) inputField.focus();
  }
}

// ====== SEND BUTTON / VOICE HINT ======
let _voiceHintTimer = null;
function showVoiceHint() {
  const hint = document.getElementById('voice-hint');
  if (!hint) return;
  hint.textContent = AppState.language === 'es'
    ? 'Toca el micrófono del teclado para dictar.'
    : "Tap the 🎤 on your keyboard to dictate.";
  hint.classList.add('show');
  if (_voiceHintTimer) clearTimeout(_voiceHintTimer);
  _voiceHintTimer = setTimeout(() => hint.classList.remove('show'), 2800);
}

function handleSendClick() {
  const inputField = document.getElementById('chat-input');
  if (!inputField) return;
  if (inputField.value.trim().length === 0) {
    showVoiceHint();
    inputField.focus();
    return;
  }
  sendMessage();
}

// ====== MESSAGE INPUT HANDLING ======
async function sendMessage() {
  const inputField = el('chat-input');
  const msg = inputField.value.trim();
  if (!msg) return;

  // Dismiss starter questions and reveal the End-session link on first user send
  dismissStarterQuestions();
  const endRow = document.getElementById('chat-end-row');
  if (endRow && endRow.hasAttribute('hidden')) endRow.removeAttribute('hidden');

  // Handle expected input (stop ID or timeframe)
  if (AppState.expected === 'stop_id') {
    await handleStopIdInput(msg);
    inputField.value = '';
    scheduleInactivityTimeout();
    return;
  }

  if (AppState.expected === 'timeframe') {
    AppState.timeframe = msg;
    AppState.expected = null;
    appendBubble(msg, 'user');
    AppState.chatHistory.push({ role: 'user', content: msg });
    saveState();
    inputField.value = '';
    submitScheduleQuery();
    scheduleInactivityTimeout();
    return;
  }

  // Regular agent message
  await sendAgentMessage(msg);
}

// ====== LANGUAGE TOGGLE ======
function toggleLanguage() {
  AppState.language = AppState.language === 'en' ? 'es' : 'en';
  saveState();
  trackEvent('language_changed', { language: AppState.language });

  // Update UI text
  const sendBtn = el('chat-send');
  if (sendBtn) sendBtn.setAttribute('aria-label', t('send'));

  // Show confirmation
  appendBubble(
    AppState.language === 'en' ? 'Language changed to English' : 'Idioma cambiado a español',
    'bot'
  );
  // Refresh starter questions in new language (if still visible)
  showStarterQuestions();
}

// ====== INITIALIZATION ======
window.addEventListener('DOMContentLoaded', () => {
  const sendBtn = el('chat-send');
  const inputField = el('chat-input');
  const endBtn = el('chat-end');

  if (!inputField || !sendBtn) {
    console.error('Chat elements missing in DOM.');
    return;
  }

  // Load state from localStorage
  loadState();

  // Strip stale greeting messages so they don't accumulate across sessions
  AppState.chatHistory = AppState.chatHistory.filter(m => !isGreetingMessage(m.content));

  // Restore history (without greetings — a fresh one is shown below)
  if (AppState.chatHistory.length > 0) {
    AppState.chatHistory.forEach((m) => {
      if (!isSessionMessage(m.content)) {
        appendBubble(m.content, m.role === 'user' ? 'user' : 'bot');
      }
    });
    // Returning user with prior turns → reveal End-session now.
    const endRow = document.getElementById('chat-end-row');
    if (endRow) endRow.removeAttribute('hidden');
  }

  // Always show a fresh greeting
  startGreeting();

  // Event listeners
  sendBtn.addEventListener('click', handleSendClick);
  sendBtn.setAttribute('aria-label', t('send'));

  if (endBtn) {
    endBtn.addEventListener('click', () => endSession(true));
  }

  inputField.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Morph send button: empty input → mic icon, typing → send arrow.
  const updateSendButtonState = () => {
    const hasText = inputField.value.trim().length > 0;
    sendBtn.classList.toggle('empty', !hasText);
    sendBtn.setAttribute('aria-label', hasText ? t('send') : 'Voice input hint');
  };
  inputField.addEventListener('input', updateSendButtonState);
  updateSendButtonState();

  // Language toggle (could add button to UI)
  // For now, accessible via console: toggleLanguage()
  window.toggleLanguage = toggleLanguage;

  console.log('RTS Live Assistant v2.0 loaded');
});
