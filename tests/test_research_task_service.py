import pytest

from db.repositories import projects_repo, research_work_items_repo
from services import research_task_service


def _make_task(pid, status="PROPOSED", **kwargs):
    task_id = research_work_items_repo.create(pid, "4", "Task", **kwargs)
    if status != "PROPOSED":
        research_work_items_repo.update_fields(task_id, status=status)
    return task_id


def test_create_task_defaults_to_proposed(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_task_service.create_task(pid, "4", "Product / La Trobe")
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "PROPOSED"


def test_create_task_serializes_list_fields_to_json(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_task_service.create_task(
        pid, "4", "Product / La Trobe", entities=["La Trobe"], source_requirements=["public website"]
    )
    row = research_work_items_repo.get(task_id)
    assert row["entities_json"] == '["La Trobe"]'
    assert row["source_requirements_json"] == '["public website"]'


def test_create_task_rejects_invalid_priority(temp_db):
    pid = projects_repo.get_or_create_id("P")
    with pytest.raises(ValueError):
        research_task_service.create_task(pid, "4", "Task", priority="urgent")


@pytest.mark.parametrize("from_status,to_status", [
    ("PROPOSED", "READY"),
    ("PROPOSED", "SKIPPED"),
    ("READY", "RUNNING"),
    ("READY", "SKIPPED"),
    ("RUNNING", "REVIEWING"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "COMPLETE"),
    ("REVIEWING", "COMPLETE"),
    ("REVIEWING", "FOLLOW_UP_REQUIRED"),
    ("WAITING_FOR_HUMAN", "REVIEWING"),
    ("WAITING_FOR_HUMAN", "COMPLETE"),
    ("WAITING_FOR_HUMAN", "FAILED"),
    ("FOLLOW_UP_REQUIRED", "READY"),
    ("FAILED", "READY"),
    ("FAILED", "SKIPPED"),
])
def test_legal_transitions_succeed(temp_db, from_status, to_status):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status=from_status)
    research_task_service.transition_task(task_id, to_status)
    assert research_work_items_repo.get(task_id)["status"] == to_status


@pytest.mark.parametrize("from_status,to_status", [
    ("PROPOSED", "RUNNING"),
    ("PROPOSED", "COMPLETE"),
    ("READY", "REVIEWING"),
    ("RUNNING", "READY"),
    ("COMPLETE", "READY"),
    ("COMPLETE", "RUNNING"),
    ("SKIPPED", "READY"),
    ("SKIPPED", "RUNNING"),
])
def test_illegal_transitions_rejected(temp_db, from_status, to_status):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status=from_status)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, to_status)
    assert research_work_items_repo.get(task_id)["status"] == from_status


def test_running_to_complete_blocked_when_human_review_required(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="RUNNING", human_review_required=1)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "COMPLETE")


def test_reviewing_to_complete_blocked_when_human_review_required(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="REVIEWING", human_review_required=1)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "COMPLETE")


def test_reviewing_to_waiting_for_human_requires_flag(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="REVIEWING", human_review_required=0)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "WAITING_FOR_HUMAN")


def test_reviewing_to_waiting_for_human_succeeds_when_flag_set(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="REVIEWING", human_review_required=1)
    research_task_service.transition_task(task_id, "WAITING_FOR_HUMAN")
    assert research_work_items_repo.get(task_id)["status"] == "WAITING_FOR_HUMAN"


def test_running_to_failed_increments_retry_count(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="RUNNING")
    research_task_service.transition_task(task_id, "FAILED")
    assert research_work_items_repo.get(task_id)["retry_count"] == 1


def test_failed_to_ready_blocked_once_retries_exhausted(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="FAILED", max_retries=1)
    research_work_items_repo.update_fields(task_id, retry_count=1)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "READY")


def test_failed_to_ready_allowed_when_retries_remain(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="FAILED", max_retries=3)
    research_work_items_repo.update_fields(task_id, retry_count=1)
    research_task_service.transition_task(task_id, "READY")
    assert research_work_items_repo.get(task_id)["status"] == "READY"


def test_proposed_to_ready_blocked_by_incomplete_dependency(temp_db):
    pid = projects_repo.get_or_create_id("P")
    dep_id = _make_task(pid, status="RUNNING")
    task_id = research_work_items_repo.create(pid, "4", "Dependent task")
    research_work_items_repo.add_dependency(task_id, dep_id)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "READY")


def test_proposed_to_ready_allowed_once_dependency_complete(temp_db):
    pid = projects_repo.get_or_create_id("P")
    dep_id = _make_task(pid, status="COMPLETE")
    task_id = research_work_items_repo.create(pid, "4", "Dependent task")
    research_work_items_repo.add_dependency(task_id, dep_id)
    research_task_service.transition_task(task_id, "READY")
    assert research_work_items_repo.get(task_id)["status"] == "READY"


def test_transition_unknown_task_raises(temp_db):
    with pytest.raises(ValueError):
        research_task_service.transition_task(9999, "READY")


def test_add_dependency_rejects_self_reference(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "A")
    with pytest.raises(ValueError):
        research_task_service.add_dependency(task_id, task_id)


def test_add_dependency_rejects_cross_project(temp_db):
    pid1 = projects_repo.get_or_create_id("P1")
    pid2 = projects_repo.get_or_create_id("P2")
    a = research_work_items_repo.create(pid1, "4", "A")
    b = research_work_items_repo.create(pid2, "4", "B")
    with pytest.raises(ValueError):
        research_task_service.add_dependency(a, b)


def test_add_dependency_rejects_direct_cycle(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")
    research_task_service.add_dependency(a, b)  # A depends on B
    with pytest.raises(ValueError):
        research_task_service.add_dependency(b, a)  # B depends on A -- direct cycle


def test_add_dependency_rejects_transitive_cycle(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")
    c = research_work_items_repo.create(pid, "4", "C")
    research_task_service.add_dependency(a, b)  # A depends on B
    research_task_service.add_dependency(b, c)  # B depends on C
    with pytest.raises(ValueError):
        research_task_service.add_dependency(c, a)  # C depends on A -- closes the loop


def test_add_dependency_allows_diamond_shape(temp_db):
    # A depends on B and C; both B and C depend on D. Not a cycle.
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")
    c = research_work_items_repo.create(pid, "4", "C")
    d = research_work_items_repo.create(pid, "4", "D")
    research_task_service.add_dependency(a, b)
    research_task_service.add_dependency(a, c)
    research_task_service.add_dependency(b, d)
    research_task_service.add_dependency(c, d)  # must not raise
    assert len(research_work_items_repo.list_dependencies(a)) == 2


def test_compute_phase_rollup_empty_phase(temp_db):
    pid = projects_repo.get_or_create_id("P")
    rollup = research_task_service.compute_phase_rollup(pid, "4")
    assert rollup == {"status_counts": {}, "weakest_completeness": None, "merged_gaps": []}


def test_compute_phase_rollup_counts_and_scores(temp_db):
    pid = projects_repo.get_or_create_id("P")
    t1 = research_work_items_repo.create(pid, "4", "A")
    research_work_items_repo.update_fields(t1, status="COMPLETE", completeness_score=0.9,
                                            identified_gaps_json='["Missing pricing"]')
    t2 = research_work_items_repo.create(pid, "4", "B")
    research_work_items_repo.update_fields(t2, status="COMPLETE", completeness_score=0.4,
                                            identified_gaps_json='["Missing pricing", "No faculty data"]')
    t3 = research_work_items_repo.create(pid, "4", "C")
    research_work_items_repo.update_fields(t3, status="RUNNING")

    rollup = research_task_service.compute_phase_rollup(pid, "4")
    assert rollup["status_counts"] == {"COMPLETE": 2, "RUNNING": 1}
    assert rollup["weakest_completeness"] == 0.4
    assert rollup["merged_gaps"] == ["Missing pricing", "No faculty data"]


def test_compute_phase_rollup_ignores_other_phases(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_work_items_repo.create(pid, "1", "Other phase task")
    rollup = research_task_service.compute_phase_rollup(pid, "4")
    assert rollup["status_counts"] == {}


def test_set_review_scores_updates_only_provided_fields(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_task_service.set_review_scores(task_id, completeness_score=0.7)
    row = research_work_items_repo.get(task_id)
    assert row["completeness_score"] == 0.7
    assert row["evidence_score"] is None


def test_set_review_scores_serializes_gaps_to_json(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_task_service.set_review_scores(task_id, identified_gaps=["Missing pricing"])
    row = research_work_items_repo.get(task_id)
    assert row["identified_gaps_json"] == '["Missing pricing"]'


def test_flag_for_human_review_sets_flag(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_task_service.flag_for_human_review(task_id)
    row = research_work_items_repo.get(task_id)
    assert row["human_review_required"] == 1
