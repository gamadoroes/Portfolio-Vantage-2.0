"""Daily snapshots of the live database.

The database lives outside OneDrive because syncing a live SQLite file can corrupt it. Snapshots
are the safety net that replaces OneDrive's version history: each one is a complete, closed file
written with SQLite's own backup API, so it is consistent even while the app is running (a plain
file copy would miss recent writes that are still in the WAL file).
"""
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .connection import get_connection

KEEP = 7
INTERVAL = timedelta(hours=24)
_STAMP = "%Y%m%d-%H%M%S"
# Only files with exactly this shape are ever counted or deleted.
_SNAPSHOT_NAME = re.compile(r"^app-\d{8}-\d{6}\.db$")


def _snapshots(folder):
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if _SNAPSHOT_NAME.match(p.name))


def _write_copy(target_path):
    with get_connection() as source:
        target = sqlite3.connect(str(target_path))
        try:
            source.backup(target)
            # The copy inherits WAL mode, which makes every later open create -wal/-shm files
            # beside it. Make the snapshot one plain, self-contained file instead.
            target.execute("PRAGMA journal_mode = DELETE")
        finally:
            target.close()


def snapshot_database(dest_dir, now=None, keep=KEEP):
    now = now or datetime.now()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    final = dest_dir / f"app-{now.strftime(_STAMP)}.db"
    partial = dest_dir / (final.name + ".partial")
    try:
        _write_copy(partial)
        partial.replace(final)  # a half-written file never has a snapshot's name
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    for old in _snapshots(dest_dir)[:-keep]:
        old.unlink()
        for side in (old.name + "-wal", old.name + "-shm"):
            (dest_dir / side).unlink(missing_ok=True)
    return final


def ensure_daily_snapshot(dest_dir, now=None, keep=KEEP):
    """Take a snapshot unless the newest one is less than a day old. Returns its path, or None."""
    now = now or datetime.now()
    existing = _snapshots(dest_dir)
    if existing:
        newest = datetime.strptime(existing[-1].stem[len("app-"):], _STAMP)  # from the name: sync can change file times
        if now - newest < INTERVAL:
            return None
    return snapshot_database(dest_dir, now=now, keep=keep)
