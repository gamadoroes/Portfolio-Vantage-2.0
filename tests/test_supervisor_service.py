import pytest

from db.repositories import projects_repo, research_work_items_repo
from services import insights_service, project_service, research_task_service, supervisor_service


def test_build_context_includes_objective(temp_db):
    projects_repo.get_or_create_id("P")
    project_service.save_project_prompt("P", "project_prompt", "Assess the online MBA market.")
    context = supervisor_service.build_context("P")
    assert "Assess the online MBA market." in context


def test_build_context_includes_phase_definitions(temp_db):
    projects_repo.get_or_create_id("P")
    context = supervisor_service.build_context("P")
    assert "The Landscape" in context  # phase "1"
    assert "Options for OES" in context  # phase "7"


def test_build_context_includes_existing_phase_summary(temp_db):
    projects_repo.get_or_create_id("P")
    insights_service.save_insights("P", {
        "generated_at": "2026-01-01T00:00:00", "competitors": [],
        "competitor_landscape_markdown": "",
        "phases": {str(i): {"title": f"Phase {i}", "summary": "Established fact" if i == 1 else "MISSING",
                             "confidence": "high" if i == 1 else "none", "evidence_sources": [], "gaps": [],
                             "suggested_topics": [], "linked_files": [], "linked_file_ids": []}
                   for i in range(1, 8)},
    })
    context = supervisor_service.build_context("P")
    assert "Established fact" in context


def test_build_context_includes_work_items_and_dependencies(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "Product / La Trobe", priority="high")
    b = research_work_items_repo.create(pid, "4", "Product / Torrens")
    research_work_items_repo.add_dependency(b, a)

    context = supervisor_service.build_context("P")
    assert "Product / La Trobe" in context
    assert "Product / Torrens" in context
    assert str(a) in context  # the dependency should be visible by id


def test_build_context_includes_recent_decisions(temp_db):
    from db.repositories import agent_decisions_repo
    pid = projects_repo.get_or_create_id("P")
    agent_decisions_repo.record(pid, "NO_ACTION", '{"reason": "nothing ready yet"}')

    context = supervisor_service.build_context("P")
    assert "NO_ACTION" in context
    assert "nothing ready yet" in context


def test_build_context_does_not_leak_other_projects(temp_db):
    pid1 = projects_repo.get_or_create_id("P1")
    pid2 = projects_repo.get_or_create_id("P2")
    research_work_items_repo.create(pid1, "4", "P1-only task")
    research_work_items_repo.create(pid2, "4", "P2-only task")
    from db.repositories import agent_decisions_repo
    agent_decisions_repo.record(pid1, "PROPOSE_TASKS", '{"note": "p1 decision"}')
    agent_decisions_repo.record(pid2, "PROPOSE_TASKS", '{"note": "p2 decision"}')

    context1 = supervisor_service.build_context("P1")
    context2 = supervisor_service.build_context("P2")

    assert "P1-only task" in context1
    assert "P2-only task" not in context1
    assert "p1 decision" in context1
    assert "p2 decision" not in context1

    assert "P2-only task" in context2
    assert "P1-only task" not in context2


def test_tool_schemas_has_nine_tools_with_correct_names():
    names = {schema["name"] for schema in supervisor_service.TOOL_SCHEMAS}
    assert names == {
        "propose_tasks", "mark_ready", "dispatch_task", "review_outcome",
        "create_followup_task", "request_human_review", "skip_task",
        "trigger_synthesis", "no_action",
    }


def test_handle_mark_ready_transitions_to_ready(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    result = supervisor_service.handle_mark_ready("P", {"task_id": task_id, "reason": "deps satisfied"})
    assert result["new_status"] == "READY"
    assert research_work_items_repo.get(task_id)["status"] == "READY"


def test_handle_mark_ready_raises_on_illegal_transition(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="COMPLETE")
    with pytest.raises(ValueError):
        supervisor_service.handle_mark_ready("P", {"task_id": task_id, "reason": "x"})


def test_handle_skip_task_transitions_to_skipped(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    result = supervisor_service.handle_skip_task("P", {"task_id": task_id, "reason": "no longer useful"})
    assert result["new_status"] == "SKIPPED"
    assert research_work_items_repo.get(task_id)["status"] == "SKIPPED"


def test_handle_no_action_is_a_noop(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    result = supervisor_service.handle_no_action("P", {"reason": "nothing ready"})
    assert "message" in result
    assert research_work_items_repo.get(task_id)["status"] == "PROPOSED"  # untouched


def test_handle_request_human_review_sets_flag_only_when_not_reviewing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")  # status PROPOSED
    result = supervisor_service.handle_request_human_review("P", {"task_id": task_id, "reason": "uncertain"})
    row = research_work_items_repo.get(task_id)
    assert row["human_review_required"] == 1
    assert row["status"] == "PROPOSED"  # unchanged, since it wasn't REVIEWING
    assert result["flagged_only"] is True


def test_handle_request_human_review_transitions_when_reviewing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="REVIEWING")
    result = supervisor_service.handle_request_human_review("P", {"task_id": task_id, "reason": "uncertain"})
    row = research_work_items_repo.get(task_id)
    assert row["human_review_required"] == 1
    assert row["status"] == "WAITING_FOR_HUMAN"
    assert result["new_status"] == "WAITING_FOR_HUMAN"


def test_handle_request_human_review_raises_for_unknown_task(temp_db):
    with pytest.raises(ValueError):
        supervisor_service.handle_request_human_review("P", {"task_id": 9999, "reason": "x"})


def test_handle_propose_tasks_creates_tasks(temp_db):
    projects_repo.get_or_create_id("P")
    result = supervisor_service.handle_propose_tasks("P", {
        "tasks": [{"phase_key": "4", "title": "Product / La Trobe"}],
        "reason": "initial plan",
    })
    assert len(result["created_task_ids"]) == 1
    row = research_work_items_repo.get(result["created_task_ids"][0])
    assert row["title"] == "Product / La Trobe"
    assert row["status"] == "PROPOSED"


def test_handle_propose_tasks_wires_existing_id_dependency(temp_db):
    pid = projects_repo.get_or_create_id("P")
    existing_id = research_work_items_repo.create(pid, "1", "Landscape task")
    result = supervisor_service.handle_propose_tasks("P", {
        "tasks": [{"phase_key": "4", "title": "Dependent task", "depends_on_existing_ids": [existing_id]}],
        "reason": "needs landscape first",
    })
    new_id = result["created_task_ids"][0]
    deps = [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(new_id)]
    assert deps == [existing_id]


def test_handle_propose_tasks_wires_batch_index_dependency_to_real_id(temp_db):
    projects_repo.get_or_create_id("P")
    result = supervisor_service.handle_propose_tasks("P", {
        "tasks": [
            {"phase_key": "4", "title": "Depends on second task", "depends_on_batch_indices": [1]},
            {"phase_key": "4", "title": "The second task"},
        ],
        "reason": "ordering matters",
    })
    first_id, second_id = result["created_task_ids"]
    deps = [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(first_id)]
    # Must be the SECOND task's real database id -- not the literal index "1".
    assert deps == [second_id]


def test_handle_propose_tasks_cycle_raises_value_error(temp_db):
    # Two tasks in the SAME batch mutually depending on each other via
    # batch indices is a genuine cycle: the handler creates both tasks
    # first, then wires dependencies in array order. Wiring task 0 -> task 1
    # succeeds (no edges exist yet); wiring task 1 -> task 0 immediately
    # after closes a real two-node cycle, since task 0 already depends on
    # task 1 at that point.
    projects_repo.get_or_create_id("P")
    with pytest.raises(ValueError, match="cycle"):
        supervisor_service.handle_propose_tasks("P", {
            "tasks": [
                {"phase_key": "4", "title": "X", "depends_on_batch_indices": [1]},
                {"phase_key": "4", "title": "Y", "depends_on_batch_indices": [0]},
            ],
            "reason": "mutually dependent tasks",
        })


def test_handle_create_followup_task_marks_original_and_creates_dependency(temp_db):
    pid = projects_repo.get_or_create_id("P")
    original_id = research_work_items_repo.create(pid, "4", "Product / Torrens")
    research_work_items_repo.update_fields(original_id, status="REVIEWING")

    result = supervisor_service.handle_create_followup_task("P", {
        "task_id": original_id,
        "followup_title": "Verify Torrens tuition fee from official source",
        "followup_objective": "Find an authoritative source for the current tuition fee.",
        "research_method": "TARGETED_WEB",
        "priority": "high",
        "reason": "Current fee was not from an authoritative source.",
    })

    original_row = research_work_items_repo.get(original_id)
    assert original_row["status"] == "FOLLOW_UP_REQUIRED"

    followup_id = result["followup_task_id"]
    followup_row = research_work_items_repo.get(followup_id)
    assert followup_row["title"] == "Verify Torrens tuition fee from official source"
    assert followup_row["phase_key"] == "4"

    deps = [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(original_id)]
    assert deps == [followup_id]


def test_handle_create_followup_task_raises_for_unknown_task(temp_db):
    with pytest.raises(ValueError):
        supervisor_service.handle_create_followup_task("P", {
            "task_id": 9999, "followup_title": "x", "reason": "x",
        })
