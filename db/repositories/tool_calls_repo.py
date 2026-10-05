from datetime import datetime

from ..connection import get_connection


def create(project_id, tool, caller, input_json, parent_call_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO tool_calls (project_id, tool, caller, parent_call_id, input_json, ok, started_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (project_id, tool, caller, parent_call_id, input_json, now),
        )
        return cur.lastrowid


def finish(id, ok, error_code=None, error_message=None, result_json=None, duration_ms=None,
           research_work_item_id=None):
    with get_connection() as conn:
        conn.execute(
            "UPDATE tool_calls SET ok = ?, error_code = ?, error_message = ?, result_json = ?, "
            "duration_ms = ?, research_work_item_id = ? WHERE id = ?",
            (1 if ok else 0, error_code, error_message, result_json, duration_ms, research_work_item_id, id),
        )


def get(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM tool_calls WHERE id = ?", (id,)).fetchone()


def list_for_project(project_id, limit=100):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM tool_calls WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit)
        ).fetchall()
