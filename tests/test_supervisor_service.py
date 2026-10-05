import json
from types import SimpleNamespace

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo
from services import insights_service, llm_service, project_service, research_task_service, supervisor_service


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


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


def test_build_context_shows_each_cards_method_focus_rationale_and_gaps_on_its_line(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_task_service.create_task(
        pid, "4", "Fees", research_method="TARGETED_WEB", entities=["Fees", "FEE-HELP"],
        rationale='Price drives "choice"\nfor career changers. ' + "x" * 500,
    )
    research_work_items_repo.update_fields(
        task_id, status="FOLLOW_UP_REQUIRED", completeness_score=0.6,
        identified_gaps_json=json.dumps(["No intake dates", "No fees for Torrens"]),
    )
    research_work_items_repo.create(pid, "2", "Bare card")

    lines = supervisor_service.build_context("P").splitlines()
    line = next(x for x in lines if x.startswith(f"- id={task_id} "))
    assert "status=FOLLOW_UP_REQUIRED" in line and "completeness=0.6" in line
    assert "method=TARGETED_WEB" in line
    assert 'focus="Fees, FEE-HELP"' in line
    assert "rationale=\"Price drives 'choice' for career changers." in line
    assert 'gaps="No intake dates; No fees for Torrens"' in line
    assert len(line) < 800  # the long rationale is clipped
    bare = next(x for x in lines if 'title="Bare card"' in x)
    assert "method=none" in bare and "rationale=" not in bare and "gaps=" not in bare


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



class _Block:
    def __init__(self, name, input):
        self.type = "tool_use"
        self.name = name
        self.input = input


class _FakeClient:
    def __init__(self, blocks=(), error=None):
        self.calls = []
        self._blocks = list(blocks)
        self._error = error
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(content=self._blocks)


@pytest.fixture
def app_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        yield flask_app


@pytest.fixture
def drafted(monkeypatch):
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: "ROLE: analyst. A complete drafted research prompt.")


def _install(monkeypatch, client):
    monkeypatch.setattr(supervisor_service.anthropic, "Anthropic", lambda api_key: client)
    return client


def _propose(*tasks, reason="Phases have no research yet"):
    return _Block("propose_tasks", {"tasks": list(tasks), "reason": reason})


def _task(phase="1", title="Landscape overview", method="TARGETED_WEB", **extra):
    return dict({"phase_key": phase, "title": title, "research_method": method,
                 "focus": ["Providers"], "rationale": "Phase has no research"}, **extra)


# ---- drafting ----

def test_drafting_offers_only_propose_and_no_action(temp_db, app_context, drafted, monkeypatch):
    projects_repo.get_or_create_id("P")
    client = _install(monkeypatch, _FakeClient([_Block("no_action", {"reason": "Nothing to add"})]))
    supervisor_service.draft_researches("P")
    call = client.calls[0]
    assert {t["name"] for t in call["tools"]} == {"propose_tasks", "no_action"}
    assert call["tool_choice"] == {"type": "any"}
    assert set(supervisor_service.DRAFTING_HANDLERS) == {"propose_tasks", "no_action"}


def test_the_supervisor_has_no_way_to_approve_or_start_research():
    for removed in ("handle_mark_ready", "handle_dispatch_task", "handle_skip_task",
                    "handle_trigger_synthesis", "run_supervisor_cycle", "TOOL_HANDLERS"):
        assert not hasattr(supervisor_service, removed)


def test_propose_creates_drafted_cards_awaiting_approval(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([_propose(
        _task(),
        _task(phase="3", title="Website review", framework_key="oes-marketing-website"),
    )]))
    result = supervisor_service.draft_researches("P")
    assert result["execution"]["success"] is True
    items = research_work_items_repo.list_for_project(pid)
    assert [i["status"] for i in items] == ["PROPOSED", "PROPOSED"]
    assert items[0]["framework_key"] == "oes-landscape"
    assert items[1]["framework_key"] == "oes-marketing-website"
    assert items[0]["prompt_text"].startswith("ROLE: analyst.")
    assert items[0]["rationale"] == "Phase has no research"
    decision = agent_decisions_repo.list_for_project(pid)[0]
    assert decision["decision_type"] == "propose_tasks"
    assert json.loads(decision["detail"])["execution"]["success"] is True


@pytest.mark.parametrize("bad_task", [
    _task(phase="7", title="Options"),
    _task(method="DEEP_MAGIC"),
    _task(title="   "),
    _task(depends_on_batch_indices=[5]),
])
def test_invalid_proposals_create_nothing_and_are_recorded(temp_db, app_context, drafted, monkeypatch, bad_task):
    pid = projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([_propose(_task(title="Fine one"), bad_task)]))
    result = supervisor_service.draft_researches("P")
    assert result["execution"]["success"] is False
    assert research_work_items_repo.list_for_project(pid) == []
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "propose_tasks"


@pytest.mark.parametrize("make_bad_id", [
    lambda pid: 99999,
    lambda pid: research_work_items_repo.create(projects_repo.get_or_create_id("Other"), "1", "Not ours"),
    lambda pid: "abc",
])
def test_bad_existing_dependencies_create_nothing_and_are_recorded(temp_db, app_context, drafted, monkeypatch, make_bad_id):
    pid = projects_repo.get_or_create_id("P")
    bad_id = make_bad_id(pid)
    _install(monkeypatch, _FakeClient([_propose(_task(title="Fine one"), _task(title="Bad dep", depends_on_existing_ids=[bad_id]))]))
    result = supervisor_service.draft_researches("P")
    assert result["execution"]["success"] is False
    assert research_work_items_repo.list_for_project(pid) == []
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "propose_tasks"


def test_existing_dependencies_in_the_same_project_are_wired(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    existing = research_work_items_repo.create(pid, "1", "Already there")
    _install(monkeypatch, _FakeClient([_propose(_task(title="New", depends_on_existing_ids=[existing]))]))
    assert supervisor_service.draft_researches("P")["execution"]["success"] is True
    new = [i for i in research_work_items_repo.list_for_project(pid) if i["id"] != existing][0]
    assert [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(new["id"])] == [existing]


def test_a_cycle_between_proposals_creates_nothing(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([_propose(
        _task(title="A", depends_on_batch_indices=[1]), _task(title="B", depends_on_batch_indices=[0]),
    )]))
    assert supervisor_service.draft_researches("P")["execution"]["success"] is False
    assert research_work_items_repo.list_for_project(pid) == []


def test_batch_dependencies_are_wired_to_real_ids(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([_propose(_task(title="A"), _task(title="B", depends_on_batch_indices=[0]))]))
    supervisor_service.draft_researches("P")
    a, b = research_work_items_repo.list_for_project(pid)
    assert [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(b["id"])] == [a["id"]]


def test_no_tool_call_raises(temp_db, app_context, monkeypatch):
    projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([SimpleNamespace(type="text", text="Hmm")]))
    with pytest.raises(RuntimeError):
        supervisor_service.draft_researches("P")


# ---- reviewing ----

def _running_card(pid, output="Sources (1):\n- A - https://a.example\n\nReport body.", run_status="completed", **card_fields):
    card_id = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB",
                                                prompt_text="Find the fees.", entities=["Fees"], **card_fields)
    research_work_items_repo.update_fields(card_id, status="RUNNING")
    research_runs_repo.create(f"run_{card_id}", pid, "resp", None, "Find the fees.")
    research_runs_repo.update(f"run_{card_id}", research_work_item_id=card_id, status=run_status,
                              output_text=output, prompt_text="Find the fees.")
    return card_id


def _review(outcome, **extra):
    return _Block("review_outcome", dict({"completeness_score": 0.8, "evidence_score": 0.7,
                                          "identified_gaps": ["No intake dates"], "outcome": outcome,
                                          "reason": "Because"}, **extra))


def test_review_is_forced_to_the_review_tool(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    client = _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    supervisor_service.review_card("P", card_id)
    call = client.calls[0]
    assert [t["name"] for t in call["tools"]] == ["review_outcome"]
    assert call["tool_choice"] == {"type": "tool", "name": "review_outcome"}
    assert "Report body." in call["messages"][0]["content"]


@pytest.mark.parametrize("outcome,status", [
    ("COMPLETE", "COMPLETE"), ("FOLLOW_UP_REQUIRED", "FOLLOW_UP_REQUIRED"),
    ("NEEDS_HUMAN", "WAITING_FOR_HUMAN"), ("FAILED", "FAILED"),
])
def test_review_outcomes(temp_db, app_context, monkeypatch, outcome, status):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    _install(monkeypatch, _FakeClient([_review(outcome)]))
    result = supervisor_service.review_card("P", card_id)
    row = research_work_items_repo.get(card_id)
    assert result["reviewed"] is True
    assert row["status"] == status
    assert row["completeness_score"] == 0.8
    assert json.loads(row["identified_gaps_json"]) == ["No intake dates"]
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "review_outcome"


def test_follow_up_arrives_as_a_new_draft(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    _install(monkeypatch, _FakeClient([_review("FOLLOW_UP_REQUIRED", followup={
        "title": "Verify intake dates", "focus": ["Intakes"], "research_method": "TARGETED_WEB",
        "rationale": "Intake dates were missing"})]))
    result = supervisor_service.review_card("P", card_id)
    follow = research_work_items_repo.get(result["followup_card_id"])
    assert follow["status"] == "PROPOSED"
    assert follow["suggested_from_work_item_id"] == card_id
    assert follow["phase_key"] == "4"
    assert follow["prompt_text"].startswith("ROLE: analyst.")
    assert research_work_items_repo.list_dependencies(card_id) == []


def test_complete_on_a_card_already_flagged_for_a_person_waits_for_them(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid, human_review_required=True)
    _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    supervisor_service.review_card("P", card_id)
    assert research_work_items_repo.get(card_id)["status"] == "WAITING_FOR_HUMAN"


def test_a_failed_review_call_releases_the_claim(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    _install(monkeypatch, _FakeClient(error=RuntimeError("Anthropic is down")))
    result = supervisor_service.review_card("P", card_id)
    assert result["reviewed"] is False
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "review_error"


def test_a_card_already_claimed_is_not_reviewed_twice(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    research_task_service.claim_transition(card_id, "RUNNING", "REVIEWING")
    client = _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    assert supervisor_service.review_card("P", card_id)["reviewed"] is False
    assert client.calls == []


@pytest.mark.parametrize("run_status,output", [("running", None), ("failed", None), ("completed", "")])
def test_cards_whose_run_has_not_produced_a_report_are_not_reviewed(temp_db, app_context, monkeypatch, run_status, output):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid, output=output, run_status=run_status)
    client = _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    assert supervisor_service.review_card("P", card_id)["reviewed"] is False
    assert client.calls == []
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"


def test_review_sees_the_report_clipped_to_the_existing_limit(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid, output="x" * (supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS + 5000))
    client = _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    supervisor_service.review_card("P", card_id)
    content = client.calls[0]["messages"][0]["content"]
    assert "x" * supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS in content
    assert "x" * (supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS + 1) not in content
