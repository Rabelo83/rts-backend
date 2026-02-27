# Transit AI Chatbot Research Notes
*Compiled: 2026-02-27 | Sources: academic papers (arXiv 2024–2025), agency case studies, industry reports*

---

## Key Validation: Our Architecture Is Right

The NYC MyCity chatbot ($500k) was shut down Jan 2026 after giving illegal advice.
Root cause: LLM answering factual questions from memory. Our approach — LLM only for
intent extraction, deterministic GTFS + Bustime API for answers — is exactly what
TransitGPT (2024) and the San Antonio transit LLM studies (2024–2025) recommend.

**LLM accuracy on raw transit queries (no code generation):**
- GPT-3.5-turbo: 59.7% on GTFS multiple-choice
- GPT-4o: 90% on simple queries, 64% on complex multi-file joins
- TransitGPT (code-gen + few-shot): 93% — our model

**CTA deployed a full chatbot in 2024 with Google/Dialogflow: 72% of conversations
still needed a human agent.** Even well-funded chatbots handle only the simplest
queries autonomously.

---

## Failure Modes Ranked by Relevance to RTS

### 1. Stop Name / Landmark Resolution (HIGH risk)
- Users say "near Walmart on Archer" or "the stop by Publix on University Ave" —
  not GTFS stop names or stop IDs
- `stop_areas.json` covers zones (UF, Downtown, etc.) — NOT arbitrary landmarks
- `_gtfs_resolve_stop_name()` handles GTFS-named stops but not street intersections
- **Gap**: No landmark-to-stop fuzzy resolution for unknown place names

### 2. Real-Time vs Static Schedule Confusion (HIGH risk)
- Users trained by Google Maps, Transit App, Moovit expect EVERY answer to be real-time
- When we return a GTFS schedule time, users interpret it as an ETA
- Bus doesn't come at that time → trust collapse
- **Gap**: Response text doesn't consistently distinguish "Live tracker: 8 min" vs
  "Schedule shows: 3:15 PM"

### 3. Spanish + English Code-Switching (HIGH risk)
- Gainesville Spanish speakers mix: "¿Cuándo viene el bus 43 al downtown?"
- Current lang detection handles clean Spanish/English but not mixed
- Not tested with real Gainesville Spanish-speaker queries
- **Gap**: No real-world multilingual test coverage

### 4. No Escalation Exit After Repeated Failures (MEDIUM risk)
- Every deployed transit chatbot study cites 2–3 failed disambiguations as
  the abandonment threshold
- After 3 failures, users need a human channel
- **Gap**: No "Call RTS at (352) 334-2600" fallback when bot can't resolve query

### 5. Holiday / Exception Service Calendar Edge Cases (MEDIUM risk)
- Users ask "what time does Route 43 run tomorrow?" on a holiday assuming weekday svc
- GTFS calendar_dates exceptions exist but edge cases not stress-tested
- **Gap**: No explicit holiday warning in schedule responses

### 6. Confident Wrong Answer on Uncertain Fuzzy Match (MEDIUM risk)
- When `_gtfs_resolve_stop_name()` matches partially, we respond with full confidence
- Users follow wrong stop directions
- **Gap**: No uncertainty signal ("I think you mean Stop 0473 (Reitz Union) — is that right?")

### 7. Conversation State Drift After Topic Change (MEDIUM risk)
- User asks about Route 43, then asks about Route 15, then "after that?" —
  "after that" might pull Route 43 context from earlier
- Greeting detection and `_is_followup_after()` help but don't cover all drift cases
- **Gap**: Complex topic-change mid-session not fully tested

---

## Best Practices to Implement (Priority Order)

### P1 — High Impact, Relatively Low Effort

**A. Label data source in every response**
- Real-time: "Live tracker shows Route 43 arriving in 8 min"
- Schedule: "Scheduled departure at 3:15 PM (static schedule — check tracker for live updates)"
- Implementation: use `sources` metadata already in every `_with_meta()` response;
  add a prefix/suffix based on `source_type`

**B. Add human escalation after 2–3 failures**
- Track consecutive "need_stop_or_route" / "clarify_route_vs_stop" sources in session
- After 2–3: append "Still stuck? Call RTS Customer Service: (352) 334-2600 or
  visit go-rts.com"
- Implementation: session manager tracks failure_count; agent_service checks it

**C. Uncertainty signal on fuzzy stop matches**
- When a stop resolves via fuzzy match (not exact or stop_id), confirm before using:
  "I think you mean Stop 0473 – Reitz Union. Is that right? [Yes] [Different stop]"
- Implementation: add `match_type: "fuzzy"` to `_gtfs_resolve_stop_name()` result;
  check it in agent_service.py

### P2 — Medium Impact, Moderate Effort

**D. Landmark-to-stop resolution**
- Add Google Places / OSM geocoding call to find the nearest GTFS stop to a
  described landmark ("near Walmart on Archer Road")
- Fallback: expand `stop_areas.json` with known Gainesville landmarks mapped to
  specific stop IDs
- Implementation: new `routes/landmark_resolver.py` module

**E. Holiday warning in schedule responses**
- Query `calendar_dates.txt` for the requested date; if exception applies, prepend
  "Note: Holiday service may differ — verify at go-rts.com/alerts"
- Build_exception_note() in response_builder.py already exists — wire it up

**F. Brevity enforcement in HUMANIZE prompt**
- Research strongly recommends ≤3 sentences for mobile/elderly users
- Add explicit constraint to humanize system prompt:
  "Keep responses under 3 sentences. Be direct. Do not explain reasoning."

### P3 — Lower Priority / Longer Term

**G. Escalation counter in session manager**
- Count consecutive unresolved requests per session
- Wire into `handle_agent_message()` fallback response

**H. Spanish code-switching test suite**
- Collect 20–30 real mixed Spanish/English transit queries
- Add to test suite as regression tests

**I. GTFS auto-refresh endpoint**
- Token-protected /api/admin/rebuild-gtfs for semester schedule changes
- Without this, the GTFS DB goes stale at semester transitions

---

## Key Sources

- [TransitGPT (arxiv 2412.06831)](https://arxiv.org/html/2412.06831v1)
- [San Antonio LLM Transit Studies (arxiv 2407.11003, 2501.03904)](https://arxiv.org/html/2407.11003v1)
- [ChatGPT for GTFS Benchmark (arxiv 2308.02618)](https://arxiv.org/abs/2308.02618)
- [CTA Chatbot Case Study (Google/Mass Transit)](https://publicsector.google/ai/chicago-transit-authority-launches-a-multi-lingual-chatbot-for-more-a-more-seamless-commute/)
- [NYC MyCity Chatbot Failure (StateScoop)](https://statescoop.com/mamdani-kill-nyc-ai-chatbot/)
- [Italian Transit Agent Chatbot (arxiv 2505.22698)](https://arxiv.org/html/2505.22698)
- [Chatbot Abandonment Analysis (WorkBot)](https://workhub.ai/chatbots-fail-in-customer-service/)
