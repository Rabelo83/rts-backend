"""
Server-side session management for RTS chat agent
Secure UUID-based session IDs with automatic cleanup
"""
import uuid
import time
import threading
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from collections import OrderedDict


class SessionManager:
    """
    Thread-safe session manager with automatic expiration

    Features:
    - Server-generated UUIDs (cryptographically secure)
    - Automatic session expiration (30 minutes inactivity)
    - Max sessions limit (prevents memory exhaustion)
    - Thread-safe operations
    - Session statistics
    """

    def __init__(self, timeout_seconds: int = 300, max_sessions: int = 10000):
        """
        Initialize session manager

        Args:
            timeout_seconds: Session timeout in seconds (default: 30 minutes)
            max_sessions: Maximum number of concurrent sessions
        """
        self.timeout_seconds = timeout_seconds
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()

        # Statistics
        self._created_count = 0
        self._expired_count = 0

    def create_session(self, initial_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Create a new session with server-generated UUID

        Args:
            initial_data: Optional initial session data

        Returns:
            Session ID (UUID string)
        """
        with self._lock:
            # Generate secure UUID
            session_id = str(uuid.uuid4())

            # Create session data
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

            # Evict oldest sessions if over limit
            while len(self._sessions) > self.max_sessions:
                self._evict_oldest()

            return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session data and update last activity time

        Args:
            session_id: Session ID to retrieve

        Returns:
            Session data dict or None if not found/expired
        """
        with self._lock:
            if session_id not in self._sessions:
                return None

            session = self._sessions[session_id]

            # Check if expired
            last_activity = datetime.fromisoformat(session["last_activity"])
            if datetime.utcnow() - last_activity > timedelta(seconds=self.timeout_seconds):
                del self._sessions[session_id]
                self._expired_count += 1
                return None

            # Update last activity
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
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

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
