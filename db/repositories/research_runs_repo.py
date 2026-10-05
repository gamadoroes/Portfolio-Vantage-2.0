from datetime import datetime, timedelta

from ..connection import get_connection

# How long a fact-extraction claim may sit with no facts saved before it is treated as a crashed call.
STALE_EXTRACTION_MINUTES = 15


def create(id, project_id, response_id, chat_session_id, prompt_preview, status="running"):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO research_runs "
            "(id, project_id, response_id, chat_session_id, status, prompt_preview, "
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id, project_id, response_id, chat_session_id, status, prompt_preview, now, now),
        )


def get(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM research_runs WHERE id = ?", (id,)).fetchone()


def update(id, **fields):
    if not fields:
        return get(id) is not None
    fields["updated_at"] = datetime.now().isoformat()
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [id]
    with get_connection() as conn:
        cur = conn.execute(f"UPDATE research_runs SET {columns} WHERE id = ?", values)
        return cur.rowcount > 0


def list_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_runs WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()


def find_recent_running_or_queued_with_preview(project_id, prompt_preview, cutoff_iso):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_runs WHERE project_id = ? AND prompt_preview = ? "
            "AND status IN ('queued', 'running') AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id, prompt_preview, cutoff_iso),
        ).fetchone()


def find_latest_for_work_item(work_item_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_runs WHERE research_work_item_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (work_item_id,),
        ).fetchone()


def claim_facts_extraction(id):
    """Mark this run's report as mined for facts. True only for the call that set it, so a
    double-click (or two tabs) cannot pay for the same report twice.

    A claim older than STALE_EXTRACTION_MINUTES that produced no fact is taken to be a call that died
    with the process, and can be claimed again."""
    now = datetime.now()
    cutoff = (now - timedelta(minutes=STALE_EXTRACTION_MINUTES)).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE research_runs SET facts_extracted_at = ? WHERE id = ? AND (facts_extracted_at IS NULL "
            "OR (facts_extracted_at < ? AND NOT EXISTS (SELECT 1 FROM facts WHERE run_id = ?)))",
            (now.isoformat(), id, cutoff, id),
        )
        return cur.rowcount == 1


def release_facts_extraction(id):
    """Undo a claim when nothing was taken from the report, so the person can try again."""
    with get_connection() as conn:
        conn.execute("UPDATE research_runs SET facts_extracted_at = NULL WHERE id = ?", (id,))
