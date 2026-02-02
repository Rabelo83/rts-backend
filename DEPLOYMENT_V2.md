# RTS Backend V2.0 - Deployment Guide

## 🎉 What's New in V2.0

This major update implements **ALL** recommendations from the comprehensive code review, including:

### Backend Improvements
- ✅ **Server-side Session Management** - Secure UUID-based sessions (no more client-generated IDs)
- ✅ **LRU Cache with Size Limits** - Prevents memory exhaustion, tracks hit/miss rates
- ✅ **Standardized Validation** - Shared stop ID validation across frontend/backend
- ✅ **Enhanced Error Handling** - Structured error codes with detailed messages
- ✅ **API Contract Schemas** - TypedDict definitions for all endpoints
- ✅ **Health Check Improvements** - Now includes cache stats and session counts

### Frontend Improvements
- ✅ **ETA-First Wizard Flow** - Matches flowchart exactly, tries Bustime before asking questions
- ✅ **Delay Indicators** - Visual badges show when buses are running late
- ✅ **State Persistence** - Wizard state survives page refreshes
- ✅ **Loading States** - Spinners and feedback for all API calls
- ✅ **Request Cancellation** - AbortController prevents race conditions
- ✅ **Back Buttons** - Navigate through wizard steps
- ✅ **Progress Indicators** - Visual dots show current step
- ✅ **Bilingual Support** - Full Spanish/English translations
- ✅ **Offline Mode** - Caches predictions for offline access
- ✅ **Analytics Tracking** - Event logging for user interactions
- ✅ **Mobile Optimizations** - 44px touch targets, responsive design
- ✅ **Accessibility** - ARIA labels, focus states, high contrast mode support

---

## 📋 Pre-Deployment Checklist

### 1. **Verify Dependencies**
Ensure all Python packages are installed:
```bash
pip install -r requirements.txt
```

### 2. **Test New Utilities**
```bash
python -c "from utils.validation import normalize_stop_id; print(normalize_stop_id('773'))"
# Should output: 0773

python -c "from utils.cache import prediction_cache; prediction_cache.set('test', 'value'); print(prediction_cache.get('test'))"
# Should output: value

python -c "from utils.session_manager import session_manager; sid = session_manager.create_session(); print(f'Session created: {sid}')"
# Should output: Session created: <uuid>
```

### 3. **Environment Variables**
Ensure these are set in your environment (Render, `.env`, etc.):

```bash
# Required
API_KEY=<your_bustime_api_key>
RTPIDATAFEED=bustime
BASE_HOST=riderts.app

# Optional - LLM features
OPENAI_API_KEY=<your_openai_key>
HUMANIZE_ENABLED=true
CHAT_LOG_ENABLED=true
ANALYTICS_ENABLED=true

# Cache tuning (optional, defaults shown)
SCHEDULE_CACHE_TTL=60
PREDICTION_CACHE_TTL=20
```

---

## 🚀 Deployment Steps

### Option A: Deploy to Render (Recommended)

1. **Push to Git**
   ```bash
   git add .
   git commit -m "V2.0: Implement all UX and technical recommendations"
   git push origin main
   ```

2. **Auto-Deploy** (if configured)
   - Render will automatically deploy when it detects the push
   - Monitor build logs in Render dashboard

3. **Manual Deploy** (if needed)
   - Go to Render dashboard → Your service
   - Click "Manual Deploy" → "Deploy latest commit"

4. **Verify Deployment**
   - Visit: `https://your-app.onrender.com/api/health`
   - Should see enhanced health check with cache stats

### Option B: Local Development

1. **Start Backend**
   ```bash
   python -m gunicorn app:app --bind 0.0.0.0:5000 --workers 2
   ```

2. **Test Endpoints**
   ```bash
   # Health check
   curl http://localhost:5000/api/health

   # Predictions with delay indicator
   curl http://localhost:5000/api/predictions?stop_id=0773

   # Agent with server-side session
   curl -X POST http://localhost:5000/api/agent \
     -H "Content-Type: application/json" \
     -d '{"message": "Next bus at stop 773"}'
   ```

3. **Visit Frontend**
   - Tracker: `http://localhost:5000/`
   - Assistant: `http://localhost:5000/chat`

---

## 🧪 Testing Checklist

### Backend Tests

#### 1. **Session Management**
```bash
# Test session creation
curl -X POST http://localhost:5000/api/agent \
  -H "Content-Type: application/json" \
  -d '{"message": "test"}' | jq '.session_id'

# Should return a UUID like: "a1b2c3d4-e5f6-..."
```

#### 2. **Cache Performance**
```bash
# First call (cache miss)
time curl -s "http://localhost:5000/api/predictions?stop_id=0773" > /dev/null

# Second call (cache hit - should be faster)
time curl -s "http://localhost:5000/api/predictions?stop_id=0773" > /dev/null

# Check cache stats
curl http://localhost:5000/api/health | jq '.cache'
```

#### 3. **Error Handling**
```bash
# Invalid stop ID
curl "http://localhost:5000/api/predictions?stop_id=invalid" | jq

# Should return:
# {
#   "error": true,
#   "error_code": "INVALID_STOP_ID",
#   "error_message": "...",
#   "details": { "provided": "invalid" }
# }

# Invalid route ID
curl "http://localhost:5000/api/vehicles?route_id=abc" | jq

# Should return structured error
```

#### 4. **Stop ID Validation**
```bash
# Test normalization
curl "http://localhost:5000/api/validate_stop?stop_id=773" | jq
# Should return: { "valid": true, "normalized": "0773", ... }

curl "http://localhost:5000/api/validate_stop?stop_id=12345" | jq
# Should return: { "valid": false, "error_code": "TOO_MANY_DIGITS", ... }
```

### Frontend Tests

#### 1. **Main Tracker (index.html)**
- [ ] Delay badges appear on delayed buses
- [ ] Character count shows as you type stop ID (e.g., "2/4 digits")
- [ ] Error message appears for stop IDs > 4 digits
- [ ] Auto-refresh works every 30 seconds
- [ ] Mobile: touch targets are at least 44px

#### 2. **Chat Assistant (chat.html)**
- [ ] Language toggle button appears (top-right)
- [ ] Click toggle to switch between EN/ES
- [ ] Wizard starts with 4 intent buttons
- [ ] "Do you know your Stop ID?" shows Yes/No buttons
- [ ] Entering stop ID triggers Bustime check first
- [ ] If no buses <45min, suggests checking schedule
- [ ] Back button appears after first wizard step
- [ ] Progress dots show at top during wizard
- [ ] State persists after page refresh
- [ ] Offline mode shows when network disconnected
- [ ] Loading spinners appear during API calls

#### 3. **ETA-First Flow (Critical Path)**

**Test Scenario:** User wants ETA for stop 0773

1. Open `/chat`
2. Click "Next Bus ETA"
3. Click "Yes" (I know my stop ID)
4. Type "773" and press Enter
5. **Expected:** Loading spinner → "Checking real-time bus data"
6. **Expected:** Predictions display with delay badges if applicable
7. **Expected:** No route/direction questions asked

**Test Scenario:** No buses within 45 minutes

1. Use a stop with no service (e.g., stop 9999)
2. **Expected:** "No buses arriving within 45 minutes"
3. **Expected:** Two buttons: "Check Schedule" / "Try Different Stop"

#### 4. **Wizard Navigation**
1. Start wizard
2. Select "Next Bus ETA"
3. Select "No" (don't know stop ID)
4. **Expected:** Route selection appears with Back button
5. Click Back
6. **Expected:** Returns to "Do you know Stop ID?"
7. Click Back again
8. **Expected:** Returns to intent selection

#### 5. **Language Toggle**
1. Click language toggle (EN/ES button)
2. **Expected:** UI text changes to Spanish
3. Click again
4. **Expected:** UI text changes back to English
5. Refresh page
6. **Expected:** Language preference persists

#### 6. **Offline Mode**
1. Load chat and make a query for stop 0773
2. Open DevTools → Network tab
3. Set throttling to "Offline"
4. Refresh page
5. Ask for stop 0773 again
6. **Expected:** "Offline - showing cached data" message
7. **Expected:** Previous predictions display
8. Set back to "Online"
9. **Expected:** "Connection restored" message

#### 7. **Mobile Testing**
- [ ] All buttons are easily tappable (44px minimum)
- [ ] Input fields don't cause zoom on focus (font-size >= 16px)
- [ ] Prediction header is sticky on scroll
- [ ] Chat messages scroll smoothly
- [ ] Keyboard doesn't cover input field

---

## 📊 Monitoring & Analytics

### Health Endpoint
```bash
curl https://your-app.onrender.com/api/health | jq
```

**Expected Response:**
```json
{
  "status": "healthy",
  "service": "rts-backend",
  "timestamp": "2026-02-02T...",
  "web_index": true,
  "bustime_api": true,
  "backend_basics": true,
  "cache": {
    "predictions": {
      "size": 42,
      "max_size": 1000,
      "hits": 128,
      "misses": 45,
      "hit_rate": "74.0%",
      "total_requests": 173
    },
    "schedule": {
      "size": 15,
      "max_size": 500,
      "hits": 67,
      "misses": 18,
      "hit_rate": "78.8%",
      "total_requests": 85
    }
  },
  "sessions": {
    "active_sessions": 8,
    "max_sessions": 10000,
    "created_count": 45,
    "expired_count": 37
  }
}
```

### Cache Performance Metrics

**Good Performance:**
- Hit rate > 70%
- Active sessions < 100 (for small deployments)
- Evictions = 0 (cache not full)

**Warning Signs:**
- Hit rate < 50% → Increase cache size or TTL
- Active sessions > 5000 → May need to increase max_sessions
- High evictions → Cache too small, increase max_size

### Analytics Logs

View analytics (if enabled):
```bash
tail -f data/analytics.log | jq
```

**Key Metrics to Monitor:**
- `intent` distribution (which features are most used)
- `response_time_ms` (API performance)
- `success: false` (error rate)
- `language` (EN vs ES usage)

---

## 🐛 Troubleshooting

### Issue: Chat doesn't load

**Symptoms:** Blank chat panel, console errors

**Check:**
```bash
# Verify chat_v2.js loaded
curl -I https://your-app.onrender.com/static/chat_v2.js

# Should return 200 OK
```

**Fix:** Clear browser cache, hard refresh (Ctrl+Shift+R)

### Issue: Session IDs not working

**Symptoms:** "Session expired" immediately after starting

**Check:** Ensure `session_manager` is imported correctly
```bash
python -c "from utils.session_manager import session_manager; print(session_manager.stats())"
```

**Fix:** Restart Flask app to reload imports

### Issue: Cache not working

**Symptoms:** Every request is slow (cache miss)

**Check:** Cache stats in `/api/health`

**Fix:**
```bash
# Verify cache module
python -c "from utils.cache import prediction_cache; print(prediction_cache.stats())"
```

### Issue: Delay badges not showing

**Symptoms:** Buses show as delayed but no visual indicator

**Check:** CSS file version
- Ensure `style.css?v=10` is loaded
- Check browser DevTools for `.delay-badge` class

**Fix:** Increment CSS version in HTML or clear CDN cache

### Issue: Language toggle not working

**Symptoms:** Clicking EN/ES does nothing

**Check:** Console for JavaScript errors

**Fix:** Ensure `chat_v2.js` is loaded (not old `chat.js`)

---

## 🔄 Rollback Plan

If V2.0 has critical issues:

### Quick Rollback (Frontend Only)

1. **Revert chat.html**
   ```html
   <!-- Change this line -->
   <script src="/static/chat.js?v=4"></script>
   <!-- Back from -->
   <script src="/static/chat_v2.js?v=1"></script>
   ```

2. **Keep backend improvements** (they're backward compatible)

### Full Rollback

```bash
git revert HEAD
git push origin main
```

---

## 📈 Performance Benchmarks

### Expected Response Times

| Endpoint | First Call (Cold) | Cached Call (Warm) |
|----------|-------------------|-------------------|
| `/api/routes` | 200-500ms | 50-100ms |
| `/api/predictions` | 300-800ms | 20-50ms |
| `/api/agent` | 500-2000ms | 200-500ms |
| `/api/health` | 50-150ms | 20-50ms |

### Cache Hit Rates (Target)

- Predictions: > 70%
- Schedule: > 80%
- Routes: > 90%

---

## 🎓 User Training

### For Customer Support

**New Features to Highlight:**

1. **Bilingual Support**
   - "You can now switch between English and Spanish using the EN/ES button"

2. **Better Error Messages**
   - If stop ID is invalid, system now tells you exactly why

3. **Offline Mode**
   - "If you lose internet, you'll still see your last predictions"

4. **Delay Alerts**
   - "Buses running late now show a yellow 'Delayed' badge"

### For Power Users

**Advanced Features:**

- **Browser Console Commands:**
  ```javascript
  toggleLanguage()  // Switch language
  AppState          // View current state
  ```

- **Keyboard Shortcuts:**
  - `Enter` in chat → Send message
  - `Shift+Enter` → New line

---

## ✅ Post-Deployment Verification

After deployment, verify:

- [ ] `/api/health` returns `"status": "healthy"`
- [ ] Cache hit rate > 0% after a few requests
- [ ] Session count increases when users connect
- [ ] Delay badges appear on delayed buses
- [ ] Language toggle works
- [ ] Wizard flow matches flowchart (ETA-first)
- [ ] Back buttons function correctly
- [ ] Mobile experience is smooth
- [ ] Offline mode triggers when network disconnects

---

## 📞 Support

**Issues or Questions?**
- Check logs: `tail -f data/analytics.log`
- Review cache stats: `curl /api/health`
- GitHub Issues: [link to your repo]

---

## 🎉 Success Criteria

V2.0 is considered successful when:

✅ Cache hit rate > 70%
✅ No critical errors in 24 hours
✅ Session management working (no session_id errors)
✅ User feedback is positive on new wizard flow
✅ Delay indicators appearing correctly
✅ Spanish translations verified by native speaker
✅ Mobile experience tested on iOS/Android

---

**Deployed by:** Claude Code
**Version:** 2.0.0
**Date:** 2026-02-02
**Review ID:** Complete UX & Technical Overhaul
