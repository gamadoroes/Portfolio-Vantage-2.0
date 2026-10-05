import json

import pytest

from db.repositories import projects_repo, research_work_items_repo, tool_calls_repo
from services import llm_service, research_task_service, tools


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_service, "prompt_completion", lambda s, u, max_tokens=4000: "ROLE: analyst. A drafted follow-up prompt.")
    return projects_repo.get_or_create_id("P")


def _reviewing(pid, **kw):
    card = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB", **kw)
    research_work_items_repo.update_fields(card, status="REVIEWING")
    return card


def _evaluate(card, outcome, /, **extra):
    inputs = dict({"task_id": card, "completeness_score": 0.8, "evidence_score": 0.7,
                   "identified_gaps": ["No intake dates"], "outcome": outcome, "reason": "Because"}, **extra)
    return tools.run_tool("evaluate_research_output", "supervisor", "P", inputs)


@pytest.mark.parametrize("outcome,status", [
    ("COMPLETE", "COMPLETE"), ("FOLLOW_UP_REQUIRED", "FOLLOW_UP_REQUIRED"),
    ("NEEDS_HUMAN", "WAITING_FOR_HUMAN"), ("FAILED", "FAILED"),
])
def test_outcomes(pid, outcome, status):
    card = _reviewing(pid)
    result = _evaluate(card, outcome)
    assert result.ok and result.data["new_status"] == status
    row = research_work_items_repo.get(card)
    assert (row["status"], row["completeness_score"]) == (status, 0.8)
    assert json.loads(row["identified_gaps_json"]) == ["No intake dates"]


def test_follow_up_is_created_as_a_logged_child_call(pid):
    card = _reviewing(pid)
    result = _evaluate(card, "FOLLOW_UP_REQUIRED", followup={"title": "Verify intakes", "focus": "Intakes"})
    follow = research_work_items_repo.get(result.data["followup_task_id"])
    assert (follow["status"], follow["suggested_from_work_item_id"]) == ("PROPOSED", card)
    children = [r for r in tool_calls_repo.list_for_project(pid) if r["parent_call_id"] == result.call_id]
    assert [c["tool"] for c in children] == ["create_followup_task"]


def test_needs_human_goes_through_request_human_review(pid):
    card = _reviewing(pid)
    result = _evaluate(card, "NEEDS_HUMAN")
    assert research_work_items_repo.get(card)["human_review_required"] == 1
    assert [r["tool"] for r in tool_calls_repo.list_for_project(pid) if r["parent_call_id"] == result.call_id] == ["request_human_review"]


def test_a_long_reason_does_not_stop_a_needs_human_review(pid):
    card = _reviewing(pid)
    assert _evaluate(card, "NEEDS_HUMAN", reason="r" * 1500).data["new_status"] == "WAITING_FOR_HUMAN"


def test_complete_on_a_flagged_card_waits_for_the_person(pid):
    card = _reviewing(pid, human_review_required=True)
    assert _evaluate(card, "COMPLETE").data["new_status"] == "WAITING_FOR_HUMAN"


def test_gaps_sent_as_one_bulleted_string(pid):
    card = _reviewing(pid)
    _evaluate(card, "FAILED", identified_gaps="\n- One gap\n- Two gap")
    assert json.loads(research_work_items_repo.get(card)["identified_gaps_json"]) == ["One gap", "Two gap"]


def test_only_a_card_being_reviewed_can_be_evaluated(pid):
    card = research_task_service.create_task(pid, "4", "Fees")
    assert _evaluate(card, "COMPLETE").error["code"] == "conflict"
    assert research_work_items_repo.get(card)["status"] == "PROPOSED"


@pytest.mark.parametrize("bad", [{"completeness_score": 1.5}, {"outcome": "GREAT"}, {"evidence_score": "lots"}])
def test_invalid_review_input(pid, bad):
    card = _reviewing(pid)
    assert _evaluate(card, "COMPLETE", **bad).error["code"] == "invalid_input"
    assert research_work_items_repo.get(card)["status"] == "REVIEWING"


@pytest.mark.parametrize("caller", ["user", "system"])
def test_only_the_supervisor_evaluates(pid, caller):
    card = _reviewing(pid)
    result = tools.run_tool("evaluate_research_output", caller, "P",
                            {"task_id": card, "completeness_score": 1, "evidence_score": 1, "outcome": "COMPLETE"})
    assert result.error["code"] == "not_allowed"
