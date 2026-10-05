import json

from db.repositories import research_work_items_repo

ALLOWED_TRANSITIONS = {
    "PROPOSED": {"READY", "SKIPPED"},
    # READY -> PROPOSED: the user un-approves, or edits an approved card.
    "READY": {"RUNNING", "SKIPPED", "PROPOSED"},
    "RUNNING": {"REVIEWING", "FAILED", "COMPLETE"},
    # REVIEWING -> FAILED: review outcome "failed" (the review claim moves RUNNING -> REVIEWING first).
    # REVIEWING -> RUNNING: system only, releasing a review claim after an error or a stale claim.
    "REVIEWING": {"COMPLETE", "FOLLOW_UP_REQUIRED", "WAITING_FOR_HUMAN", "FAILED", "RUNNING"},
    "WAITING_FOR_HUMAN": {"REVIEWING", "COMPLETE", "FAILED"},
    "FOLLOW_UP_REQUIRED": {"READY"},
    # FAILED -> PROPOSED: the user moves a failed card back to draft to edit it.
    "FAILED": {"READY", "SKIPPED", "PROPOSED"},
    "COMPLETE": set(),
    # SKIPPED -> PROPOSED: the user restores a skipped card.
    "SKIPPED": {"PROPOSED"},
}

PHASE7_PREREQUISITE_PHASES = ("1", "2", "3", "4", "5", "6")

VALID_PRIORITIES = {"low", "medium", "high"}


def create_task(
    project_id, phase_key, title, objective=None, priority=None, research_method=None,
    entities=None, expected_output=None, source_requirements=None,
    human_review_required=False, max_retries=3, prompt_text=None, framework_key=None,
    rationale=None, suggested_from_work_item_id=None,
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
        prompt_text=prompt_text,
        framework_key=framework_key,
        rationale=rationale,
        suggested_from_work_item_id=suggested_from_work_item_id,
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


def claim_transition(work_item_id, from_status, to_status):
    """Atomically move a card from from_status to to_status; False if it was not in from_status.

    Used where two requests could race for the same card: READY -> RUNNING (Run),
    RUNNING -> REVIEWING (review claim) and REVIEWING -> RUNNING (releasing a claim).
    These edges carry no guards, so skipping transition_task's checks is safe.
    """
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Cannot transition from {from_status} to {to_status}")
    return research_work_items_repo.claim_status(work_item_id, from_status, to_status)


def _assert_dependencies_satisfied(work_item_id):
    dep_ids = [r["depends_on_work_item_id"] for r in research_work_items_repo.list_dependencies(work_item_id)]
    for dep_id in dep_ids:
        dep = research_work_items_repo.get(dep_id)
        if dep is None or dep["status"] not in ("COMPLETE", "SKIPPED"):
            raise ValueError(f"Cannot move to READY: dependency {dep_id} is not complete")


def dependencies_satisfied(work_item_id):
    """True when every research this one waits for is COMPLETE or SKIPPED."""
    try:
        _assert_dependencies_satisfied(work_item_id)
    except ValueError:
        return False
    return True


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


def set_review_scores(task_id, completeness_score=None, evidence_score=None, identified_gaps=None):
    fields = {}
    if completeness_score is not None:
        fields["completeness_score"] = completeness_score
    if evidence_score is not None:
        fields["evidence_score"] = evidence_score
    if identified_gaps is not None:
        fields["identified_gaps_json"] = json.dumps(identified_gaps)
    if fields:
        research_work_items_repo.update_fields(task_id, **fields)


def flag_for_human_review(task_id):
    research_work_items_repo.update_fields(task_id, human_review_required=1)


def phase7_readiness(project_id):
    ready = [
        phase_key for phase_key in PHASE7_PREREQUISITE_PHASES
        if any(item["status"] == "COMPLETE" for item in research_work_items_repo.list_for_phase(project_id, phase_key))
    ]
    return {
        "ready_phases": ready,
        "ready_count": len(ready),
        "unlocked": len(ready) == len(PHASE7_PREREQUISITE_PHASES),
    }
