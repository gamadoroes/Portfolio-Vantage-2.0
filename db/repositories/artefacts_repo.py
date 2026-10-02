from datetime import datetime

from ..connection import get_connection


def upsert(id, project_id, name, source_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM artefacts WHERE id = ?", (id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE artefacts SET name = ?, source_id = ?, updated_at = ? WHERE id = ?",
                (name, source_id, now, id),
            )
        else:
            conn.execute(
                "INSERT INTO artefacts (id, project_id, source_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (id, project_id, source_id, name, now, now),
            )


def get(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM artefacts WHERE id = ?", (id,)).fetchone()


def list_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM artefacts WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()


def rename(id, new_name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE artefacts SET name = ?, updated_at = ? WHERE id = ?", (new_name, now, id))


def delete(id):
    with get_connection() as conn:
        conn.execute("DELETE FROM artefacts WHERE id = ?", (id,))
