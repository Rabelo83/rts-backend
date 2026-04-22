# Prompts

Reusable prompts to delegate tactical work on this repo to **any AI** (ChatGPT, Gemini, Claude Sonnet/Haiku, a Claude Code subagent, etc.) while keeping Opus-grade reasoning focused on strategy.

## How to use

1. Pick the right file for the work:
   - **Strategy / architecture / product decisions** → do not delegate. Talk to Opus.
   - **Concrete refactor / feature / review** → find a file in `tasks/`.
   - **Recurring persona** (reviewer, backend engineer, etc.) → use `roles/`.
2. Always include `context/project-brief.md` as the **first** message to the delegated AI. It carries the commercial thesis, conventions, and guardrails that every task depends on.
3. Then paste the role and/or task file.
4. Attach any specific files the task references (or let the AI read them if it has repo access).

## Directory

```
prompts/
  context/
    project-brief.md        — MANDATORY preamble for every delegated AI
  roles/
    replicability-reviewer.md — reviews PRs for hardcoded agency leaks
    backend-engineer.md     — Python/agent/data work
    frontend-engineer.md    — public_html/ work
  tasks/
    extract-agency-hardcode.md  — first replicability task
    add-realtime-adapter.md     — add new transit-authority adapter
    add-agent-tool.md           — template for new agent tool + tests
```

## Authoring rules

- Keep prompts **self-contained** — a delegated AI has no prior context.
- Always specify **file-path scope** so AIs don't drift into unrelated code.
- Always specify **success criteria** (tests pass, grep returns empty, etc).
- Always specify **commit message format**: concise subject + `Co-Authored-By: <Model Name> <noreply@...>` footer.
- Never paste the commercial goal verbatim — reference `context/project-brief.md` so the single source of truth stays in one file.

## When to add a new prompt

Create a new `tasks/*.md` whenever you've delegated the same type of work twice. The third time it should be a template.
