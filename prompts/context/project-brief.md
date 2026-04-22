# Project brief — read this first

You are working on **rts-backend**, a Python Flask backend powering an AI-first conversational transit assistant. This file is the preamble you must internalize before any task.

## What it is

A bilingual (English/Spanish) chat interface that answers Gainesville RTS (Regional Transit System) rider questions using:
- A Claude tool-use agent ([routes/agent_claude.py](../../routes/agent_claude.py)) with ~10 tools ([routes/agent_tools.py](../../routes/agent_tools.py))
- A SQLite GTFS database rebuilt on deploy ([Backend Basics/db/build_gtfs_db.py](../../Backend Basics/db/build_gtfs_db.py))
- A BusTime / Clever Devices real-time vendor API ([rts_api.py](../../rts_api.py))
- An in-memory RAPTOR trip planner ([utils/](../../utils/))
- Vanilla JS frontend in [public_html/](../../public_html/)
- Deployed on Render via [render.yaml](../../render.yaml)

## The commercial thesis (do not violate)

The product goal is **not** a Gainesville-only app. It is a **white-label AI transit assistant** that:
1. Outperforms existing RTS apps (Go RTS, RideRTS) in Gainesville via conversational UX + proactive alerts + multilingual + zero-friction modes (SMS/geofence/voice).
2. **Drops into any mid-sized transit agency** with a GTFS feed and a real-time vendor, deployable in a day.

The sellable asset to agencies is the **admin dashboard** (query analytics, confusion clusters, ridership-demand signal), even though the chat is what riders see.

## Non-negotiable engineering rules

1. **No hardcoded agency content.** Anything Gainesville-specific (agency name, phone number, URL `go-rts.com`, hub names like "Butler Plaza" / "Reitz Union", `route_id` values, landmarks, brand colors) is a **replicability blocker**. Route it through `agency_config.yaml` (being built) — never inline it.
2. **Real-time vendors are abstracted.** Different agencies use BusTime (RTS, Clever Devices), GTFS-RT, Swiftly (Miami), TransLoc (Jacksonville). Agent tools call a `RealtimeProvider` interface — never `rts_api` directly.
3. **Agent system prompt is templated.** Reference agency via `{agency_name}`, `{support_phone}`, `{hubs}` — never literal "Gainesville RTS".
4. **Do not grow the system prompt for each new case.** The prompt has caused regression bugs historically. Prefer code-level logic in `agent_tools.py` over prompt additions.
5. **Keep tests passing.** Run `pytest` before claiming a task done. Tests live in [tests/](../../tests/).

## Style and process

- Commit messages: concise subject line + Co-Authored-By footer. See [CLAUDE.md](../../CLAUDE.md).
- Language/runtime: Python 3.11, Flask, SQLite, vanilla JS.
- Minimal comments — well-named identifiers over prose.
- When in doubt, ask. Do not invent scope.

## How you report back

When you finish, report:
- **What changed** (files + lines).
- **Why** (link to the thesis rule above — e.g. "unblocks replicability rule #1").
- **Verification** — tests that ran, greps that confirm cleanliness.
- **Suggested commit message.**
