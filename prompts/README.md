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

## Quick-start: delegating to Codex

1. Open Codex on this repo.
2. Paste `codex-kickoff.md` as the first message. Wait for "Ready".
3. Send your one-line task. Codex will already know the project rules, the URL map, the agent tools, and the open punch list — so the task itself can stay tiny. Saves Opus tokens for strategy work.

Stale `STATE-OF-PLAY.md` = wasted Codex tokens re-deriving what's already known. Update it at the end of each session that ships work.

## Directory

```
prompts/
  codex-kickoff.md          — paste-ready preamble for delegating to Codex / Sonnet / etc.
  context/
    project-brief.md        — MANDATORY preamble: commercial thesis + engineering rules
    STATE-OF-PLAY.md        — refreshed each session: what shipped, what's open, current tools/URLs
  roles/
    replicability-reviewer.md — reviews PRs for hardcoded agency leaks
  tasks/
    add-pwa-primitives.md
    add-web-push.md
    extract-agency-hardcode.md
```

## Authoring rules

- Keep prompts **self-contained** — a delegated AI has no prior context.
- Always specify **file-path scope** so AIs don't drift into unrelated code.
- Always specify **success criteria** (tests pass, grep returns empty, etc).
- Always specify **commit message format**: concise subject + `Co-Authored-By: <Model Name> <noreply@...>` footer.
- Never paste the commercial goal verbatim — reference `context/project-brief.md` so the single source of truth stays in one file.

## When to add a new prompt

Create a new `tasks/*.md` whenever you've delegated the same type of work twice. The third time it should be a template.
