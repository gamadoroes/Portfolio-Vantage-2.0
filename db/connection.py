import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

_db_path_override = None


def default_database_path():
    """Per-user, absolute, and outside any synced project folder.

    The first default was the relative path "instance/app.db". It depended on the folder the app
    was launched from, and it sat inside OneDrive, where syncing a live SQLite database can
    corrupt it.

    On Windows this deliberately avoids AppData: the Microsoft Store build of Python silently
    redirects writes under %LOCALAPPDATA% into a private per-package folder, so a database there
    is invisible to Explorer, backup tools and every other Python.
    """
    home = Path(os.path.expanduser("~"))
    if sys.platform == "win32":
        return str(home / "PortfolioVantage" / "app.db")
    return str(home / ".local" / "share" / "PortfolioVantage" / "app.db")


def set_database_path(path):
    """Override the DB path. Pass None to clear the override (tests only)."""
    global _db_path_override
    _db_path_override = path


def get_database_path():
    if _db_path_override:
        return Path(_db_path_override)
    return Path(os.environ.get("DATABASE_PATH") or default_database_path())


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
