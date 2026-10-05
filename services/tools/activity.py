# services/tools/activity.py
"""Activity-feed entries for actions the user takes through tools (same decision types as before)."""
import json

from db.repositories import agent_decisions_repo, research_work_items_repo


def record_user_action(ctx, decision_type, card_id=None, **detail):
    if card_id is not None:
        row = research_work_items_repo.get(card_id)
        if row is not None:
            detail.setdefault("title", row["title"])
    agent_decisions_repo.record(ctx.project_id, decision_type, json.dumps(detail), research_work_item_id=card_id)
