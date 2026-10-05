import pytest

from db.repositories import (
    agent_decisions_repo,
    projects_repo,
    research_runs_repo,
    research_work_items_repo,
    tool_calls_repo,
)
from services import insights_service, project_service, supervisor_service, tools


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return projects_repo.get_or_create_id("P")


def _card_with_run(pid, title="Fees", output="Report " * 10, status="RUNNING", run_status="completed", run_id="run_1"):
    card = research_work_items_repo.create(pid, "4", title, research_method="TARGETED_WEB", prompt_text="Find the fees.")
    research_work_items_repo.update_fields(card, status=status)
    research_runs_repo.create(run_id, pid, "resp", None, "preview")
    research_runs_repo.update(run_id, research_work_item_id=card, status=run_status, output_text=output, prompt_text="Find the fees.")
    return card


def test_project_state_describes_the_project(pid):
    project_service.save_project_prompt("P", "project_prompt", "Assess the MBA market.")
    a = research_work_items_repo.create(pid, "1", "Landscape")
    b = _card_with_run(pid)
    research_work_items_repo.add_dependency(b, a)
    agent_decisions_repo.record(pid, "no_action", '{"input": {"reason": "x"}}')
    result = tools.run_tool("get_project_state", "supervisor", "P", {})
    assert result.ok
    state = result.data
    assert state["objective"] == "Assess the MBA market."
    assert [p["key"] for p in state["phases"]] == ["1", "2", "3", "4", "5", "6", "7"]
    assert len(state["phases"][2]["frameworks"]) == 3
    cards = {c["id"]: c for c in state["cards"]}
    assert cards[b]["dependency_ids"] == [a]
    assert cards[b]["latest_run"]["output_text"].startswith("Report")
    assert cards[a]["latest_run"] is None
    assert state["recent_decisions"][0]["decision_type"] == "no_action"
    assert state["phase7"]["unlocked"] is False


def test_project_state_only_includes_this_project(pid):
    other = projects_repo.get_or_create_id("Other")
    research_work_items_repo.create(other, "4", "Theirs")
    assert tools.run_tool("get_project_state", "system", "P", {}).data["cards"] == []


def test_project_state_includes_existing_phase_summaries(pid):
    insights_service.save_insights("P", {"generated_at": "", "competitors": [], "competitor_landscape_markdown": "",
                                         "phases": {"1": {"title": "L", "summary": "Established fact", "confidence": "high",
                                                          "evidence_sources": [], "gaps": [], "suggested_topics": [],
                                                          "linked_files": [], "linked_file_ids": []}}})
    assert tools.run_tool("get_project_state", "system", "P", {}).data["phase_summaries"] == {"1": "Established fact"}


def test_get_task_clips_the_report_to_the_review_limit(pid):
    card = _card_with_run(pid, output="x" * (supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS + 5000))
    data = tools.run_tool("get_task", "supervisor", "P", {"task_id": card}).data
    assert data["card"]["id"] == card and data["card"]["title"] == "Fees"
    run = data["latest_run"]
    assert run["report_excerpt"].startswith("x" * supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS)
    assert run["report_excerpt"].endswith("...[truncated]")
    assert run["report_chars_total"] == supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS + 5000
    assert "output_text" not in run


def test_get_task_without_a_run(pid):
    card = research_work_items_repo.create(pid, "4", "No run yet")
    assert tools.run_tool("get_task", "user", "P", {"task_id": card}).data["latest_run"] is None


def test_get_task_needs_a_task_in_this_project(pid):
    other = research_work_items_repo.create(projects_repo.get_or_create_id("Other"), "4", "Theirs")
    assert tools.run_tool("get_task", "supervisor", "P", {"task_id": other}).error["code"] == "not_found"
    assert tools.run_tool("get_task", "supervisor", "P", {}).error["code"] == "invalid_input"


def test_get_task_rejects_an_oversized_id(pid):
    assert tools.run_tool("get_task", "supervisor", "P", {"task_id": 2**70}).error["code"] == "invalid_input"


def test_build_context_reads_the_project_through_the_tool(pid):
    supervisor_service.build_context("P")
    log = tool_calls_repo.list_for_project(pid)[0]
    assert (log["tool"], log["caller"], log["ok"]) == ("get_project_state", "system", 1)
