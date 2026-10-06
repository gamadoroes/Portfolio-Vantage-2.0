import threading
import time

import pytest

from db.repositories import projects_repo, research_work_items_repo
from services import llm_service, project_service, prompt_drafting_service, prompt_frameworks


@pytest.fixture
def project(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    projects_repo.get_or_create_id("P")
    project_service.save_project_prompt("P", "project_prompt", "Assess online Psychology postgrad programs.")
    return "P"


def _capture(monkeypatch, reply="ROLE: analyst. Research every provider in depth and cite all sources."):
    calls = []

    def fake(system_prompt, user_message, max_tokens=4000):
        calls.append({"system": system_prompt, "user": user_message})
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm_service, "prompt_completion", fake)
    return calls


def test_draft_prompt_uses_the_framework_and_card_details(project, monkeypatch):
    calls = _capture(monkeypatch)
    result = prompt_drafting_service.draft_prompt(
        project, "4", None, "Fee structures", focus=["Fees", "FEE-HELP"], rationale="Price drives choice"
    )
    assert result["drafted"] is True
    assert result["framework_key"] == "oes-product-features"
    assert result["prompt_text"].startswith("ROLE: analyst.")
    assert calls[0]["system"] == prompt_frameworks.get_framework("oes-product-features")["system_prompt"]
    user = calls[0]["user"]
    for expected in ("Fee structures", "Fees, FEE-HELP", "Price drives choice",
                     "Assess online Psychology postgrad programs.", "Market / Program Type"):
        assert expected in user


def test_draft_prompt_strips_budget_sections(project, monkeypatch):
    _capture(monkeypatch, reply="Research the fees in detail.\n\n## Token Budget Allocation\n- 10,000 tokens\n\n## Output\nA table.")
    text = prompt_drafting_service.draft_prompt(project, "4", None, "Fees")["prompt_text"]
    assert "Token Budget" not in text
    assert "A table." in text


@pytest.mark.parametrize("reply", [RuntimeError("Anthropic is down"), "   "])
def test_draft_prompt_falls_back_when_drafting_fails(project, monkeypatch, reply):
    _capture(monkeypatch, reply=reply)
    result = prompt_drafting_service.draft_prompt(project, "4", None, "Fee structures", focus=["Fees"])
    assert result["drafted"] is False
    assert result["prompt_text"].startswith(prompt_drafting_service.FALLBACK_MARKER)
    assert "Fee structures" in result["prompt_text"]


def test_create_drafted_card_creates_a_proposed_card_with_its_prompt(project, monkeypatch):
    _capture(monkeypatch)
    result = prompt_drafting_service.create_drafted_card(
        project, "3", "Website review", research_method="TARGETED_WEB", focus=["UX"],
        rationale="Covers competitors", framework_key="oes-marketing-website",
    )
    row = research_work_items_repo.get(result["card_id"])
    assert result["drafted"] is True
    assert row["status"] == "PROPOSED"
    assert row["framework_key"] == "oes-marketing-website"
    assert row["research_method"] == "TARGETED_WEB"
    assert row["rationale"] == "Covers competitors"
    assert row["prompt_text"].startswith("ROLE: analyst.")


USER_PROMPT = "My own prompt: research every provider's website and cite all sources used."


def _act_during_drafting(monkeypatch, action):
    def fake(system_prompt, user_message, max_tokens=4000):
        card = research_work_items_repo.list_for_project(projects_repo.get_id("P"))[0]
        action(card["id"])
        return "ROLE: analyst. Drafted prompt that arrives after the user acted."
    monkeypatch.setattr(llm_service, "prompt_completion", fake)


def test_a_prompt_written_and_approved_during_drafting_is_kept(project, monkeypatch):
    from services import board_service

    def write_and_approve(card_id):
        board_service.edit_card("P", card_id, {"prompt_text": USER_PROMPT})
        board_service.approve("P", card_id)
    _act_during_drafting(monkeypatch, write_and_approve)
    result = prompt_drafting_service.create_drafted_card(
        project, "3", "Website review", research_method="TARGETED_WEB", framework_key="oes-marketing-website",
    )
    row = research_work_items_repo.get(result["card_id"])
    assert (row["status"], row["prompt_text"]) == ("READY", USER_PROMPT)


def test_a_draft_still_fills_a_card_whose_title_was_edited_with_the_prompt_left_empty(project, monkeypatch):
    from services import board_service
    _act_during_drafting(monkeypatch, lambda card_id: board_service.edit_card(
        "P", card_id, {"title": "Website review, renamed", "prompt_text": ""}))
    result = prompt_drafting_service.create_drafted_card(
        project, "3", "Website review", research_method="TARGETED_WEB", framework_key="oes-marketing-website",
    )
    row = research_work_items_repo.get(result["card_id"])
    assert row["title"] == "Website review, renamed"
    assert row["status"] == "PROPOSED"
    assert row["prompt_text"].startswith("ROLE: analyst. Drafted prompt")


# ---- several prompts drafted at the same time ----

def _job(n, **over):
    return dict({"phase_key": "4", "framework_key": "oes-product-features", "title": f"Research {n}", "focus": ["Fees"],
                 "rationale": "Price drives choice"}, **over)


def _title_of(user_message):
    return user_message.split("RESEARCH TITLE: ")[1].split("\n")[0]


def test_draft_prompts_returns_the_results_in_input_order(project, monkeypatch):
    def fake(system_prompt, user_message, max_tokens=4000):
        title = _title_of(user_message)
        time.sleep({"Research 1": 0.3, "Research 2": 0.1, "Research 3": 0.0}[title])  # they finish in reverse
        return f"ROLE: analyst. Prompt for {title}"
    monkeypatch.setattr(llm_service, "prompt_completion", fake)
    results = prompt_drafting_service.draft_prompts(project, [_job(1), _job(2), _job(3)])
    assert [r["prompt_text"] for r in results] == [f"ROLE: analyst. Prompt for Research {n}" for n in (1, 2, 3)]
    assert all(r["drafted"] and r["framework_key"] == "oes-product-features" for r in results)


def test_draft_prompts_with_nothing_to_draft_does_nothing(project, monkeypatch):
    calls = _capture(monkeypatch)
    assert prompt_drafting_service.draft_prompts(project, []) == []
    assert calls == []


def test_no_more_than_four_prompts_are_drafted_at_once(project, monkeypatch):
    lock = threading.Lock()
    running = {"now": 0, "most": 0}

    def fake(system_prompt, user_message, max_tokens=4000):
        with lock:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
        time.sleep(0.15)
        with lock:
            running["now"] -= 1
        return "ROLE: analyst. A drafted prompt."
    monkeypatch.setattr(llm_service, "prompt_completion", fake)
    results = prompt_drafting_service.draft_prompts(project, [_job(n) for n in range(1, 9)])
    assert len(results) == 8 and all(r["drafted"] for r in results)
    assert running["most"] == 4


def test_each_draft_runs_inside_the_apps_context_even_in_another_thread(project, monkeypatch):
    from flask import current_app

    from app import create_app
    seen = []

    def fake(system_prompt, user_message, max_tokens=4000):
        seen.append((threading.get_ident(), current_app.config["ANTHROPIC_MODEL"]))  # needs the app context, as the real call does
        return "ROLE: analyst. A drafted prompt."
    monkeypatch.setattr(llm_service, "prompt_completion", fake)
    flask_app = create_app()
    with flask_app.app_context():
        results = prompt_drafting_service.draft_prompts(project, [_job(1), _job(2)])
    assert all(r["drafted"] for r in results)
    assert len(seen) == 2 and {model for _, model in seen} == {flask_app.config["ANTHROPIC_MODEL"]}
    assert threading.get_ident() not in {thread for thread, _ in seen}


def test_one_draft_that_fails_gets_the_fallback_and_the_others_are_drafted(project, monkeypatch):
    def fake(system_prompt, user_message, max_tokens=4000):
        if _title_of(user_message) == "Research 2":
            raise RuntimeError("Anthropic is down")
        return "ROLE: analyst. A drafted prompt."
    monkeypatch.setattr(llm_service, "prompt_completion", fake)
    results = prompt_drafting_service.draft_prompts(project, [_job(1), _job(2), _job(3)])
    assert [r["drafted"] for r in results] == [True, False, True]
    assert results[1]["prompt_text"].startswith(prompt_drafting_service.FALLBACK_MARKER)
    assert "Research 2" in results[1]["prompt_text"]


def test_a_worker_that_raises_gives_its_card_the_fallback_and_stops_no_other(project, monkeypatch):
    real = prompt_drafting_service.draft_prompt

    def flaky(project_name, phase_key, framework_key, title, **kwargs):
        if title == "Research 2":
            raise ValueError("Unknown framework: 'nope'")
        return real(project_name, phase_key, framework_key, title, **kwargs)

    _capture(monkeypatch)
    monkeypatch.setattr(prompt_drafting_service, "draft_prompt", flaky)
    results = prompt_drafting_service.draft_prompts(project, [_job(1), _job(2, focus=["Fees", "FEE-HELP"]), _job(3)])
    assert [r["drafted"] for r in results] == [True, False, True]
    assert results[1]["framework_key"] == "oes-product-features"
    assert results[1]["prompt_text"].startswith(prompt_drafting_service.FALLBACK_MARKER)
    assert "Research 2" in results[1]["prompt_text"] and "Fees, FEE-HELP" in results[1]["prompt_text"]
