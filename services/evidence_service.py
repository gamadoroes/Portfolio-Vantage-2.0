# services/evidence_service.py
"""Facts and conclusions as the screens and tools show them. All SQL is in db/repositories/evidence_repo.py."""


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
