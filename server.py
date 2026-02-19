#!/usr/bin/env python3
"""
Main Hub - Central notification system for all apps/crons
Any service can POST notifications here → wakes Jarvis → Jarvis responds
"""

import json
import os
import sqlite3
import datetime
import urllib.request
import subprocess
import re
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configuration
HOST = "127.0.0.1"  # Internal only
PORT = 10020
DB_FILE = Path(__file__).parent / "hub.db"
LOG_FILE = Path(__file__).parent / "hub.log"

# OpenClaw API
OPENCLAW_HOST = os.environ.get("OPENCLAW_HOST", "127.0.0.1")
OPENCLAW_PORT = int(os.environ.get("OPENCLAW_PORT", "0"))
OPENCLAW_TOKEN = os.environ.get("OPENCLAW_TOKEN", "")
ARIEL_PHONE = os.environ.get("ARIEL_PHONE", "")
SSH_ALERT_IGNORE_USERS = {
    u.strip().lower() for u in os.environ.get("SSH_ALERT_IGNORE_USERS", "opusstudio").split(",") if u.strip()
}

def log(msg):
    timestamp = datetime.datetime.now().isoformat()
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + "\n")


def should_ignore_notification(source, title, message):
    """Drop noisy SSH login alerts for allowlisted service users."""
    text = f"{title}\n{message}".lower()
    if source.lower() != "ssh":
        return False, None

    for user in SSH_ALERT_IGNORE_USERS:
        if re.search(rf"\buser\s*[:=]\s*{re.escape(user)}\b", text) or re.search(rf"\bfor\s+{re.escape(user)}\b", text):
            return True, user

    return False, None

# ============ DATABASE ============

def init_db():
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT,
            message TEXT NOT NULL,
            priority TEXT DEFAULT 'normal',
            status TEXT DEFAULT 'pending',
            response TEXT,
            responded_at TEXT
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON notifications(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_source ON notifications(source)')
    conn.commit()
    conn.close()
    log("DB initialized")

def db_insert(source, title, message, priority='normal'):
    conn = sqlite3.connect(str(DB_FILE))
    timestamp = datetime.datetime.now().isoformat()
    cursor = conn.execute(
        'INSERT INTO notifications (timestamp, source, title, message, priority, status) VALUES (?, ?, ?, ?, ?, ?)',
        (timestamp, source, title, message, priority, 'pending')
    )
    notif_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return notif_id

def db_get_pending():
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        'SELECT * FROM notifications WHERE status = ? ORDER BY id ASC',
        ('pending',)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def db_get_recent(limit=20):
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        'SELECT * FROM notifications ORDER BY id DESC LIMIT ?',
        (limit,)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def db_mark_done(notif_id, response=None):
    conn = sqlite3.connect(str(DB_FILE))
    responded_at = datetime.datetime.now().isoformat()
    conn.execute(
        'UPDATE notifications SET status = ?, response = ?, responded_at = ? WHERE id = ?',
        ('done', response, responded_at, notif_id)
    )
    conn.commit()
    conn.close()

# ============ OPENCLAW ============

def wake_jarvis(notif_id, source, title, message, priority):
    """Send raw hub notification first, then send Jarvis response."""
    try:
        priority_emoji = {"urgent": "🚨", "high": "❗", "normal": "📬", "low": "📝"}.get(priority, "📬")
        target = ARIEL_PHONE or '+972542634114'

        raw_text = f"""Hub Notification #{notif_id}

{priority_emoji} **{source}**{f': {title}' if title else ''}

{message}"""

        # 1) Always send the raw notification first so Ariel sees original content.
        raw_cmd = [
            'sudo', '-n', '/usr/bin/openclaw', 'message', 'send',
            '--channel', 'whatsapp',
            '--target', target,
            '--message', raw_text
        ]
        raw_res = subprocess.run(raw_cmd, capture_output=True, text=True, timeout=60)
        if raw_res.returncode != 0:
            log(f"ERROR sending raw hub notification #{notif_id}: rc={raw_res.returncode} stderr={raw_res.stderr[:200]}")
            return False

        # 2) Then ask Jarvis to respond briefly.
        prompt = f"""You just received this hub alert:

{priority_emoji} **{source}**{f': {title}' if title else ''}

{message}

Reply briefly and clearly."""
        agent_cmd = [
            'sudo', '-n', '/usr/bin/openclaw', 'agent',
            '--to', target,
            '--channel', 'whatsapp',
            '--message', prompt,
            '--deliver',
            '--timeout', '120',
            '--json'
        ]
        # Run response generation in background so /notify never blocks on model latency.
        subprocess.Popen(agent_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"Jarvis queued response #{notif_id} (raw sent immediately)")
        return True
    except Exception as e:
        log(f"ERROR calling Jarvis for #{notif_id}: {e}")
        return False

# ============ HTTP SERVER ============

class HubHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def do_GET(self):
        if self.path == '/':
            # Status page
            pending = db_get_pending()
            recent = db_get_recent(10)
            self.send_json({
                "status": "ok",
                "pending_count": len(pending),
                "recent": recent
            })
        
        elif self.path == '/pending':
            # Get pending notifications
            pending = db_get_pending()
            self.send_json({"notifications": pending})
        
        elif self.path.startswith('/history'):
            # Get recent history
            recent = db_get_recent(50)
            self.send_json({"notifications": recent})
        
        else:
            self.send_json({"error": "Not found"}, 404)
    
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode() if content_length > 0 else '{}'
        
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}
        
        if self.path == '/notify':
            # New notification
            source = data.get('source', 'unknown')
            title = data.get('title', '')
            message = data.get('message', '')
            priority = data.get('priority', 'normal')
            
            if not message:
                self.send_json({"error": "message required"}, 400)
                return
            
            ignored, ignored_user = should_ignore_notification(source, title, message)
            if ignored:
                log(f"Ignored SSH alert for user '{ignored_user}' from source '{source}'")
                self.send_json({
                    "ok": True,
                    "ignored": True,
                    "reason": f"ssh alert ignored for user {ignored_user}"
                })
                return

            # Insert to DB
            notif_id = db_insert(source, title, message, priority)
            log(f"New notification #{notif_id} from {source}: {title or message[:50]}")
            
            # Wake Jarvis
            wake_jarvis(notif_id, source, title, message, priority)
            
            self.send_json({
                "ok": True,
                "id": notif_id,
                "message": "Notification sent to Jarvis"
            })
        
        elif self.path.startswith('/done/'):
            # Mark notification as done
            try:
                notif_id = int(self.path.split('/')[-1])
                response = data.get('response', '')
                db_mark_done(notif_id, response)
                log(f"Marked #{notif_id} as done")
                self.send_json({"ok": True})
            except:
                self.send_json({"error": "Invalid ID"}, 400)
        
        else:
            self.send_json({"error": "Not found"}, 404)

# ============ MAIN ============

def main():
    log("=== Hub starting ===")
    init_db()
    
    server = HTTPServer((HOST, PORT), HubHandler)
    log(f"Hub listening on http://{HOST}:{PORT}")
    log("Endpoints:")
    log("  POST /notify - Send notification {source, title, message, priority}")
    log("  POST /done/<id> - Mark notification done {response}")
    log("  GET /pending - List pending notifications")
    log("  GET /history - Recent notifications")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down")

if __name__ == '__main__':
    main()
