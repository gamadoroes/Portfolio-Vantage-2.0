import pytest

from db.connection import set_database_path
from db.migrate_runner import apply_migrations


@pytest.fixture
def temp_db(tmp_path):
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    yield
    set_database_path(None)
