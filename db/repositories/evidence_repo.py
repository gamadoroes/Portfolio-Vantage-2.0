# db/repositories/evidence_repo.py
"""Facts, the web pages they cite, and conclusions (Phase 4b). All SQL for these tables lives here.

Not to be confused with the older `evidence` / `findings` tables, which hold the Insights phase write-ups.
"""
from datetime import datetime

from ..connection import get_connection

_FACT_SELECT = (
    "SELECT f.*, s.url AS source_url, s.title AS source_title, s.publisher AS source_publisher, "
    "s.published_date AS source_published_date, w.title AS card_title "
    "FROM facts f "
    "LEFT JOIN cited_sources s ON s.id = f.cited_source_id "
    "LEFT JOIN research_work_items w ON w.id = f.research_work_item_id "
)


def _now():
    return datetime.now().isoformat()


def _like(word):
    """A LIKE pattern that matches the word anywhere, with % and _ taken literally."""
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def get_or_create_source(project_id, url, title, publisher=None, published_date=None):
    """(id, created). The same address in the same project returns the page already saved."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO cited_sources (project_id, url, title, publisher, published_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, url) DO NOTHING",
            (project_id, url, title, publisher, published_date, _now()),
        )
        created = cur.rowcount == 1
        row = conn.execute(
            "SELECT id FROM cited_sources WHERE project_id = ? AND url = ?", (project_id, url)
        ).fetchone()
    return row["id"], created


def get_source(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM cited_sources WHERE id = ?", (id,)).fetchone()


def get_or_create_fact(project_id, phase_key, research_work_item_id, claim, claim_key,
                       quote=None, cited_source_id=None, as_of=None, run_id=None):
    """(id, created). The same claim_key in the same project returns the fact already saved, whatever its status."""
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO facts (project_id, phase_key, claim, claim_key, quote, cited_source_id, as_of, "
            " research_work_item_id, run_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?) ON CONFLICT(project_id, claim_key) DO NOTHING",
            (project_id, phase_key, claim, claim_key, quote, cited_source_id, as_of,
             research_work_item_id, run_id, now, now),
        )
        created = cur.rowcount == 1
        row = conn.execute(
            "SELECT id FROM facts WHERE project_id = ? AND claim_key = ?", (project_id, claim_key)
        ).fetchone()
    return row["id"], created


def get_fact(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM facts WHERE id = ?", (id,)).fetchone()


def set_fact_status(id, status):
    with get_connection() as conn:
        conn.execute("UPDATE facts SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), id))


def create_conclusion(project_id, phase_key, research_work_item_id, text, fact_ids):
    """One conclusion and its links to the facts behind it, written together."""
    now = _now()
    with get_connection() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                "INSERT INTO conclusions (project_id, phase_key, text, research_work_item_id, status, "
                " created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (project_id, phase_key, text, research_work_item_id, now, now),
            )
            conclusion_id = cur.lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO conclusion_facts (conclusion_id, fact_id) VALUES (?, ?)",
                [(conclusion_id, fact_id) for fact_id in fact_ids],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return conclusion_id


def get_conclusion(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM conclusions WHERE id = ?", (id,)).fetchone()


def set_conclusion_status(id, status):
    with get_connection() as conn:
        conn.execute("UPDATE conclusions SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), id))


def list_facts(project_id, phase_key=None):
    """Every fact of the project (rejected ones too), oldest first, with its page and research title."""
    sql, params = _FACT_SELECT + "WHERE f.project_id = ?", [project_id]
    if phase_key is not None:
        sql += " AND f.phase_key = ?"
        params.append(phase_key)
    with get_connection() as conn:
        return conn.execute(sql + " ORDER BY f.id", params).fetchall()


def list_conclusions(project_id, phase_key=None):
    """Every conclusion of the project (rejected ones too), oldest first, with its research title."""
    sql = ("SELECT c.*, w.title AS card_title FROM conclusions c "
           "LEFT JOIN research_work_items w ON w.id = c.research_work_item_id WHERE c.project_id = ?")
    params = [project_id]
    if phase_key is not None:
        sql += " AND c.phase_key = ?"
        params.append(phase_key)
    with get_connection() as conn:
        return conn.execute(sql + " ORDER BY c.id", params).fetchall()


def conclusion_fact_links(project_id):
    """{conclusion_id: [(fact_id, fact_status), ...]} for every conclusion of the project."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT cf.conclusion_id, cf.fact_id, f.status FROM conclusion_facts cf "
            "JOIN conclusions c ON c.id = cf.conclusion_id JOIN facts f ON f.id = cf.fact_id "
            "WHERE c.project_id = ? ORDER BY cf.conclusion_id, cf.fact_id",
            (project_id,),
        ).fetchall()
    links = {}
    for row in rows:
        links.setdefault(row["conclusion_id"], []).append((row["fact_id"], row["status"]))
    return links


def search_facts(project_id, words, phase_key=None, limit=10):
    """Active facts containing every word (in the claim, the quote or the page title), newest first."""
    sql, params = _FACT_SELECT + "WHERE f.project_id = ? AND f.status = 'active'", [project_id]
    if phase_key is not None:
        sql += " AND f.phase_key = ?"
        params.append(phase_key)
    for word in words:
        sql += (" AND (f.claim LIKE ? ESCAPE '\\' OR COALESCE(f.quote, '') LIKE ? ESCAPE '\\'"
                " OR COALESCE(s.title, '') LIKE ? ESCAPE '\\')")
        params += [_like(word)] * 3
    sql += " ORDER BY f.id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        return conn.execute(sql, params).fetchall()


def known_facts(project_id, per_phase=10):
    """Per phase: how many active facts there are, and the newest claims."""
    with get_connection() as conn:
        counts = conn.execute(
            "SELECT phase_key, COUNT(*) AS n FROM facts WHERE project_id = ? AND status = 'active' "
            "GROUP BY phase_key ORDER BY phase_key",
            (project_id,),
        ).fetchall()
        known = {}
        for row in counts:
            recent = conn.execute(
                "SELECT claim FROM facts WHERE project_id = ? AND phase_key = ? AND status = 'active' "
                "ORDER BY id DESC LIMIT ?",
                (project_id, row["phase_key"], per_phase),
            ).fetchall()
            known[row["phase_key"]] = {"count": row["n"], "recent": [r["claim"] for r in recent]}
    return known
