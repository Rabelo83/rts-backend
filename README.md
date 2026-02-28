# RTS Backend

This repository hosts the Gainesville RTS realtime + schedule backend and the accompanying web UI with AI-powered chat assistant.

## Live Sites

| Page | URL |
|---|---|
| Main tracker | https://rts-backend-7ru5.onrender.com/ |
| Chat assistant (v1) | https://rts-backend-7ru5.onrender.com/chat |
| **Chat assistant (v2 tool-use agent)** | https://rts-backend-7ru5.onrender.com/chat?agent=v2 |
| Trip wizard | https://rts-backend-7ru5.onrender.com/wizard |
| Project dashboard | https://rts-backend-7ru5.onrender.com/dashboard |

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check + cache stats |
| `/api/agent` | POST | Chat agent v1 (rule-based) |
| `/api/agent/stream` | POST | Chat agent v1 — SSE streaming |
| `/api/agent/v2` | POST | Tool-use agent v2 (GPT-4o-mini) |
| `/api/agent/v2/stream` | POST | Tool-use agent v2 — SSE streaming |
| `/api/predictions` | GET | Real-time bus arrivals (`?stop_id=`) |
| `/api/routes` | GET | All routes |

> **Testing the v2 agent:** open `https://rts-backend-7ru5.onrender.com/chat?agent=v2`
> — the `?agent=v2` param switches the chat UI to the new tool-use agent with no code changes needed.
