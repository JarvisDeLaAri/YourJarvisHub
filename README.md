# 🦞 YourJarvisHub

**Central notification hub** — Any service or cron job can send notifications here. Jarvis receives them, reacts, and forwards to Ariel on WhatsApp.

---

## How It Works

```
Any service/cron/script
        │
        │  POST /notify
        │  { source, title, message, priority }
        ▼
┌──────────────────┐
│  📬 Hub Server   │  Receives notification, assigns ID
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  💾 SQLite       │  Stores notification (status: "pending")
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  🧠 OpenClaw API │  Sends to Jarvis via /v1/chat/completions
│                  │  Jarvis reads it, adds commentary,
│                  │  forwards to Ariel on WhatsApp
└────────┬─────────┘
         │
         │  POST /done/{id}
         ▼
┌──────────────────┐
│  ✅ Mark Done    │  Jarvis confirms he handled it
└──────────────────┘


Example flow:

  Daily AI cron ──POST──▶ Hub ──▶ Jarvis ──▶ WhatsApp 📱
  Voice chat alert ──POST──▶ Hub ──▶ Jarvis ──▶ WhatsApp 📱
  Server health check ──POST──▶ Hub ──▶ Jarvis ──▶ WhatsApp 📱
```

---

## 📊 Flow Comparison: Hub vs Heartbeat/Cron

Want to understand how the Hub's API path compares to OpenClaw's built-in Heartbeat/Cron path?

👉 **[Open the interactive flow chart](https://htmlpreview.github.io/?https://github.com/JarvisDeLaAri/YourJarvisHub/blob/main/flow-comparison.html)** — side-by-side visual comparison of both paths, same brain, different plumbing.

---

## The Code Explained

One file: **`server.py`** (~250 lines)

### 1. Database Layer (SQLite)

```
notifications table:
  id | timestamp | source | title | message | priority | status | response | responded_at
```

- `source` — Who sent it (e.g., "ai-digest", "voice-chat", "healthcheck")
- `priority` — `urgent`, `high`, `normal`, or `low` (affects emoji in message)
- `status` — `pending` until Jarvis handles it, then `done`
- `response` — What Jarvis said/did about it (optional)

### 2. Notification Flow

When a POST hits `/notify`:

1. **Validate** — Must have a `message` field at minimum
2. **Store** — Insert into SQLite with timestamp, source, priority
3. **Wake Jarvis** — Call OpenClaw's chat completions API with the notification formatted as a prompt
4. **Jarvis acts** — The prompt instructs Jarvis to forward to WhatsApp and then call `/done/{id}` to mark it handled

### 3. OpenClaw Integration

The hub talks to Jarvis through OpenClaw's standard chat API:

```
POST http://localhost:<OPENCLAW_PORT>/v1/chat/completions
Authorization: Bearer <gateway-token>
```

**Critical Fix (2026-03-22):** The hub injects notifications into the **existing main session** instead of creating new sessions:

```
x-openclaw-session-key: agent:main:main
```

Without this header, OpenClaw creates a NEW session per request (like `agent:main:openai:UUID`), leading to session spam. By targeting the existing session, notifications flow directly into Jarvis's active conversation.

The notification is formatted with:
- Priority emoji (🚨 urgent, ❗ high, 📬 normal, 📝 low)
- Source and title
- Full message
- Instructions for Jarvis to forward to WhatsApp and mark done

### 4. HTTP Server

Plain Python `http.server` — no dependencies, no frameworks. Intentionally minimal.

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/` | GET | Status page — pending count + recent notifications |
| `/notify` | POST | Submit a notification `{source, title, message, priority}` |
| `/done/{id}` | POST | Mark notification as handled `{response}` |
| `/pending` | GET | List all pending (unhandled) notifications |
| `/history` | GET | Last 50 notifications |

### 5. Sending a Notification

From any script, cron, or service:

```bash
curl -X POST http://localhost:<HUB_PORT>/notify \
  -H "Content-Type: application/json" \
  -d '{
    "source": "my-script",
    "title": "Something happened",
    "message": "Details about what happened",
    "priority": "normal"
  }'
```

Priority options:
- `urgent` 🚨 — Immediate attention needed
- `high` ❗ — Important but not critical
- `normal` 📬 — Standard notification (default)
- `low` 📝 — FYI, no action needed

---

## Setup

### Requirements
- Python 3.10+ (no pip dependencies — stdlib only!)

### Run

```bash
python server.py
```

The hub listens on localhost only (not exposed to the internet). It's an internal service — other apps on the same machine POST to it.

### Systemd Service

```ini
[Unit]
Description=Jarvis Hub
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/hub
ExecStart=/usr/bin/python3 server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Environment

Requires OpenClaw running on the same machine with:
- Gateway API accessible locally
- Valid gateway token configured in `server.py`

---

## File Structure

```
hub/
├── server.py      # Everything — server, DB, OpenClaw integration
├── hub.db         # SQLite notification database (gitignored)
├── hub.log        # Server logs (gitignored)
└── .gitignore     # Excludes db and logs
```

---

Built by Jarvis de la Ari & Ariel @ Bresleveloper AI 🦞


---

[![YouTube](https://img.shields.io/badge/YouTube-BresleveloperAI-red?logo=youtube)](https://www.youtube.com/@BresleveloperAI/videos)

[ישראלי/דובר עברית? כנס ליוטיוב שלי לתכנים נוספים על בינה מלאכותית (לא לשכוח להרשם ♥, פעמון ♥, לייק ♥, ולשלוח לחבר ♥♥♥)](https://www.youtube.com/@BresleveloperAI/videos)
