"""
utils/ridership_db.py
Thread-safe SQLite connector for the RTS Pulse daily ridership accumulation DB.

Separate from the GTFS DB (Backend Basics/db/rts_gtfs.sqlite) and the push DB
(db/push.sqlite) -- same DATA_DIR-aware pattern as utils/push_db.py.

Path resolution:
- If DATA_DIR is set (Render persistent disk mount, typically /data),
  the DB lives at $DATA_DIR/ridership.sqlite so it survives redeploys.
- Otherwise, db/ridership.sqlite at the repo root (local dev).
"""
import os
import sqlite3
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _ROOT / "db" / "ridership_schema.sql"

_data_dir = os.getenv("DATA_DIR", "").strip()
if _data_dir:
    _DB_PATH = Path(_data_dir) / "ridership.sqlite"
else:
    _DB_PATH = _ROOT / "db" / "ridership.sqlite"

_local = threading.local()


def get_ridership_db() -> sqlite3.Connection:
    """
    Return a thread-local SQLite connection to ridership.sqlite.
    Creates the file and runs the schema on first access. WAL mode.
    """
    if not getattr(_local, "conn", None):
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
        _run_schema(conn)
    return _local.conn


def init_db() -> None:
    """Explicitly initialise the DB (idempotent). Call once at startup."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _run_schema(conn)
    conn.close()


def _run_schema(conn: sqlite3.Connection) -> None:
    if _SCHEMA_PATH.exists():
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.commit()
