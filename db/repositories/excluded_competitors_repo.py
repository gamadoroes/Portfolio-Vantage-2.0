from datetime import datetime

from ..connection import get_connection


def add(project_id, competitor_name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO excluded_competitors (project_id, competitor_name, created_at) "
            "VALUES (?, ?, ?)",
            (project_id, competitor_name, now),
        )


def remove(project_id, competitor_name):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM excluded_competitors WHERE project_id = ? AND competitor_name = ?",
            (project_id, competitor_name),
        )


def list_for_project(project_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT competitor_name FROM excluded_competitors WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return [r["competitor_name"] for r in rows]
