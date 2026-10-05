import shutil
import sqlite3
import threading
import time

import pytest

from app import create_app
from db import backup, migrate_runner
from db.connection import set_database_path

OLD_VERSIONS = {"0001_initial", "0002_evidence_filename_snapshot"}
PHASE_2_AND_3_VERSIONS = {"0003_research_work_items", "0004_agent_decisions_work_items"}


def _applied_versions(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    finally:
        conn.close()


@pytest.fixture
def outdated_db(tmp_path, monkeypatch):
    """A database left at migrations 0001-0002, like an install from before Phase 2."""
    monkeypatch.chdir(tmp_path)
    old_migrations = tmp_path / "old_migrations"
    old_migrations.mkdir()
    for name in ("0001_initial.sql", "0002_evidence_filename_snapshot.sql"):
        shutil.copy(migrate_runner.MIGRATIONS_DIR / name, old_migrations / name)

    db_path = tmp_path / "app.db"
    set_database_path(str(db_path))
    with monkeypatch.context() as patch:
        patch.setattr(migrate_runner, "MIGRATIONS_DIR", old_migrations)
        migrate_runner.apply_migrations()
    assert _applied_versions(db_path) == OLD_VERSIONS
    yield db_path
    set_database_path(None)


def test_first_request_brings_an_outdated_database_up_to_date(outdated_db):
    client = create_app().test_client()

    client.get("/")

    assert PHASE_2_AND_3_VERSIONS <= _applied_versions(outdated_db)


def test_creating_the_app_does_not_touch_the_database(tmp_path):
    # `from app import app` runs create_app() at import time, including while
    # pytest collects tests. Migrating inside create_app() would therefore
    # modify whatever database sits at the default path as a side effect of an
    # import.
    db_path = tmp_path / "never_created.db"
    set_database_path(str(db_path))
    try:
        create_app()
    finally:
        set_database_path(None)

    assert not db_path.exists()


def test_simultaneous_first_requests_apply_migrations_only_once(outdated_db, monkeypatch):
    calls = []
    real_apply = migrate_runner.apply_migrations

    def slow_counting_apply():
        calls.append(1)
        time.sleep(0.2)  # widen the window in which a second request could slip in
        real_apply()

    monkeypatch.setattr(migrate_runner, "apply_migrations", slow_counting_apply)
    flask_app = create_app()

    threads = [threading.Thread(target=lambda: flask_app.test_client().get("/")) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert PHASE_2_AND_3_VERSIONS <= _applied_versions(outdated_db)


def test_a_request_takes_one_daily_snapshot_not_one_per_request(temp_db, tmp_path):
    flask_app = create_app()
    flask_app.config["BACKUP_DIR"] = str(tmp_path / "snaps")
    client = flask_app.test_client()

    client.get("/")
    client.get("/")
    client.get("/")

    assert len(list((tmp_path / "snaps").glob("app-*.db"))) == 1


def test_a_failing_snapshot_never_breaks_a_request(temp_db, tmp_path, monkeypatch):
    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "ensure_daily_snapshot", disk_full)
    flask_app = create_app()
    flask_app.config["BACKUP_DIR"] = str(tmp_path / "snaps")

    assert flask_app.test_client().get("/").status_code == 200
