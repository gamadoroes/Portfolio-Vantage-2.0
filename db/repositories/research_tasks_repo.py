# db/repositories/research_tasks_repo.py
from datetime import datetime

from ..connection import get_connection


def get_or_create_task(project_id, phase_key, title):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM research_tasks WHERE project_id = ? AND phase_key = ?",
            (project_id, phase_key),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO research_tasks (project_id, phase_key, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, phase_key, title, now, now),
        )
        return cur.lastrowid


def create_ad_hoc_task(project_id, title):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO research_tasks (project_id, phase_key, title, created_at, updated_at) "
            "VALUES (?, NULL, ?, ?, ?)",
            (project_id, title, now, now),
        )
        return cur.lastrowid


def list_tasks_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_tasks WHERE project_id = ? ORDER BY phase_key", (project_id,)
        ).fetchall()


def next_version_number(project_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS max_v FROM project_insight_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return row["max_v"] + 1


def create_version(project_id, version, generated_at, competitors_json, competitor_landscape_markdown):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO project_insight_versions "
            "(project_id, version, generated_at, competitors_json, competitor_landscape_markdown, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, version, generated_at, competitors_json, competitor_landscape_markdown, now),
        )
        return cur.lastrowid


def list_versions_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM project_insight_versions WHERE project_id = ? ORDER BY version",
            (project_id,),
        ).fetchall()


def get_latest_version(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM project_insight_versions WHERE project_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()


def get_version_by_number(project_id, version):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM project_insight_versions WHERE project_id = ? AND version = ?",
            (project_id, version),
        ).fetchone()


def create_finding(research_task_id, insight_version_id, content, gaps_notes, confidence):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO findings "
            "(research_task_id, insight_version_id, content, gaps_notes, confidence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (research_task_id, insight_version_id, content, gaps_notes, confidence, now, now),
        )
        return cur.lastrowid


def list_findings_for_version(insight_version_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM findings WHERE insight_version_id = ?", (insight_version_id,)
        ).fetchall()


def get_finding_for_task_in_version(research_task_id, insight_version_id):
    """Scoped to one phase's finding within one version.

    NOTE: list_findings_for_version() alone is NOT enough to reconstruct a
    single phase — it returns all 7 phases' findings for that version. This
    function is what insights_service._build_phase_data actually needs.
    """
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM findings WHERE research_task_id = ? AND insight_version_id = ?",
            (research_task_id, insight_version_id),
        ).fetchone()


def create_evidence(project_id, raw_text, source_id=None, filename=None, stable_file_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO evidence (project_id, source_id, raw_text, filename, stable_file_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, source_id, raw_text, filename, stable_file_id, now),
        )
        return cur.lastrowid


def link_finding_evidence(finding_id, evidence_id):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO finding_evidence (finding_id, evidence_id) VALUES (?, ?)",
            (finding_id, evidence_id),
        )


def list_evidence_for_finding(finding_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT e.* FROM evidence e "
            "JOIN finding_evidence fe ON fe.evidence_id = e.id "
            "WHERE fe.finding_id = ?",
            (finding_id,),
        ).fetchall()


def unlink_evidence_by_source_in_version(insight_version_id, source_id):
    """Remove finding<->evidence links for a given source, scoped to one version only.

    Used when a file is deleted: only the current/latest version's links are
    pruned (matching today's behavior of only editing the live insights.json),
    historical versions keep their evidence untouched.
    """
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM finding_evidence WHERE evidence_id IN ("
            "  SELECT e.id FROM evidence e WHERE e.source_id = ?"
            ") AND finding_id IN ("
            "  SELECT id FROM findings WHERE insight_version_id = ?"
            ")",
            (source_id, insight_version_id),
        )
