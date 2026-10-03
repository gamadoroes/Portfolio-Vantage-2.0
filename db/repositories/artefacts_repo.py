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
        # Detach (don't delete) any rows in OTHER tables pointing at this
        # artefact first: both research_runs.artefact_id and
        # project_messages.artefact_id are nullable FKs with no ON DELETE
        # clause, and foreign_keys=ON, so deleting an artefact still
        # referenced by either would raise sqlite3.IntegrityError. Setting
        # artefact_id to NULL on each preserves the referencing rows --
        # research runs and chat messages alike -- intact; it only degrades
        # "linked to a generated artefact" to an orphaned/unlinked
        # reference, which is the correct degrade path once the artefact is
        # gone.
        conn.execute("UPDATE project_messages SET artefact_id = NULL WHERE artefact_id = ?", (id,))
        conn.execute("UPDATE research_runs SET artefact_id = NULL WHERE artefact_id = ?", (id,))
        conn.execute("DELETE FROM artefacts WHERE id = ?", (id,))
