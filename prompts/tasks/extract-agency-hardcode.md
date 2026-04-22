# Task: Extract agency-hardcoded content into `agency_config.yaml`

> Read [prompts/context/project-brief.md](../context/project-brief.md) first.

## Goal

Create `agency_config.yaml` at the repo root, populate it with every Gainesville-specific value in the codebase, and rewrite the source to read from it. This is the single highest-leverage refactor for the commercial replicability thesis.

## Scope — files that must change

Only Python source under `routes/`, `utils/`, the repo-root Flask files (`app.py`, `rts_api.py`, `web.py`, `web_index.py`, `webqa.py`, `config.py`, `server.py`), and the `Backend Basics/db/*.py` build scripts. **Do not** touch:
- `public_html/*` (frontend extraction is a separate task)
- `Backend Basics/RTSGTFS_Spring2026_V6/*` (raw GTFS — that's data, not code)
- `tests/*` (update only if a test directly asserts a hardcoded string)

## Step 1 — Inventory

Run [prompts/roles/replicability-reviewer.md](../roles/replicability-reviewer.md) first. Produce the leak report. This is step 1's deliverable.

## Step 2 — Schema

Author `agency_config.yaml` with this starting schema (add keys as the review surfaces them):

```yaml
agency:
  id: "rts-gainesville"                     # slug used in multi-tenant routing
  short_name: "RTS"
  full_name: "Gainesville RTS (Regional Transit System)"
  city: "Gainesville"
  state: "FL"
  timezone: "America/New_York"

contact:
  support_phone: "(352) 334-2600"
  support_hours: "Mon–Fri 8 AM–5 PM"
  website: "https://go-rts.com"
  rider_app_url: "https://riderts.app"

realtime:
  provider: "bustime"                       # one of: bustime | gtfs_rt | swiftly | transloc
  endpoint: "https://riderts.app/bustime/api/v3"
  api_key_env: "BUSTIME_API_KEY"

gtfs:
  static_feed_path: "Backend Basics/RTSGTFS_Spring2026_V6"
  refresh_mode: "on_deploy"                 # later: scheduled

languages:
  default: "en"
  supported: ["en", "es"]

landmarks:
  hubs:
    - { id: "butler-plaza-ts", display: "Butler Plaza Transfer Station" }
    - { id: "rosa-parks-ts",   display: "Rosa Parks RTS Downtown Station" }
    - { id: "reitz-union",     display: "Reitz Union" }
  # add the rest from the leak report

branding:
  primary_color: "#0057B8"                  # placeholder, confirm with visual inspection
  app_name: "RTS Bus Tracker"
```

## Step 3 — Loader

Add `utils/agency_config.py` exposing a single `get_agency_config() -> dict` that:
- Loads the YAML once (cache on module).
- Resolves env-var references (`api_key_env` → `os.getenv(value)`).
- Raises a clear error on missing required keys.
- Has a unit test in `tests/test_agency_config.py` covering load + env resolution.

## Step 4 — Rewrite call sites

For each leak from the review:
- Agent system prompt strings → read from config via a small helper `format_system_prompt(cfg)` that interpolates `{agency_full_name}`, `{support_phone}`, etc.
- Phone numbers in fallback responses → read from `cfg["contact"]["support_phone"]`.
- Timezone constants (`ZoneInfo("America/New_York")`) → read from `cfg["agency"]["timezone"]`.
- `rts_api.py` vendor endpoint → read from `cfg["realtime"]["endpoint"]`; rename file later (separate task).
- Hardcoded hub names → read from `cfg["landmarks"]["hubs"]`.

## Step 5 — Verify

Run:
```bash
pytest
grep -ri "gainesville\|go-rts\.com\|riderts\.app\|\(352\) 334\|butler plaza\|reitz union" \
  -- ':!agency_config.yaml' ':!prompts/' ':!*.md' ':!Backend Basics/RTSGTFS_*'
```
Second command must print **nothing** (or only KEEP-annotated lines).

## Step 6 — Report

Deliver:
1. The leak report from step 1.
2. `agency_config.yaml` contents (final).
3. Diff stats (`git diff --stat`).
4. Test output showing all green.
5. Suggested commit message:
   ```
   refactor: extract Gainesville-specific content into agency_config.yaml

   Replaces hardcoded agency name, phone, URLs, hubs, and timezone with
   values loaded from agency_config.yaml. Unblocks white-label deploys to
   other transit agencies.

   Co-Authored-By: <Model Name> <noreply@...>
   ```

## Do NOT

- Do not change agent behavior. This refactor is **behavior-preserving**.
- Do not introduce new features.
- Do not touch `public_html/`.
- Do not add a second agency yet — that's a follow-up task.
