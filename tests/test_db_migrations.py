from db.connection import get_connection
from db.migrate_runner import apply_migrations


def test_apply_migrations_creates_all_tables(temp_db):
    expected = {
        "projects", "sources", "project_selected_sources", "artefacts",
        "chat_sessions", "project_messages", "research_runs", "research_tasks",
        "project_insight_versions", "findings", "evidence", "finding_evidence",
        "agent_decisions", "excluded_competitors", "schema_migrations",
    }
    with get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert expected.issubset(tables)


def test_apply_migrations_is_idempotent(temp_db):
    apply_migrations()
    apply_migrations()
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    assert count == 2


def test_evidence_table_has_filename_snapshot_columns(temp_db):
    with get_connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(evidence)").fetchall()}
    assert {"filename", "stable_file_id"}.issubset(columns)


def test_foreign_keys_enforced(temp_db):
    with get_connection() as conn:
        with_fk_error = False
        try:
            conn.execute(
                "INSERT INTO sources (project_id, stable_file_id, filename, created_at, updated_at) "
                "VALUES (9999, 'f_doesnotexist', 'x.txt', '2026-01-01', '2026-01-01')"
            )
        except Exception:
            with_fk_error = True
        assert with_fk_error
