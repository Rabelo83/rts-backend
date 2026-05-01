# Codex kickoff prompt

Paste this verbatim into Codex (or any delegated AI with repo access) at the **start** of every new session. It loads the strategic context cheaply so subsequent task instructions can be one-liners.

---

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

---

## How to use

1. Open Codex (or the delegated AI) on this repo.
2. Paste the prompt above as the first message. Wait for "Ready".
3. Then send your **one-line task**. Examples:
   - "Fix the pathological arrive-by itinerary bug listed in STATE-OF-PLAY.md > Trip Planner trust bugs."
   - "Add greyed-out chips on the Live Map for routes with no service today."
   - "Investigate the Route 8 tool inconsistency in STATE-OF-PLAY.md > Chat agent regressions."

Because the AI already has the context loaded, your task instruction can stay one sentence — saving Opus tokens for strategy.

## Maintaining the context files

When this session ends, update `prompts/context/STATE-OF-PLAY.md` with:
- Any new commits shipped
- Any new tools added to the agent
- Any new open items on the punch list
- Any items the latest session crossed off

Stale STATE-OF-PLAY = wasted Codex tokens re-deriving what's already known.
