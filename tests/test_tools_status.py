import json

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo, tool_calls_repo
from services import llm_service, prompt_drafting_service, research_task_service, tools

GOOD = "Research the fee structures of every online Psychology postgraduate program."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_service, "prompt_completion", lambda s, u, max_tokens=4000: "ROLE: analyst. Drafted follow-up prompt.")
    return projects_repo.get_or_create_id("P")


def _card(pid, status="PROPOSED", prompt=GOOD, method="TARGETED_WEB", **kw):
    card = research_task_service.create_task(pid, "4", "Fees", research_method=method, prompt_text=prompt, **kw)
    if status != "PROPOSED":
        research_work_items_repo.update_fields(card, status=status)
    return card


def _act(card, action, caller="user"):
    return tools.run_tool("update_task_status", caller, "P", {"task_id": card, "action": action})


@pytest.mark.parametrize("action,start,end", [
    ("approve", "PROPOSED", "READY"), ("back_to_draft", "READY", "PROPOSED"), ("back_to_draft", "FAILED", "PROPOSED"),
    ("skip", "PROPOSED", "SKIPPED"), ("restore", "SKIPPED", "PROPOSED"), ("retry", "FAILED", "READY"),
    ("accept", "WAITING_FOR_HUMAN", "COMPLETE"), ("mark_failed", "WAITING_FOR_HUMAN", "FAILED"),
])
def test_user_actions(pid, action, start, end):
    card = _card(pid, status=start)
    result = _act(card, action)
    assert result.data == {"task_id": card, "new_status": end}
    assert research_work_items_repo.get(card)["status"] == end
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"].startswith("user_")


def test_needs_followup_creates_a_logged_child(pid):
    card = _card(pid, status="WAITING_FOR_HUMAN")
    research_work_items_repo.update_fields(card, identified_gaps_json=json.dumps(["No intake dates"]))
    result = _act(card, "needs_followup")
    assert result.data["new_status"] == "FOLLOW_UP_REQUIRED"
    children = [r for r in tool_calls_repo.list_for_project(pid) if r["parent_call_id"] == result.call_id]
    assert [c["tool"] for c in children] == ["create_followup_task"]


def test_needs_followup_clips_a_long_focus_so_the_child_cannot_fail(pid):
    card = _card(pid, status="WAITING_FOR_HUMAN")
    research_work_items_repo.update_fields(card, entities_json=json.dumps([f"{i} " + "x" * 300 for i in range(15)]))
    result = _act(card, "needs_followup")
    assert result.ok and result.data["new_status"] == "FOLLOW_UP_REQUIRED"
    children = [r for r in tool_calls_repo.list_for_project(pid) if r["parent_call_id"] == result.call_id]
    assert [c["ok"] for c in children] == [1]


def test_system_marks_a_failed_run(pid):
    card = _card(pid, status="RUNNING")
    assert _act(card, "run_failed", caller="system").data["new_status"] == "FAILED"


@pytest.mark.parametrize("caller,action", [("user", "run_failed"), ("system", "approve"), ("supervisor", "approve"),
                                           ("supervisor", "back_to_draft"), ("supervisor", "run_failed")])
def test_callers_only_get_their_actions(pid, caller, action):
    card = _card(pid, status="RUNNING" if action == "run_failed" else "PROPOSED")
    assert _act(card, action, caller=caller).error["code"] == "not_allowed"


@pytest.mark.parametrize("prompt,method", [("Too short", "TARGETED_WEB"), (None, "TARGETED_WEB"),
                                           (prompt_drafting_service.FALLBACK_MARKER + " Research question: Fees and more", "TARGETED_WEB"),
                                           (GOOD, None)])
def test_approve_refuses_cards_not_ready_to_send(pid, prompt, method):
    card = _card(pid, prompt=prompt, method=method)
    assert _act(card, "approve").error["code"] == "invalid_input"
    assert research_work_items_repo.get(card)["status"] == "PROPOSED"


def test_approve_in_the_wrong_state_is_a_conflict(pid):
    card = _card(pid, status="COMPLETE")
    assert _act(card, "approve").error == {
        "code": "conflict", "fields": [],
        "message": "This research has changed since the board was loaded. The board has been refreshed."}


def test_retry_limit_message(pid):
    card = _card(pid, status="FAILED")
    research_work_items_repo.update_fields(card, retry_count=3)
    assert "too many times" in _act(card, "retry").error["message"]


def test_unknown_action_is_invalid(pid):
    assert _act(_card(pid), "launch").error["code"] == "invalid_input"
