import os
import tempfile

import pytest

# Snapshots default to a folder inside the repo. Point them at a throwaway folder for the whole test
# run, before any test module imports config, so tests can never write to or prune real backups.
os.environ["BACKUP_DIR"] = tempfile.mkdtemp(prefix="vantage-test-backups-")

from db.connection import set_database_path  # noqa: E402
from db.migrate_runner import apply_migrations


@pytest.fixture
def temp_db(tmp_path):
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    yield
    set_database_path(None)
