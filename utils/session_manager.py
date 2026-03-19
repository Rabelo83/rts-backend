"""
Server-side session management for RTS chat agent
Secure UUID-based session IDs with automatic cleanup

Persistence: sessions are written to SQLite so they survive server restarts
and Render idle-spin-downs. Set DATA_DIR env var to point to a Render
Persistent Disk mount path (e.g. /data) so they also survive redeploys.
"""
import uuid
import time
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from collections import OrderedDict

# Resolve DB path — respects DATA_DIR env var for Render Persistent Disk
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parents[1] / "data")))
_SESSION_DB_PATH = _DATA_DIR / "sessions.sqlite"


class SessionManager:
    """
    Thread-safe session manager with automatic expiration and SQLite persistence.

    Features:
    - Server-generated UUIDs (cryptographically secure)
    - Automatic session expiration (30 minutes inactivity)
    - SQLite write-through — sessions survive server restarts and Render spin-downs
    - Max sessions limit (prevents memory exhaustion)
    - Thread-safe operations
    """

    def __init__(self, timeout_seconds: int = 300, max_sessions: int = 10000,
                 db_path: Path = _SESSION_DB_PATH):
        self.timeout_seconds = timeout_seconds
        self.max_sessions = max_sessions
        self._db_path = db_path
        self._sessions: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._created_count = 0
        self._expired_count = 0
        self._init_db()

    # ------------------------------------------------------------------
    # SQLite helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create sessions table if it doesn't exist."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id   TEXT PRIMARY KEY,
                    created_at   TEXT,
                    last_activity TEXT,
                    message_count INTEGER DEFAULT 0,
                    history      TEXT DEFAULT '[]',
                    context      TEXT DEFAULT '{}'
                )
            """)
            conn.commit()
            conn.close()
        except Exception:
            pass  # If DB unavailable, fall back to memory-only

    def _db_save(self, session: Dict[str, Any]) -> None:
        """Upsert a session row to SQLite."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                INSERT INTO sessions (session_id, created_at, last_activity,
                                      message_count, history, context)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_activity = excluded.last_activity,
                    message_count = excluded.message_count,
                    history       = excluded.history,
                    context       = excluded.context
            """, (
                session["session_id"],
                session["created_at"],
                session["last_activity"],
                session["message_count"],
                json.dumps(session["history"]),
                json.dumps(session["context"]),
            ))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _db_load(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a session from SQLite (used on cache miss after restart)."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            conn.close()
            if row is None:
                return None
            return {
                "session_id":    row["session_id"],
                "created_at":    row["created_at"],
                "last_activity": row["last_activity"],
                "message_count": row["message_count"],
                "history":       json.loads(row["history"]),
                "context":       json.loads(row["context"]),
            }
        except Exception:
            return None

    def _db_delete(self, session_id: str) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _db_delete_expired(self, cutoff_iso: str) -> int:
        try:
            conn = sqlite3.connect(self._db_path)
            cur = conn.execute(
                "DELETE FROM sessions WHERE last_activity < ?", (cutoff_iso,)
            )
            count = cur.rowcount
            conn.commit()
            conn.close()
            return count
        except Exception:
            return 0

    def create_session(self, initial_data: Optional[Dict[str, Any]] = None) -> str:
        """Create a new session with server-generated UUID."""
        with self._lock:
            session_id = str(uuid.uuid4())
            session_data = {
                "session_id": session_id,
                "created_at": datetime.utcnow().isoformat(),
                "last_activity": datetime.utcnow().isoformat(),
                "message_count": 0,
                "history": [],
                "context": initial_data or {},
            }
            self._sessions[session_id] = session_data
            self._sessions.move_to_end(session_id)
            self._created_count += 1
            while len(self._sessions) > self.max_sessions:
                self._evict_oldest()
            self._db_save(session_data)
            return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data, loading from SQLite on cache miss (post-restart recovery)."""
        with self._lock:
            # Cache miss — try to restore from SQLite (happens after server restart)
            if session_id not in self._sessions:
                restored = self._db_load(session_id)
                if restored:
                    self._sessions[session_id] = restored
                else:
                    return None

            session = self._sessions[session_id]

            # Check if expired
            last_activity = datetime.fromisoformat(session["last_activity"])
            if datetime.utcnow() - last_activity > timedelta(seconds=self.timeout_seconds):
                del self._sessions[session_id]
                self._db_delete(session_id)
                self._expired_count += 1
                return None

            session["last_activity"] = datetime.utcnow().isoformat()
            self._sessions.move_to_end(session_id)
            return session

    def update_session(self, session_id: str, data: Dict[str, Any]) -> bool:
        """
        Update session data

        Args:
            session_id: Session ID to update
            data: Data to merge into session context

        Returns:
            True if successful, False if session not found
        """
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                return False

            # Merge data into context
            session["context"].update(data)
            session["last_activity"] = datetime.utcnow().isoformat()
            session["message_count"] += 1

            return True

    def add_message(self, session_id: str, role: str, content: str) -> bool:
        """
        Add a message to session history

        Args:
            session_id: Session ID
            role: Message role ("user" or "assistant")
            content: Message content

        Returns:
            True if successful, False if session not found
        """
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                return False

            message = {
                "role": role,
                "content": content,
                "timestamp": datetime.utcnow().isoformat()
            }

            session["history"].append(message)
            session["last_activity"] = datetime.utcnow().isoformat()

            # Keep last 50 messages only
            if len(session["history"]) > 50:
                session["history"] = session["history"][-50:]

            self._db_save(session)
            return True

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session

        Args:
            session_id: Session ID to delete

        Returns:
            True if deleted, False if not found
        """
        with self._lock:
            found = session_id in self._sessions
            if found:
                del self._sessions[session_id]
            self._db_delete(session_id)
            return found

    def cleanup_expired(self) -> int:
        """
        Remove all expired sessions

        Returns:
            Number of sessions removed
        """
        with self._lock:
            current_time = datetime.utcnow()
            timeout_delta = timedelta(seconds=self.timeout_seconds)

            expired_ids = [
                sid for sid, session in self._sessions.items()
                if current_time - datetime.fromisoformat(session["last_activity"]) > timeout_delta
            ]

            for sid in expired_ids:
                del self._sessions[sid]
                self._expired_count += 1

            # Also purge expired rows from SQLite
            cutoff = (datetime.utcnow() - timeout_delta).isoformat()
            self._db_delete_expired(cutoff)

            return len(expired_ids)

    def _evict_oldest(self) -> None:
        """Evict oldest session (must be called with lock held)"""
        if self._sessions:
            self._sessions.popitem(last=False)

    def stats(self) -> dict:
        """
        Get session statistics

        Returns:
            Dictionary with session stats
        """
        with self._lock:
            return {
                "active_sessions": len(self._sessions),
                "max_sessions": self.max_sessions,
                "timeout_seconds": self.timeout_seconds,
                "created_count": self._created_count,
                "expired_count": self._expired_count,
            }

    def get_all_sessions(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all active sessions (for debugging/admin)

        Returns:
            Dictionary of all sessions
        """
        with self._lock:
            return dict(self._sessions)


# ============================================================
# GLOBAL SESSION MANAGER INSTANCE
# ============================================================

# 30-minute timeout, max 10000 concurrent sessions
session_manager = SessionManager(timeout_seconds=1800, max_sessions=10000)


# ============================================================
# BACKGROUND CLEANUP TASK (optional)
# ============================================================

def start_cleanup_task(interval_seconds: int = 60):
    """
    Start background thread to periodically clean up expired sessions

    Args:
        interval_seconds: Cleanup interval in seconds
    """
    def cleanup_loop():
        while True:
            time.sleep(interval_seconds)
            removed = session_manager.cleanup_expired()
            if removed > 0:
                print(f"[SessionManager] Cleaned up {removed} expired sessions")

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()
