# GPT User Simulator — Generate RTS Test Scenarios

You are going to act as a **Gainesville, FL bus rider** using the RTS (Regional Transit System) AI chat assistant. Your job is to generate as many **realistic, diverse questions** as possible that a real rider might type — including tricky, edge-case, and broken English/Spanish queries.

---

## Context: What the assistant can and cannot do

The assistant has exactly **5 tools**:

| Tool | What it answers |
|---|---|
| `search_stops` | "Where is stop X?" / Resolve a landmark or place name to a stop ID |
| `get_realtime_predictions` | "When is the next bus at stop X?" (live ETAs, in minutes) |
| `get_schedule` | "When does route Y leave stop Z?" (GTFS scheduled times) |
| `search_routes` | "What routes go to [place]?" |
| `get_route_overview` | "What is the route X schedule today/tomorrow?" (first/last, frequency) |

**The assistant CANNOT do**: trip planning (A→B routing), accessibility info, route coincidence analysis, fare questions, or comparing multiple routes simultaneously.

**Routes in Gainesville RTS** include (but are not limited to): 1, 2, 5, 8, 9, 10, 11, 12, 13, 15, 16, 20, 25, 35, 38, 43, 75, 76.

**Known stops/landmarks**: Rosa Parks Transit Center, Reitz Union (UF), Santa Fe College, Butler Plaza, Downtown (Rosa Parks area), Oaks Mall, Walmart on Archer Road, Airport, Haile Plantation, Millhopper, Tower Road.

---

## Your task

Generate a JSON array of **30 scenario objects** that a real Gainesville bus rider might ask. Cover ALL of the following categories:

| Category | How many | Notes |
|---|---|---|
| `realtime_eta` | 5 | Real-time arrivals at a specific stop or landmark |
| `schedule_next` | 4 | Scheduled next departures for a route from a stop |
| `schedule_first_last` | 3 | First or last bus of the day |
| `route_overview` | 2 | Full day schedule for a route |
| `route_discovery` | 4 | What routes serve an area/destination |
| `schedule_explicit_time` | 3 | "Next bus after X:XX AM/PM" — must use explicit time |
| `spanish` | 4 | Spanish or mixed English/Spanish queries |
| `out_of_scope` | 3 | Questions the tools CANNOT answer (trip planning, coincidence, fares, accessibility) |
| `greeting` | 1 | Simple greeting |
| `ambiguous_stop` | 1 | A place name that could match multiple stops |

### Style requirements — make them sound like REAL rider messages:
- Use casual language, abbreviations, typos, or incomplete sentences
- Mix formal and informal phrasing
- Include variations like: "tmrw", "2morrow", "wht time", "buses", "next one"
- Spanish queries may mix English and Spanish (code-switching): "¿qué route va a UF?"
- Some queries should be vague and require the agent to ask a clarifying question
- Vary the time references: "morning", "after work", "tonight", "around noon", "after 3pm", "before 8am"

### Out-of-scope categories to use for `out_of_scope` scenarios:
- Trip planning: "How do I get from X to Y?"
- Route coincidence: "Where do routes X and Y cross?"
- Fares: "How much does the bus cost?"
- Accessibility: "Is route X wheelchair accessible?"

---

## Output format

Return ONLY a valid JSON array. Each object must follow this schema exactly:

```json
[
  {
    "id": "GPT01",
    "type": "single",
    "category": "realtime_eta",
    "description": "One sentence describing what this scenario tests",
    "query": "Exact message the user would type",
    "expected_behavior": "What the correct assistant response should look like or do",
    "pass_signals": ["word1", "word2"],
    "fail_signals": ["bad phrase 1", "bad phrase 2"]
  }
]
```

### Rules for pass_signals and fail_signals:
- `pass_signals`: 2–5 short strings that SHOULD appear in a correct response (e.g. `"AM"`, `"PM"`, `"route"`, `"min"`, `"ruta"`)
- `fail_signals`: 1–3 strings that SHOULD NOT appear (e.g. `"I don't know"`, `"customer service"`, `"The following routes are"` for Spanish queries)
- Keep strings short (1–4 words) so substring matching works reliably
- For `out_of_scope`: pass_signal should include `"don't have the ability"` or `"not able to"`; fail_signal should include `"take route"` or `"transfer"` or `"call RTS"`
- For `spanish`: fail_signals must include at least one English phrase that would appear if the agent wrongly responded in English

---

## Multi-turn scenarios (bonus)

After the 30 single-turn scenarios, add **5 multi-turn chains** (type = "multi").
Each has a `turns` array with 2–3 messages simulating a real conversation:

```json
{
  "id": "GPTM01",
  "type": "multi",
  "category": "followup_after",
  "description": "...",
  "turns": ["first message", "follow-up message"],
  "expected_behavior": "...",
  "pass_signals_per_turn": [["signal1"], ["signal2"]],
  "fail_signals_per_turn": [["bad1"], ["bad2"]]
}
```

Multi-turn chain types to include:
- `followup_after`: "and after that?" / "the next one?" / "what comes next?"
- `followup_location`: "what about from [different place]?"
- `spanish_continuation`: Spanish first message, Spanish follow-up
- `stop_disambiguation`: vague stop → agent returns candidates → user picks one
- `context_bleed`: ask about route A, then ask about a new unrelated place (route A should NOT bleed into turn 2)

---

## Important

- Do NOT generate scenarios that require data you cannot know (e.g. specific real-time ETAs)
- Do NOT include scenarios for features that don't exist (e.g. trip booking, account management)
- DO include creative, real-world phrasing that a rider in Gainesville would actually type
- Assign IDs sequentially: GPT01, GPT02, ..., GPT30, GPTM01, ..., GPTM05
- Return ONLY the JSON array — no explanation, no markdown outside the JSON block

Begin:
