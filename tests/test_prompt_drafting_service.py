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
