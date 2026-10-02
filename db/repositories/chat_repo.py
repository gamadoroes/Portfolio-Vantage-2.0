from datetime import datetime

from ..connection import get_connection


def create_session(id, project_id, name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (id, project_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (id, project_id, name, now, now),
        )


def get_session(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (id,)).fetchone()


def rename_session(id, new_name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE chat_sessions SET name = ?, updated_at = ? WHERE id = ?", (new_name, now, id)
        )


def list_sessions_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM chat_sessions WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()


def delete_session(id):
    with get_connection() as conn:
        conn.execute("DELETE FROM project_messages WHERE chat_session_id = ?", (id,))
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (id,))


def append_message(chat_session_id, role, content, artefact_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM project_messages WHERE chat_session_id = ?",
            (chat_session_id,),
        ).fetchone()
        next_seq = row["max_seq"] + 1
        conn.execute(
            "INSERT INTO project_messages (chat_session_id, seq, role, content, artefact_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_session_id, next_seq, role, content, artefact_id, now),
        )
        return next_seq


def list_messages(chat_session_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM project_messages WHERE chat_session_id = ? ORDER BY seq",
            (chat_session_id,),
        ).fetchall()


def link_artefact_to_message(chat_session_id, seq, artefact_id):
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE project_messages SET artefact_id = ? WHERE chat_session_id = ? AND seq = ?",
            (artefact_id, chat_session_id, seq),
        )
        return cur.rowcount > 0
