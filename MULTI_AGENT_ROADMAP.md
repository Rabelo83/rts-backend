# RTS Backend: Multi-Agent Implementation Roadmap

This plan focuses on coordinating multiple independent AI agents to simultaneously enhance the RTS transit assistant. By isolating agents into specific layers and domains of the codebase, we eliminate merge conflicts and double iteration speed.

## 🤖 Agent Roles & Isolation Boundaries

To successfully orchestrate multiple AI agents simultaneously, they must work in strictly isolated domains:
*   **Agent Alpha (UI/UX Specialist)**: Strictly edits frontend files, UI/UX logic, and client-facing API consumption.
*   **Agent Bravo (Backend/Data Engineer)**: Strictly edits Python backend files, database schemas, and system environment APIs.

---

## 🚀 Execution Roadmap

### Stage 1: Immediate Enhancements (Parallel Execution)
*These tasks touch entirely different parts of the system and can be executed by multiple agents at the precise same time.*

#### Agent Alpha Track: PWA Conversion (Frontend)
*   **Goal**: Allow users to install the web app natively.
*   **Action Items**:
    1.  Create `manifest.json` and generate necessary icons.
    2.  Write a robust `service-worker.js` for caching the app shell (offline mode).
    3.  Wire service worker registration into `public_html/frontend.js`.
*   **Boundary**: Only touches `/public_html` directory.

#### Agent Bravo Track: Database Scaling Migration (Backend)
*   **Goal**: Migrate away from Render SQLite persistent disks to a scalable SQL architecture (PostgreSQL/Turso).
*   **Action Items**:
    1.  Install and configure SQLAlchemy/ORM layer for `analytics`, `sessions`, and `qa_history`.
    2.  Refactor specific Python endpoints (e.g., `utils/session_manager.py`, `routes/admin_api.py`) to drop direct SQLite connections.
    3.  Create local development environment parity mechanisms.
*   **Boundary**: Only touches Python files and `/db` dependencies. Never touches `public_html` or `gtfs_engine`.

---

### Stage 2: Heavy Refactoring & Map Integration (Parallel Execution)
*Once Stage 1 is verified, both agents step into a heavier feature rollout simultaneously.*

#### Agent Alpha Track: Transition to Modern Frontend Strategy
*   **Goal**: Refactor `public_html/chat_v2.js` out of a singular massive codebase and into maintainable components using Vite and Svelte/React.
*   **Action Items**:
    1.  Restructure frontend into a Node-based architecture (e.g., `frontend/src/components/`).
    2.  Setup Vite bundler and port the PWA plugin (`vite-plugin-pwa`) over.
    3.  Port existing vanilla JS chat logic into component state.

#### Agent Bravo Track: Live Map Data Streaming Prep
*   **Goal**: Prepare the backend for the incoming Live Map integration without slowing down Agent Alpha.
*   **Action Items**:
    1.  Optimize the `/api/vehicles` endpoint for high-frequency polling.
    2.  Refactor `utils/gtfs_engine.py` (if needed) to expose route geometries explicitly for Leaflet mapping.
    3.  *Optional*: Set up a Flask-SocketIO or SSE stream for real-time bus locations to prevent HTTP polling overhead.

---

### Stage 3: Feature Integration (Coordination)
*Agents must sync up to marry the final UI feature.*

#### Agent Alpha & Agent Bravo Sync: Interactive Live Map
*   **Goal**: Finalize the interactive map tab showing real-time pulses.
*   **Coordination**:
    1.  **Agent Bravo** deploys the finished backend data streaming.
    2.  **Agent Alpha** integrates Leaflet.js within the new Vite frontend build, drawing bus data strictly from Bravo's deployed changes.

---

### Stage 4: Automating Operations (Sequential)
*As requested, GTFS Automation is the final touch.*

#### Agent Bravo Track: Automated GTFS Updates
*   **Goal**: Replace manual GTFS `.zip` uploads with an autonomous cron-based pulling script.
*   **Action Items**:
    1.  Build a fetch script (`scripts/update_gtfs.py`) that accesses the live transit authority GTFS feed.
    2.  Use APScheduler within Flask or a separate worker script to run the job weekly.
    3.  Automatically invoke the `get_engine()._load()` command in `app.py` after a successful data ingestion to reload RAPTOR routing without a server reboot.
