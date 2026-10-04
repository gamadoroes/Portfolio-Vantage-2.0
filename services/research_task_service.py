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


def add_dependency(work_item_id, depends_on_work_item_id):
    if work_item_id == depends_on_work_item_id:
        raise ValueError("A task cannot depend on itself")
    item = research_work_items_repo.get(work_item_id)
    dep = research_work_items_repo.get(depends_on_work_item_id)
    if item is None or dep is None:
        raise ValueError("Task not found")
    if item["project_id"] != dep["project_id"]:
        raise ValueError("Dependencies must be within the same project")
    if _creates_cycle(depends_on_work_item_id, work_item_id):
        raise ValueError("This dependency would create a cycle")
    research_work_items_repo.add_dependency(work_item_id, depends_on_work_item_id)


def _creates_cycle(start_id, target_id):
    """True if target_id is reachable from start_id by following existing
    depends_on edges -- i.e. adding (target_id depends_on start_id) would
    close a loop."""
    visited = set()
    stack = [start_id]
    while stack:
        current = stack.pop()
        if current == target_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        for row in research_work_items_repo.list_dependencies(current):
            stack.append(row["depends_on_work_item_id"])
    return False


def compute_phase_rollup(project_id, phase_key):
    items = research_work_items_repo.list_for_phase(project_id, phase_key)

    status_counts = {}
    for item in items:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1

    completed_scores = [
        item["completeness_score"] for item in items
        if item["status"] == "COMPLETE" and item["completeness_score"] is not None
    ]
    weakest_completeness = min(completed_scores) if completed_scores else None

    merged_gaps = []
    seen_gaps = set()
    for item in items:
        gaps = json.loads(item["identified_gaps_json"]) if item["identified_gaps_json"] else []
        for gap in gaps:
            if gap not in seen_gaps:
                seen_gaps.add(gap)
                merged_gaps.append(gap)

    return {
        "status_counts": status_counts,
        "weakest_completeness": weakest_completeness,
        "merged_gaps": merged_gaps,
    }
