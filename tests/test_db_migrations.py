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
    assert count == 6


def test_agent_decisions_and_research_runs_schema(temp_db):
    with get_connection() as conn:
        decision_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(agent_decisions)").fetchall()
        }
        assert "research_work_item_id" in decision_columns

        run_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
        }
        assert "output_text" in run_columns


def test_research_work_items_schema(temp_db):
    with get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"research_work_items", "research_work_item_dependencies"}.issubset(tables)

        work_item_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(research_work_items)").fetchall()
        }
        expected_columns = {
            "id", "project_id", "phase_key", "title", "objective", "status", "priority",
            "research_method", "entities_json", "expected_output", "source_requirements_json",
            "completeness_score", "evidence_score", "identified_gaps_json", "retry_count",
            "max_retries", "human_review_required", "created_at", "updated_at",
        }
        assert expected_columns.issubset(work_item_columns)

        dep_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(research_work_item_dependencies)").fetchall()
        }
        assert {"work_item_id", "depends_on_work_item_id", "created_at"}.issubset(dep_columns)

        run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()}
        assert "research_work_item_id" in run_columns

        artefact_columns = {row["name"] for row in conn.execute("PRAGMA table_info(artefacts)").fetchall()}
        assert "research_work_item_id" in artefact_columns


def test_research_work_items_default_status_is_proposed(temp_db):
    from db.repositories import projects_repo
    pid = projects_repo.get_or_create_id("P")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO research_work_items (project_id, phase_key, title, created_at, updated_at) "
            "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')",
            (pid, "4", "Product / La Trobe"),
        )
        row = conn.execute("SELECT status FROM research_work_items WHERE project_id = ?", (pid,)).fetchone()
        assert row["status"] == "PROPOSED"


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


def test_research_board_columns(temp_db):
    with get_connection() as conn:
        item_cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_work_items)").fetchall()}
        run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_runs)").fetchall()}
    assert {"prompt_text", "framework_key", "rationale", "suggested_from_work_item_id"}.issubset(item_cols)
    assert {"prompt_text", "report_stable_file_id", "report_linked_at"}.issubset(run_cols)


def test_tool_calls_table(temp_db):
    with get_connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
    assert {"id", "project_id", "tool", "caller", "research_work_item_id", "parent_call_id", "input_json",
            "ok", "error_code", "error_message", "result_json", "started_at", "duration_ms"}.issubset(cols)
