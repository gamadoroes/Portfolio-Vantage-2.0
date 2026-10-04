import json

from db.repositories import research_work_items_repo

ALLOWED_TRANSITIONS = {
    "PROPOSED": {"READY", "SKIPPED"},
    "READY": {"RUNNING", "SKIPPED"},
    "RUNNING": {"REVIEWING", "FAILED", "COMPLETE"},
    "REVIEWING": {"COMPLETE", "FOLLOW_UP_REQUIRED", "WAITING_FOR_HUMAN"},
    "WAITING_FOR_HUMAN": {"REVIEWING", "COMPLETE", "FAILED"},
    "FOLLOW_UP_REQUIRED": {"READY"},
    "FAILED": {"READY", "SKIPPED"},
    "COMPLETE": set(),
    "SKIPPED": set(),
}

VALID_PRIORITIES = {"low", "medium", "high"}


def create_task(
    project_id, phase_key, title, objective=None, priority=None, research_method=None,
    entities=None, expected_output=None, source_requirements=None,
    human_review_required=False, max_retries=3,
):
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority!r}. Must be one of {sorted(VALID_PRIORITIES)}")
    return research_work_items_repo.create(
        project_id, phase_key, title,
        objective=objective,
        priority=priority,
        research_method=research_method,
        entities_json=json.dumps(entities) if entities else None,
        expected_output=expected_output,
        source_requirements_json=json.dumps(source_requirements) if source_requirements else None,
        human_review_required=1 if human_review_required else 0,
        max_retries=max_retries,
    )


def transition_task(work_item_id, new_status):
    item = research_work_items_repo.get(work_item_id)
    if item is None:
        raise ValueError(f"No such research task: {work_item_id}")

    current = item["status"]
    if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"Cannot transition from {current} to {new_status}")

    if new_status == "READY":
        _assert_dependencies_satisfied(work_item_id)
        if current == "FAILED" and item["retry_count"] >= item["max_retries"]:
            raise ValueError("Retry limit reached -- transition to SKIPPED instead")

    if new_status == "COMPLETE" and current in ("RUNNING", "REVIEWING") and item["human_review_required"]:
        raise ValueError("This task requires human review before it can be marked COMPLETE")

    if new_status == "WAITING_FOR_HUMAN" and not item["human_review_required"]:
        raise ValueError("This task does not require human review")

    fields = {"status": new_status}
    if new_status == "FAILED":
        fields["retry_count"] = item["retry_count"] + 1
    research_work_items_repo.update_fields(work_item_id, **fields)


def _assert_dependencies_satisfied(work_item_id):
    dep_ids = [r["depends_on_work_item_id"] for r in research_work_items_repo.list_dependencies(work_item_id)]
    for dep_id in dep_ids:
        dep = research_work_items_repo.get(dep_id)
        if dep is None or dep["status"] not in ("COMPLETE", "SKIPPED"):
            raise ValueError(f"Cannot move to READY: dependency {dep_id} is not complete")
