from datetime import datetime

from ..connection import get_connection


def create(
    project_id, phase_key, title, objective=None, priority=None, research_method=None,
    entities_json=None, expected_output=None, source_requirements_json=None,
    human_review_required=0, max_retries=3,
):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO research_work_items "
            "(project_id, phase_key, title, objective, priority, research_method, "
            " entities_json, expected_output, source_requirements_json, "
            " human_review_required, max_retries, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id, phase_key, title, objective, priority, research_method,
                entities_json, expected_output, source_requirements_json,
                human_review_required, max_retries, now, now,
            ),
        )
        return cur.lastrowid


def get(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM research_work_items WHERE id = ?", (id,)).fetchone()


def list_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_work_items WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()


def list_for_phase(project_id, phase_key):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_work_items WHERE project_id = ? AND phase_key = ? ORDER BY created_at",
            (project_id, phase_key),
        ).fetchall()


def update_fields(id, **fields):
    if not fields:
        return get(id) is not None
    fields["updated_at"] = datetime.now().isoformat()
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [id]
    with get_connection() as conn:
        cur = conn.execute(f"UPDATE research_work_items SET {columns} WHERE id = ?", values)
        return cur.rowcount > 0


def add_dependency(work_item_id, depends_on_work_item_id):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO research_work_item_dependencies "
            "(work_item_id, depends_on_work_item_id, created_at) VALUES (?, ?, ?)",
            (work_item_id, depends_on_work_item_id, now),
        )


def remove_dependency(work_item_id, depends_on_work_item_id):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM research_work_item_dependencies "
            "WHERE work_item_id = ? AND depends_on_work_item_id = ?",
            (work_item_id, depends_on_work_item_id),
        )


def list_dependencies(work_item_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT depends_on_work_item_id FROM research_work_item_dependencies WHERE work_item_id = ?",
            (work_item_id,),
        ).fetchall()


def list_dependents(work_item_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT work_item_id FROM research_work_item_dependencies WHERE depends_on_work_item_id = ?",
            (work_item_id,),
        ).fetchall()
