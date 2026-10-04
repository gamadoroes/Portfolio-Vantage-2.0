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
