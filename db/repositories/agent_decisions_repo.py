from datetime import datetime

from ..connection import get_connection


def record(project_id, decision_type, detail, research_task_id=None, research_work_item_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO agent_decisions "
            "(project_id, research_task_id, research_work_item_id, decision_type, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, research_task_id, research_work_item_id, decision_type, detail, now),
        )
        return cur.lastrowid


def list_for_project(project_id, limit=50):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM agent_decisions WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
