import sqlite3
from datetime import datetime, timedelta

import pytest

from db import backup
from db.repositories import projects_repo

T0 = datetime(2026, 10, 5, 9, 0, 0)


def _count(path, table):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def test_snapshot_contains_everything_written_so_far(temp_db, tmp_path):
    # The live database runs in WAL mode, so recent writes can sit in the -wal file and a plain
    # copy of app.db would miss them. The snapshot has to include them.
    projects_repo.get_or_create_id("Alpha")
    projects_repo.get_or_create_id("Beta")

    out = backup.snapshot_database(tmp_path / "snaps", now=T0)

    assert out.name == "app-20261005-090000.db"
    assert _count(out, "projects") == 2


def test_snapshot_is_a_healthy_standalone_database(temp_db, tmp_path):
    out = backup.snapshot_database(tmp_path, now=T0)

    conn = sqlite3.connect(out)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 4
    finally:
        conn.close()


def test_snapshot_is_one_self_contained_file(temp_db, tmp_path):
    # The live database runs in WAL mode, which makes SQLite create -wal and -shm files whenever it
    # is opened. A snapshot must not inherit that: it should open and close without leaving
    # anything beside it, so copying or pruning a snapshot means handling exactly one file.
    snaps = tmp_path / "snaps"
    out = backup.snapshot_database(snaps, now=T0)

    conn = sqlite3.connect(out)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        conn.execute("SELECT COUNT(*) FROM projects").fetchone()
    finally:
        conn.close()

    assert _names(snaps) == ["app-20261005-090000.db"]


def test_pruning_also_removes_leftover_side_files_of_the_pruned_snapshot(temp_db, tmp_path):
    snaps = tmp_path / "snaps"
    first = backup.snapshot_database(snaps, now=T0)
    (snaps / (first.name + "-wal")).write_bytes(b"")
    (snaps / (first.name + "-shm")).write_bytes(b"x")

    for day in range(1, 9):  # pushes the first snapshot out of the newest seven
        backup.snapshot_database(snaps, now=T0 + timedelta(days=day))

    assert not first.exists()
    assert [n for n in _names(snaps) if n.startswith(first.name)] == []


def test_only_the_newest_seven_snapshots_are_kept(temp_db, tmp_path):
    snaps = tmp_path / "snaps"  # tmp_path itself also holds the throwaway test database
    for day in range(9):  # 5 Oct .. 13 Oct
        backup.snapshot_database(snaps, now=T0 + timedelta(days=day))

    assert _names(snaps) == [f"app-202610{d:02d}-090000.db" for d in range(7, 14)]


def test_pruning_never_touches_files_that_are_not_snapshots(temp_db, tmp_path):
    (tmp_path / "pre-move-20261005-110358.db").write_bytes(b"keep me")
    (tmp_path / "notes.txt").write_text("keep me")

    for day in range(9):
        backup.snapshot_database(tmp_path, now=T0 + timedelta(days=day))

    assert (tmp_path / "pre-move-20261005-110358.db").read_bytes() == b"keep me"
    assert (tmp_path / "notes.txt").read_text() == "keep me"


def test_daily_snapshot_is_taken_once_a_day(temp_db, tmp_path):
    first = backup.ensure_daily_snapshot(tmp_path, now=T0)
    same_day = backup.ensure_daily_snapshot(tmp_path, now=T0 + timedelta(hours=23))
    next_day = backup.ensure_daily_snapshot(tmp_path, now=T0 + timedelta(hours=25))

    assert first is not None and next_day is not None
    assert same_day is None
    assert len(list(tmp_path.glob("app-*.db"))) == 2


def test_a_failed_snapshot_leaves_nothing_that_looks_complete(temp_db, tmp_path, monkeypatch):
    # A half-written file must never be mistaken for a good snapshot: it would count as
    # "today's backup" and push out an older, healthy one.
    def broken_copy(target_path):
        target_path.write_bytes(b"half a database")
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup, "_write_copy", broken_copy)
    snaps = tmp_path / "snaps"

    with pytest.raises(RuntimeError):
        backup.snapshot_database(snaps, now=T0)

    assert list(snaps.iterdir()) == []  # no snapshot, and the half-written file is cleaned up
