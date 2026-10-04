import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo
from services import insights_service, project_service, supervisor_service


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


def test_build_context_includes_run_status_and_output_for_work_item(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task with a run")
    research_runs_repo.create("run_1", pid, None, None, "preview")
    research_runs_repo.update(
        "run_1", research_work_item_id=task_id, status="completed", output_text="some finding"
    )

    context = supervisor_service.build_context("P")
    assert "run_status=completed" in context
    assert "some finding" in context


def _task_with_run(pid, title, run_id, task_status, run_status, output_text):
    task_id = research_work_items_repo.create(pid, "4", title)
    research_work_items_repo.update_fields(task_id, status=task_status)
    research_runs_repo.create(run_id, pid, None, None, "preview")
    research_runs_repo.update(
        run_id, research_work_item_id=task_id, status=run_status, output_text=output_text
    )
    return task_id


def test_build_context_omits_run_fields_when_task_has_no_run(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_work_items_repo.create(pid, "4", "Task without a run")

    context = supervisor_service.build_context("P")
    assert "run_status=" not in context
    assert "run_output" not in context


def test_build_context_omits_run_output_when_run_has_no_output(temp_db):
    pid = projects_repo.get_or_create_id("P")
    _task_with_run(pid, "Empty run", "run_1", "RUNNING", "completed", None)

    context = supervisor_service.build_context("P")
    assert "run_status=completed" in context
    assert "run_output" not in context


def test_build_context_shows_full_output_for_task_awaiting_review(temp_db):
    pid = projects_repo.get_or_create_id("P")
    output = "A" * 4000 + "FINDING_MARKER" + "B" * 500
    task_id = _task_with_run(pid, "Awaiting review", "run_1", "RUNNING", "completed", output)

    context = supervisor_service.build_context("P")
    assert f"<run_output task_id={task_id}>" in context
    assert "FINDING_MARKER" in context
    assert "...[truncated]" not in context


def test_build_context_shows_full_output_for_task_whose_run_failed(temp_db):
    pid = projects_repo.get_or_create_id("P")
    output = "A" * 4000 + "FAILURE_MARKER"
    _task_with_run(pid, "Failed run", "run_1", "RUNNING", "failed", output)

    context = supervisor_service.build_context("P")
    assert "FAILURE_MARKER" in context


def test_build_context_shows_only_short_preview_for_already_reviewed_task(temp_db):
    pid = projects_repo.get_or_create_id("P")
    output = "A" * 4000 + "FINDING_MARKER"
    _task_with_run(pid, "Reviewed", "run_1", "COMPLETE", "completed", output)

    context = supervisor_service.build_context("P")
    assert "FINDING_MARKER" not in context
    assert "...[truncated]" in context
    assert "<run_output" not in context


def test_build_context_shows_only_short_preview_while_run_still_running(temp_db):
    pid = projects_repo.get_or_create_id("P")
    output = "A" * 4000 + "PARTIAL_MARKER"
    _task_with_run(pid, "In flight", "run_1", "RUNNING", "running", output)

    context = supervisor_service.build_context("P")
    assert "PARTIAL_MARKER" not in context
    assert "<run_output" not in context


def test_build_context_flattens_short_preview_onto_the_task_line(temp_db):
    pid = projects_repo.get_or_create_id("P")
    output = 'line one\n- a "quoted" bullet\n# Heading'
    _task_with_run(pid, "Markdown output", "run_1", "COMPLETE", "completed", output)

    context = supervisor_service.build_context("P")
    assert "run_output=\"line one - a 'quoted' bullet # Heading\"" in context


def test_build_context_caps_total_output_shown_for_tasks_awaiting_review(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_ids = [
        _task_with_run(pid, f"Awaiting {i}", f"run_{i}", "RUNNING", "completed", "x" * 20000)
        for i in range(5)
    ]

    context = supervisor_service.build_context("P")
    expected_full = (
        supervisor_service.MAX_REVIEW_OUTPUT_TOTAL_CHARS // supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS
    )
    assert context.count("<run_output task_id=") == expected_full
    assert "context truncated" not in context
    # Tasks past the budget must still be listed, not dropped.
    for task_id in task_ids:
        assert f"- id={task_id} " in context


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


def test_handle_propose_tasks_out_of_range_batch_index_raises_value_error(temp_db):
    projects_repo.get_or_create_id("P")
    with pytest.raises(ValueError):
        supervisor_service.handle_propose_tasks("P", {
            "tasks": [{"phase_key": "4", "title": "X", "depends_on_batch_indices": [5]}],
            "reason": "bad index",
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


def test_handle_dispatch_task_file_analysis_completes_synchronously(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from services import project_service
    project_service.create_project("P")
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task", objective="Find the tuition fee.")
    research_work_items_repo.update_fields(task_id, status="READY")

    monkeypatch.setattr(
        supervisor_service.llm_service, "prompt_completion",
        lambda system_prompt, user_message, max_tokens=3000: "The fee is $40,000.",
    )

    result = supervisor_service.handle_dispatch_task("P", {
        "task_id": task_id, "research_method": "FILE_ANALYSIS", "reason": "files are available",
    })

    assert result["status"] == "completed"
    task_row = research_work_items_repo.get(task_id)
    assert task_row["status"] == "RUNNING"  # dispatch never reviews -- that's a separate call
    run = research_runs_repo.find_latest_for_work_item(task_id)
    assert run["status"] == "completed"
    assert run["output_text"] == "The fee is $40,000."


def test_handle_dispatch_task_targeted_web_starts_async_job(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from services import project_service
    project_service.create_project("P")
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task", objective="Find the tuition fee.")
    research_work_items_repo.update_fields(task_id, status="READY")

    class FakeResponse:
        id = "resp_123"
        status = "queued"

    monkeypatch.setattr(
        supervisor_service.openai_service, "start_deep_research",
        lambda prompt: FakeResponse(),
    )

    result = supervisor_service.handle_dispatch_task("P", {
        "task_id": task_id, "research_method": "TARGETED_WEB", "reason": "needs live web data",
    })

    assert result["status"] == "running"
    task_row = research_work_items_repo.get(task_id)
    assert task_row["status"] == "RUNNING"
    run = research_runs_repo.find_latest_for_work_item(task_id)
    assert run["status"] == "running"
    assert run["response_id"] == "resp_123"


def test_handle_dispatch_task_raises_for_unknown_task(temp_db):
    with pytest.raises(ValueError):
        supervisor_service.handle_dispatch_task("P", {
            "task_id": 9999, "research_method": "FILE_ANALYSIS", "reason": "x",
        })


def _dispatched_task_with_run(pid, status="completed"):
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="RUNNING")
    run_id = f"run_{task_id}"
    research_runs_repo.create(run_id, pid, None, None, "preview")
    research_runs_repo.update(run_id, research_work_item_id=task_id, status=status)
    return task_id


def test_handle_review_outcome_raises_when_no_finished_run(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _dispatched_task_with_run(pid, status="running")
    with pytest.raises(ValueError):
        supervisor_service.handle_review_outcome("P", {
            "task_id": task_id, "outcome": "COMPLETE", "reason": "x",
        })


def test_handle_review_outcome_raises_when_no_run_at_all(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="RUNNING")
    with pytest.raises(ValueError):
        supervisor_service.handle_review_outcome("P", {
            "task_id": task_id, "outcome": "COMPLETE", "reason": "x",
        })


def test_handle_review_outcome_complete_goes_through_reviewing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _dispatched_task_with_run(pid, status="completed")
    result = supervisor_service.handle_review_outcome("P", {
        "task_id": task_id, "outcome": "COMPLETE", "completeness_score": 0.9,
        "evidence_score": 0.8, "identified_gaps": [], "reason": "fully answered",
    })
    assert result["outcome"] == "COMPLETE"
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "COMPLETE"
    assert row["completeness_score"] == 0.9


def test_handle_review_outcome_follow_up_required(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _dispatched_task_with_run(pid, status="completed")
    result = supervisor_service.handle_review_outcome("P", {
        "task_id": task_id, "outcome": "FOLLOW_UP_REQUIRED",
        "identified_gaps": ["Missing authoritative source"], "reason": "weak evidence",
    })
    assert result["outcome"] == "FOLLOW_UP_REQUIRED"
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "FOLLOW_UP_REQUIRED"
    assert row["identified_gaps_json"] == '["Missing authoritative source"]'


def test_handle_review_outcome_failed_skips_reviewing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _dispatched_task_with_run(pid, status="failed")
    result = supervisor_service.handle_review_outcome("P", {
        "task_id": task_id, "outcome": "FAILED", "reason": "run errored out",
    })
    assert result["outcome"] == "FAILED"
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "FAILED"
    # No exception was raised getting here -- confirms RUNNING->FAILED was taken
    # directly, since RUNNING->REVIEWING->FAILED is illegal per Phase 2's table
    # (REVIEWING has no FAILED edge) and would have raised ValueError instead.


def _complete_task_for_phase(pid, phase_key):
    task_id = research_work_items_repo.create(pid, phase_key, f"Task for phase {phase_key}")
    research_work_items_repo.update_fields(task_id, status="COMPLETE")
    return task_id


def test_handle_trigger_synthesis_rejects_when_one_phase_missing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    # Phases 1-5 have a COMPLETE task; phase 6 does not.
    for phase_key in ["1", "2", "3", "4", "5"]:
        _complete_task_for_phase(pid, phase_key)

    with pytest.raises(ValueError, match="6"):
        supervisor_service.handle_trigger_synthesis("P", {"reason": "think we're done"})


def test_handle_trigger_synthesis_succeeds_when_all_six_phases_complete(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from services import project_service
    project_service.create_project("P")
    pid = projects_repo.get_or_create_id("P")
    for phase_key in ["1", "2", "3", "4", "5", "6"]:
        _complete_task_for_phase(pid, phase_key)

    monkeypatch.setattr(
        supervisor_service.llm_service, "prompt_completion",
        lambda system_prompt, user_message, max_tokens=4000: "## Strategic Options\n\nDo X, then Y.",
    )

    result = supervisor_service.handle_trigger_synthesis("P", {"reason": "all phases complete"})
    assert result["synthesis_generated"] is True

    from services import insights_service
    current = insights_service.load_current_insights("P")
    assert current["phases"]["7"]["summary"] == "## Strategic Options\n\nDo X, then Y."


def test_tool_handlers_keys_match_tool_schemas_names():
    # Guards against a typo'd TOOL_HANDLERS key (e.g. "propose_task" instead
    # of "propose_tasks") that would otherwise surface only as a KeyError at
    # runtime in run_supervisor_cycle, long after the handler itself was
    # written and tested in isolation.
    schema_names = {schema["name"] for schema in supervisor_service.TOOL_SCHEMAS}
    assert set(supervisor_service.TOOL_HANDLERS.keys()) == schema_names


class _FakeToolUseBlock:
    def __init__(self, name, input):
        self.type = "tool_use"
        self.name = name
        self.input = input


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeAnthropicResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


def _patch_anthropic(monkeypatch, tool_name, tool_input, preceding_text=None):
    content = []
    if preceding_text:
        content.append(_FakeTextBlock(preceding_text))
    content.append(_FakeToolUseBlock(tool_name, tool_input))
    response = _FakeAnthropicResponse(content)
    monkeypatch.setattr(
        supervisor_service.anthropic, "Anthropic",
        lambda api_key: _FakeAnthropicClient(response),
    )


@pytest.fixture
def app_context():
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        yield


def test_run_supervisor_cycle_executes_and_records_decision(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    _patch_anthropic(monkeypatch, "mark_ready", {"task_id": task_id, "reason": "ready to go"})

    result = supervisor_service.run_supervisor_cycle("P")

    assert result["decision"]["action"] == "mark_ready"
    assert result["execution"]["success"] is True
    assert research_work_items_repo.get(task_id)["status"] == "READY"

    decisions = agent_decisions_repo.list_for_project(pid)
    assert len(decisions) == 1
    assert decisions[0]["decision_type"] == "mark_ready"
    assert decisions[0]["research_work_item_id"] == task_id


def test_run_supervisor_cycle_records_failed_decision_without_crashing(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="COMPLETE")
    _patch_anthropic(monkeypatch, "mark_ready", {"task_id": task_id, "reason": "trying anyway"})

    result = supervisor_service.run_supervisor_cycle("P")

    assert result["execution"]["success"] is False
    assert "error" in result["execution"]
    decisions = agent_decisions_repo.list_for_project(pid)
    assert len(decisions) == 1  # still recorded, even though execution failed


def test_run_supervisor_cycle_propagates_cycle_rejection_as_failed_decision(temp_db, app_context, monkeypatch):
    # Same mutual-dependency construction as Task 5's handler-level cycle
    # test, but driven end-to-end through run_supervisor_cycle: the (faked)
    # model proposes two tasks in one batch that depend on each other via
    # batch indices, which is a genuine cycle once both edges are wired.
    projects_repo.get_or_create_id("P")
    _patch_anthropic(monkeypatch, "propose_tasks", {
        "tasks": [
            {"phase_key": "4", "title": "X", "depends_on_batch_indices": [1]},
            {"phase_key": "4", "title": "Y", "depends_on_batch_indices": [0]},
        ],
        "reason": "mutually dependent tasks",
    })

    result = supervisor_service.run_supervisor_cycle("P")

    assert result["execution"]["success"] is False
    assert "cycle" in result["execution"]["error"].lower()
    decisions = agent_decisions_repo.list_for_project(projects_repo.get_or_create_id("P"))
    assert len(decisions) == 1
    assert decisions[0]["decision_type"] == "propose_tasks"


def test_run_supervisor_cycle_no_tool_use_block_raises(temp_db, app_context, monkeypatch):
    projects_repo.get_or_create_id("P")
    response = _FakeAnthropicResponse([_FakeTextBlock("I don't know what to do.")])
    monkeypatch.setattr(
        supervisor_service.anthropic, "Anthropic",
        lambda api_key: _FakeAnthropicClient(response),
    )
    with pytest.raises(RuntimeError):
        supervisor_service.run_supervisor_cycle("P")
