import json

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo
from services import llm_service, research_task_service, tools

DRAFT = "ROLE: analyst. A complete drafted research prompt for this card."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_service, "prompt_completion", lambda system, user, max_tokens=4000: DRAFT)
    return projects_repo.get_or_create_id("P")


def _task(**over):
    return dict({"phase_key": "1", "title": "Landscape overview", "research_method": "TARGETED_WEB",
                 "focus": ["Providers"], "rationale": "Phase 1 has nothing yet"}, **over)


def _create(caller="supervisor", *tasks, **extra):
    return tools.run_tool("create_research_task", caller, "P", dict({"tasks": list(tasks)}, **extra))


def test_supervisor_creates_drafted_cards_that_wait_for_approval(pid):
    result = _create("supervisor", _task(), _task(phase_key="3", title="Website review", framework_key="oes-marketing-website"))
    assert result.ok and result.data["drafted_from_framework"] == 2
    items = research_work_items_repo.list_for_project(pid)
    assert [i["status"] for i in items] == ["PROPOSED", "PROPOSED"]
    assert items[0]["prompt_text"] == DRAFT
    assert (items[0]["framework_key"], items[1]["framework_key"]) == ("oes-landscape", "oes-marketing-website")
    assert agent_decisions_repo.list_for_project(pid) == []  # the Supervisor's decision is recorded by supervisor_service


def test_user_can_write_their_own_prompt_and_it_is_recorded(pid):
    result = _create("user", _task(prompt_text="Research every provider and cite each claim, please.", draft_prompt=False))
    (card_id,) = result.data["created_task_ids"]
    assert research_work_items_repo.get(card_id)["prompt_text"].startswith("Research every provider")
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "user_add"


def test_user_can_add_a_card_with_no_prompt_yet(pid):
    (card_id,) = _create("user", _task(draft_prompt=False)).data["created_task_ids"]
    assert research_work_items_repo.get(card_id)["prompt_text"] is None


def test_batch_dependencies_are_wired(pid):
    existing = research_work_items_repo.create(pid, "1", "Earlier")
    a, b = _create("supervisor", _task(title="A", depends_on_existing_ids=[existing]),
                   _task(title="B", depends_on_batch_indices=[0])).data["created_task_ids"]

    def deps(i):
        return [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(i)]

    assert deps(a) == [existing] and deps(b) == [a]


def test_focus_sent_as_one_string_is_a_list(pid):
    (card_id,) = _create("supervisor", _task(focus="Fees\nDelivery")).data["created_task_ids"]
    assert json.loads(research_work_items_repo.get(card_id)["entities_json"]) == ["Fees", "Delivery"]


@pytest.mark.parametrize("bad", [
    _task(phase_key="7"), _task(title="   "), _task(research_method="DEEP_MAGIC"),
    _task(depends_on_batch_indices=[5]), _task(depends_on_existing_ids=[99999]), _task(priority="urgent"),
])
def test_invalid_requests_create_nothing(pid, bad):
    result = _create("supervisor", _task(title="Fine"), bad)
    assert result.error["code"] == "invalid_input"
    assert research_work_items_repo.list_for_project(pid) == []


def test_dependency_on_another_projects_card_creates_nothing(pid):
    theirs = research_work_items_repo.create(projects_repo.get_or_create_id("Other"), "1", "Theirs")
    assert _create("supervisor", _task(depends_on_existing_ids=[theirs])).error["code"] == "invalid_input"
    assert research_work_items_repo.list_for_project(pid) == []


def test_a_loop_between_new_cards_creates_nothing(pid):
    result = _create("supervisor", _task(title="A", depends_on_batch_indices=[1]), _task(title="B", depends_on_batch_indices=[0]))
    assert result.error["code"] == "invalid_input"
    assert research_work_items_repo.list_for_project(pid) == []


def test_too_many_cards_in_one_call(pid):
    assert _create("supervisor", *[_task(title=f"T{i}") for i in range(11)]).error["code"] == "invalid_input"


def test_followup_is_a_draft_linked_to_its_original(pid):
    original = research_task_service.create_task(pid, "4", "Fees", research_method="FILE_ANALYSIS", framework_key="oes-product-features")
    result = tools.run_tool("create_followup_task", "supervisor", "P",
                            {"task_id": original, "title": "Verify intakes", "focus": "Intakes"})
    follow = research_work_items_repo.get(result.data["followup_task_id"])
    assert (follow["status"], follow["phase_key"], follow["suggested_from_work_item_id"]) == ("PROPOSED", "4", original)
    assert follow["research_method"] == "FILE_ANALYSIS"  # falls back to the original's method
    assert json.loads(follow["entities_json"]) == ["Intakes"]
    assert follow["prompt_text"] == DRAFT


def test_request_human_review_moves_a_card_being_reviewed(pid):
    card = research_task_service.create_task(pid, "4", "Fees")
    research_work_items_repo.update_fields(card, status="REVIEWING")
    data = tools.run_tool("request_human_review", "supervisor", "P", {"task_id": card, "reason": "Unsure"}).data
    assert data == {"task_id": card, "new_status": "WAITING_FOR_HUMAN"}
    assert research_work_items_repo.get(card)["human_review_required"] == 1


def test_request_human_review_only_flags_other_cards(pid):
    card = research_task_service.create_task(pid, "4", "Fees")
    data = tools.run_tool("request_human_review", "supervisor", "P", {"task_id": card}).data
    assert data["new_status"] == "PROPOSED" and research_work_items_repo.get(card)["human_review_required"] == 1


@pytest.mark.parametrize("name,inputs", [("request_human_review", {"task_id": 1}), ("no_action", {})])
def test_supervisor_only_tools_refuse_the_user(pid, name, inputs):
    research_task_service.create_task(pid, "4", "Card")
    assert tools.run_tool(name, "user", "P", inputs).error["code"] == "not_allowed"


def test_no_action(pid):
    assert tools.run_tool("no_action", "supervisor", "P", {"reason": "Nothing to add"}).data == {}
