import json
import threading
import time

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo
from services import llm_service, prompt_drafting_service, research_task_service, tools
from services.prompt_frameworks import resolve_framework

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


@pytest.mark.parametrize("name,inputs", [
    ("create_research_task", {"tasks": [_task(depends_on_existing_ids=[2**70])]}),
    ("create_followup_task", {"task_id": 0, "title": "Anything"}),
])
def test_out_of_range_card_ids_are_invalid_input_and_create_nothing(pid, name, inputs):
    assert tools.run_tool(name, "supervisor", "P", inputs).error["code"] == "invalid_input"
    assert research_work_items_repo.list_for_project(pid) == []


# ---- a batch is drafted at the same time ----

def _slow_drafting(monkeypatch, delay=0.3, fail_for=()):
    """Each draft takes `delay` seconds and answers with a prompt that names its own card's title."""
    def fake(system, user, max_tokens=4000):
        time.sleep(delay)
        title = user.split("RESEARCH TITLE: ")[1].split("\n")[0]
        if title in fail_for:
            raise RuntimeError("Anthropic is down")
        return f"ROLE: analyst. A complete drafted research prompt for: {title}"
    monkeypatch.setattr(llm_service, "prompt_completion", fake)


FOUR = [("1", "Alpha overview"), ("2", "Beta audience"), ("3", "Gamma website"), ("4", "Delta fees")]


def test_four_drafted_cards_are_drafted_at_the_same_time_and_each_gets_its_own_prompt(pid, monkeypatch):
    _slow_drafting(monkeypatch)
    started = time.monotonic()
    result = _create("supervisor", *[_task(phase_key=p, title=t) for p, t in FOUR])
    elapsed = time.monotonic() - started
    assert result.ok and result.data["drafted_from_framework"] == 4
    assert elapsed < 0.9, f"four 0.3 s drafts took {elapsed:.2f} s; one after another would be 1.2 s"
    for card_id, (_, title) in zip(result.data["created_task_ids"], FOUR, strict=True):
        row = research_work_items_repo.get(card_id)
        assert (row["title"], row["status"]) == (title, "PROPOSED")
        assert row["prompt_text"] == f"ROLE: analyst. A complete drafted research prompt for: {title}"


def test_one_draft_that_fails_gets_the_fallback_prompt_and_the_others_are_still_drafted(pid, monkeypatch):
    _slow_drafting(monkeypatch, delay=0.05, fail_for=("Beta audience",))
    result = _create("supervisor", *[_task(phase_key=p, title=t) for p, t in FOUR])
    assert result.ok and result.data["drafted_from_framework"] == 3
    prompts = {research_work_items_repo.get(i)["title"]: research_work_items_repo.get(i)["prompt_text"]
                for i in result.data["created_task_ids"]}
    assert prompts["Beta audience"].startswith(prompt_drafting_service.FALLBACK_MARKER)
    assert "Beta audience" in prompts["Beta audience"]
    for title in ("Alpha overview", "Gamma website", "Delta fees"):
        assert prompts[title] == f"ROLE: analyst. A complete drafted research prompt for: {title}"


def test_a_draft_that_blows_up_outright_gives_that_card_the_fallback_and_stops_no_other(pid, monkeypatch):
    real = prompt_drafting_service.draft_prompt

    def flaky(project_name, phase_key, framework_key, title, **kwargs):
        if title == "Gamma website":
            raise RuntimeError("something nobody planned for")
        return real(project_name, phase_key, framework_key, title, **kwargs)

    monkeypatch.setattr(prompt_drafting_service, "draft_prompt", flaky)
    result = _create("supervisor", *[_task(phase_key=p, title=t) for p, t in FOUR])
    assert result.ok and result.data["drafted_from_framework"] == 3
    rows = {research_work_items_repo.get(i)["title"]: research_work_items_repo.get(i) for i in result.data["created_task_ids"]}
    assert rows["Gamma website"]["prompt_text"].startswith(prompt_drafting_service.FALLBACK_MARKER)
    assert rows["Gamma website"]["framework_key"] == resolve_framework("3")  # the card keeps its framework
    assert all(rows[t]["prompt_text"] == DRAFT for t in ("Alpha overview", "Beta audience", "Delta fees"))


def test_ids_come_back_in_task_order_and_dependencies_are_wired_even_when_drafts_finish_out_of_order(pid, monkeypatch):
    def fake(system, user, max_tokens=4000):
        title = user.split("RESEARCH TITLE: ")[1].split("\n")[0]
        time.sleep({"A": 0.4, "B": 0.2, "C": 0.0}[title])  # the first card finishes last
        return f"ROLE: analyst. Prompt for {title}"
    monkeypatch.setattr(llm_service, "prompt_completion", fake)
    existing = research_work_items_repo.create(pid, "1", "Earlier")
    ids = _create("supervisor", _task(title="A", depends_on_existing_ids=[existing]),
                  _task(title="B", depends_on_batch_indices=[0]),
                  _task(title="C", depends_on_batch_indices=[0, 1])).data["created_task_ids"]
    assert [research_work_items_repo.get(i)["title"] for i in ids] == ["A", "B", "C"]
    assert ids == sorted(ids)

    def deps(i):
        return sorted(d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(i))

    assert [deps(ids[0]), deps(ids[1]), deps(ids[2])] == [[existing], [ids[0]], [ids[0], ids[1]]]
    assert [research_work_items_repo.get(i)["prompt_text"] for i in ids] == [f"ROLE: analyst. Prompt for {t}" for t in "ABC"]


def test_the_user_add_activity_is_still_recorded_once_per_card_in_order(pid):
    ids = _create("user", _task(title="A"), _task(title="B", draft_prompt=False)).data["created_task_ids"]
    decisions = [d for d in agent_decisions_repo.list_for_project(pid) if d["decision_type"] == "user_add"]
    assert sorted(d["research_work_item_id"] for d in decisions) == sorted(ids)


def test_the_database_is_written_only_from_the_calling_thread_and_in_task_order(pid, monkeypatch):
    seen = []
    real = research_work_items_repo.fill_empty_draft_prompt

    def spy(card_id, text):
        seen.append((card_id, threading.get_ident()))
        return real(card_id, text)

    monkeypatch.setattr(research_work_items_repo, "fill_empty_draft_prompt", spy)
    ids = _create("supervisor", *[_task(phase_key=p, title=t) for p, t in FOUR]).data["created_task_ids"]
    assert [card for card, _ in seen] == ids
    assert {thread for _, thread in seen} == {threading.get_ident()}
