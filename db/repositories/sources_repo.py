from datetime import datetime

from ..connection import get_connection


def upsert(project_id, stable_file_id, filename, kind="upload"):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM sources WHERE project_id = ? AND stable_file_id = ?",
            (project_id, stable_file_id),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO sources (project_id, stable_file_id, filename, kind, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, stable_file_id, filename, kind, now, now),
        )
        return cur.lastrowid


def get_by_stable_id(project_id, stable_file_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE project_id = ? AND stable_file_id = ?",
            (project_id, stable_file_id),
        ).fetchone()


def get_by_filename(project_id, filename):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE project_id = ? AND filename = ?",
            (project_id, filename),
        ).fetchone()


def list_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE project_id = ? ORDER BY filename", (project_id,)
        ).fetchall()


def rename(source_id, new_filename):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE sources SET filename = ?, updated_at = ? WHERE id = ?",
            (new_filename, now, source_id),
        )


def delete(source_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM project_selected_sources WHERE source_id = ?", (source_id,))
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))


def set_selected(project_id, source_id, selected):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        if selected:
            conn.execute(
                "INSERT OR IGNORE INTO project_selected_sources (project_id, source_id, created_at) "
                "VALUES (?, ?, ?)",
                (project_id, source_id, now),
            )
        else:
            conn.execute(
                "DELETE FROM project_selected_sources WHERE project_id = ? AND source_id = ?",
                (project_id, source_id),
            )


def list_selected_ids(project_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT source_id FROM project_selected_sources WHERE project_id = ? "
            "ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return [r["source_id"] for r in rows]
