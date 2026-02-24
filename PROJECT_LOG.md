# RTS Project Log

Purpose: Keep a short history of meaningful project changes (features, fixes, deployments, blockers, decisions).

How to use:
- Update `TASKS.md` and `data/project_tasks.json` for current status changes.
- Add a log entry here for major changes only.
- Keep entries short and dated.

## Entry Template

### YYYY-MM-DD
- Type: `feature` | `fix` | `deploy` | `docs` | `decision` | `blocker`
- Summary:
- Files/Areas:
- Notes / Follow-up:

---

### 2026-02-24
- Type: `feature`, `docs`
- Summary: Added project task tracking system and dashboard page for status visibility.
- Files/Areas: `TASKS.md`, `data/project_tasks.json`, `routes/project_status.py`, `public_html/dashboard.html`, `app.py`
- Notes / Follow-up: Keep `TASKS.md` and `data/project_tasks.json` updated after each completed/pending/blocked status change. Dashboard is currently public at `/dashboard` once deployed.
