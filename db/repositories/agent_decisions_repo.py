from datetime import datetime

from ..connection import get_connection


def record(project_id, decision_type, detail, research_task_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO agent_decisions (project_id, research_task_id, decision_type, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, research_task_id, decision_type, detail, now),
        )
        return cur.lastrowid
