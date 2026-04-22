"""
utils/push_db.py
Thread-safe SQLite connector for the push/favorites database.

Separate from the GTFS DB (Backend Basics/db/rts_gtfs.sqlite).
Path: db/push.sqlite  (relative to repo root, created on first access).
"""
import sqlite3
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DB_PATH = _ROOT / "db" / "push.sqlite"
_SCHEMA_PATH = _ROOT / "db" / "push_schema.sql"

_local = threading.local()


def get_push_db() -> sqlite3.Connection:
    """
    Return a thread-local SQLite connection to push.sqlite.
    Creates the file and runs the schema on first access.
    WAL mode + foreign keys enabled.
    """
    if not getattr(_local, "conn", None):
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _run_schema(conn)
    return _local.conn


def init_db() -> None:
    """Explicitly initialise the DB (idempotent). Call once at startup."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _run_schema(conn)
    conn.close()


def _run_schema(conn: sqlite3.Connection) -> None:
    """Execute push_schema.sql (all statements are CREATE IF NOT EXISTS)."""
    if _SCHEMA_PATH.exists():
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.commit()
