# Delegating to Codex (and other AIs) — full process

This file documents:
1. **The kickoff prompt** to paste verbatim at the start of every Codex session.
2. **The full delegation process** — when to delegate, how, and what to do after.
3. **Concrete examples** of one-line tasks you can send after the kickoff.
4. **How to keep the system maintained** so it stays cheap.

The goal: spend Opus tokens on strategy and architecture; spend Codex / Sonnet / Haiku tokens on tactical execution. The kickoff prompt is what makes that delegation cheap, because the delegated AI already has the project context loaded before you give it the actual task.

---

## 1. The kickoff prompt — paste this first

Every new Codex session starts the same way. Paste the block below as the **first message**. Wait for "Ready" before sending the task.

```
You are working on rts-backend, a Flask + Python AI-first transit assistant. Read these three files in order before doing anything else:

1. prompts/context/project-brief.md         — the commercial thesis and engineering rules
2. prompts/context/STATE-OF-PLAY.md         — what shipped recently, current tools/URLs, open punch list
3. CLAUDE.md                                — tech stack + URL map + env vars

Then confirm you understand the following non-negotiables before I give you a task:

- White-label is the commercial thesis. Never hardcode "Gainesville", "RTS", "go-rts.com", route_ids, hub names, or brand colors. Route through agency_config.yaml.
- Default to free / self-hostable dependencies. Geocoding = Nominatim (already wired), map tiles = OpenFreeMap. Never propose Google paid APIs as default.
- Do not grow the agent system prompt for tool-selection bugs — fix the routing table or tool description in routes/agent_tools.py instead.
- Tests: cd tests/ && python run_tests.py before claiming a task done.
- Local dev: --port 5050 (port 5000 is hijacked by macOS AirPlay).
- Commit message format: concise subject + body + "Co-Authored-By: <Your Model Name> <noreply@anthropic.com>" footer.

When you finish a task, report:
1. What changed (files + line ranges)
2. Why (link to a rule in project-brief.md or a punch-list item in STATE-OF-PLAY.md)
3. Verification — tests run, smoke-test results, greps confirming cleanliness
4. Suggested commit message
5. What's next — any follow-ups your work surfaced

Reply with "Ready" once you've read all three context files and internalized the rules. Do not start implementation until I give you the task.
```

That's it. Paste, wait for "Ready", then send the task.

---

## 2. The full delegation process

### When to delegate (vs. talking to Opus)

| Type of work | Where it goes |
|---|---|
| Strategy, architecture, product calls, "should we build X" | **Opus** (this Claude Code session) |
| Tradeoff analysis, IA decisions, what-stack-to-use | **Opus** |
| Concrete bug fix where the diagnosis is already known | **Codex** |
| Mechanical refactor (rename, extract function, etc.) | **Codex** |
| Adding a new agent tool from a known template | **Codex** |
| Polish work (CSS, copy, accessibility) | **Codex** |
| PR / branch review against `replicability-reviewer.md` | **Codex with the role file** |

Rule of thumb: **if the answer requires you to weigh competing options, talk to Opus. If the answer is "just do this thing," talk to Codex.**

### The five-step delegation flow

1. **Identify the task** in `STATE-OF-PLAY.md > Open work` or in TASKS.md. If the task isn't there yet, we should add it first (during an Opus session) so Codex has a single source of truth.
2. **Open Codex** on the repo (give it filesystem access).
3. **Paste the kickoff prompt** (section 1 above). Wait for "Ready".
4. **Send the one-line task** referencing the punch-list item by name.
5. **Review Codex's output**, then either:
   - Commit and push if it looks clean.
   - Bounce it back with a correction (Codex tokens are cheap, iteration is fine).
   - Bring the question to Opus if Codex hits something architectural.

### After Codex commits

When Codex finishes, you (or the next Opus session) should:
- **Verify the commit**: `git log -1`, `git diff HEAD~1`, run tests.
- **Update STATE-OF-PLAY.md**: cross off the punch-list item; add any follow-ups Codex surfaced.
- **Update PROJECT_LOG.md**: add a dated entry for the change.

Stale `STATE-OF-PLAY.md` = wasted future Codex tokens re-deriving what's already known. Keep it fresh.

---

## 3. Concrete one-line task examples

After the kickoff "Ready", send tasks like these. Each references a specific item in `STATE-OF-PLAY.md` so Codex can find the full context.

**Bug fixes:**
- "Fix the pathological arrive-by itinerary bug listed in STATE-OF-PLAY.md > Trip Planner trust bugs. Bound earliest dep to `arrive_by - 2h` and reject travel time > 2× shortest viable."
- "Investigate the Route 8 tool inconsistency in STATE-OF-PLAY.md > Chat agent regressions. Determine which tool is wrong: get_vehicle_location says 1 active bus, get_route_vehicle_count says no scheduled trips today."

**Polish / features:**
- "Implement greyed-out chips on the Live Map for routes with no service today. Use `engine.service_ids_for_date()` to determine which routes have active service."
- "Add per-direction polyline coloring on the Live Map (inbound desaturated). See STATE-OF-PLAY.md > Live Map polish."

**Refactor:**
- "Extract the BusTime vehicle aggregator from `routes/map_api.py:_fetch_all_vehicles` into a shared utility module. Both the map endpoint and the chat agent's `get_active_vehicles_systemwide` tool currently call it."

**Review:**
- "Review the most recent commit against the rules in `prompts/roles/replicability-reviewer.md`. Report any hardcoded agency content."

**Testing:**
- "Add three test scenarios to `tests/scenarios_v2.json` covering the new `get_active_vehicles_systemwide` tool: empty system, single bus, multiple routes."

Notice the pattern: **task name + specific approach hint + reference to where the context lives**. Codex doesn't need backstory because the kickoff already loaded it.

---

## 4. Maintenance — keep the system cheap

The whole system breaks down if `STATE-OF-PLAY.md` goes stale. After every Opus session that ships work:

1. **Append to `STATE-OF-PLAY.md > What shipped today`** — list each new commit with a one-line "why".
2. **Update the agent tool list** if tools were added/renamed.
3. **Cross off** any open-work items the session completed.
4. **Add new** open-work items the session surfaced.
5. **Update the URL map** if routing changed.

If `STATE-OF-PLAY.md` is more than ~5 sessions out of date, the next Codex session will burn tokens re-deriving things. That's the moment to invest 5 minutes refreshing it.

---

## 5. Why this works

A normal Codex session burns ~30% of its context window on figuring out the project before doing anything useful. With this kickoff:

- 3 files (~5k tokens) load the rules, state, and tech stack.
- Codex confirms understanding.
- Your one-line task fits in <100 tokens.

Net: Codex spends almost all its budget on the actual work, not orientation. And because the rules are explicit, Codex doesn't spend any tokens *guessing* — it knows white-label, no-Google-paid, no-prompt-growth, etc. up front.

The Opus side benefits too: you're not re-explaining the project to me each session, because I read `STATE-OF-PLAY.md` and `CLAUDE.md` automatically.

---

## 6. Other delegated AIs (Sonnet, Haiku, Gemini, ChatGPT)

The kickoff prompt works verbatim for any AI that has filesystem access to this repo. Just substitute the `Co-Authored-By: <Your Model Name>` line with the actual model name (Sonnet 4.6, Haiku 4.5, GPT-5, etc.).

For AIs **without** filesystem access (e.g. ChatGPT web UI without code interpreter), paste the contents of `project-brief.md`, `STATE-OF-PLAY.md`, and any specific files the task touches as additional messages before sending the task.
