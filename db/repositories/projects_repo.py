from datetime import datetime

from ..connection import get_connection


def get_or_create_id(name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO projects (name, description, archived, created_at, updated_at) "
            "VALUES (?, '', 0, ?, ?)",
            (name, now, now),
        )
        return cur.lastrowid


def get_id(name):
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None


def exists(name):
    return get_id(name) is not None


def get_created_at(name):
    with get_connection() as conn:
        row = conn.execute("SELECT created_at FROM projects WHERE name = ?", (name,)).fetchone()
        return row["created_at"] if row else None


def get_metadata(name):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT description, archived FROM projects WHERE name = ?", (name,)
        ).fetchone()
    if not row:
        return {"description": "", "archived": False}
    return {"description": row["description"] or "", "archived": bool(row["archived"])}


def save_metadata(name, description, archived):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE projects SET description = ?, archived = ?, updated_at = ? WHERE name = ?",
            (description, 1 if archived else 0, now, name),
        )


def list_all():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, description, archived FROM projects ORDER BY name"
        ).fetchall()
    return [
        {"name": r["name"], "description": r["description"] or "", "archived": bool(r["archived"])}
        for r in rows
    ]
