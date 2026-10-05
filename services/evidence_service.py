# services/evidence_service.py
"""Facts and conclusions as the screens and tools show them. All SQL is in db/repositories/evidence_repo.py."""

from db.repositories import evidence_repo, projects_repo


def fact_dict(row):
    """One fact from evidence_repo.list_facts / search_facts, as the Insights tab and the search tool show it."""
    source = None
    if row["source_url"]:
        source = {"url": row["source_url"], "title": row["source_title"] or "",
                  "publisher": row["source_publisher"] or "", "published_date": row["source_published_date"] or ""}
    return {
        "id": row["id"], "phase_key": row["phase_key"], "claim": row["claim"], "quote": row["quote"] or "",
        "as_of": row["as_of"] or "", "status": row["status"], "source": source,
        "card_id": row["research_work_item_id"], "card_title": row["card_title"] or "", "created_at": row["created_at"],
    }


def list_evidence(project_name, phase_key=None):
    """Every fact and conclusion of the project (rejected ones included and marked), grouped by phase."""
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    phases = {}

    def slot(key):
        return phases.setdefault(key, {"facts": [], "conclusions": []})

    for row in evidence_repo.list_facts(project_id, phase_key):
        slot(row["phase_key"])["facts"].append(fact_dict(row))
    links = evidence_repo.conclusion_fact_links(project_id)
    for row in evidence_repo.list_conclusions(project_id, phase_key):
        cited = links.get(row["id"], [])
        slot(row["phase_key"])["conclusions"].append({
            "id": row["id"], "phase_key": row["phase_key"], "text": row["text"], "status": row["status"],
            "fact_ids": [fact_id for fact_id, _ in cited],
            "rejected_fact_count": sum(1 for _, status in cited if status == "rejected"),
            "card_id": row["research_work_item_id"], "card_title": row["card_title"] or "",
        })
    return phases
