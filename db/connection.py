import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_DEFAULT_DB_PATH = "instance/app.db"
_db_path_override = None


def set_database_path(path):
    """Override the DB path. Pass None to clear the override (tests only)."""
    global _db_path_override
    _db_path_override = path


def get_database_path():
    if _db_path_override:
        return Path(_db_path_override)
    return Path(os.environ.get("DATABASE_PATH", _DEFAULT_DB_PATH))


def has_database_path_override():
    """True if set_database_path() has already pinned an explicit path (tests)."""
    return _db_path_override is not None


@contextmanager
def get_connection():
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
    finally:
        conn.close()
