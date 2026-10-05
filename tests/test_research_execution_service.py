from types import SimpleNamespace

import pytest

from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
from services import insights_service, llm_service, research_execution_service, research_task_service

PROMPT = "Research the fee structures of every online Psychology postgraduate program in Australia."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Run workers inline instead of on a thread.
    monkeypatch.setattr(research_execution_service, "_spawn", lambda target, *args: target(*args))
    return projects_repo.get_or_create_id("P")


def _ready_card(pid, method="TARGETED_WEB", phase="4", prompt=PROMPT, title="Fees"):
    card_id = research_task_service.create_task(pid, phase, title, research_method=method, prompt_text=prompt)
    research_work_items_repo.update_fields(card_id, status="READY")
    return card_id


def _fake_openai(monkeypatch, fail_for=()):
    sent = []

    def start(prompt):
        sent.append(prompt)
        if prompt in fail_for:
            raise RuntimeError("OpenAI is down")
        return SimpleNamespace(id=f"resp_{len(sent)}", status="queued")

    monkeypatch.setattr(research_execution_service.openai_service, "start_deep_research", start)
    return sent


def test_start_web_run_sends_the_full_prompt_and_links_the_run(pid, monkeypatch):
    sent = _fake_openai(monkeypatch)
    card_id = _ready_card(pid)
    result = research_execution_service.start_runs("P", [card_id])
    assert result == {"started": [card_id], "not_ready": [], "failed": []}
    assert sent == [PROMPT]
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"
    run = research_runs_repo.find_latest_for_work_item(card_id)
    assert run["prompt_text"] == PROMPT
    assert run["response_id"] == "resp_1"
    assert run["status"] == "running"


def test_only_cards_still_approved_are_started(pid, monkeypatch):
    sent = _fake_openai(monkeypatch)
    draft = research_task_service.create_task(pid, "4", "Draft", research_method="TARGETED_WEB", prompt_text=PROMPT)
    other_pid = projects_repo.get_or_create_id("Other")
    foreign = _ready_card(other_pid)
    result = research_execution_service.start_runs("P", [draft, foreign, 99999])
    assert result["started"] == []
    assert result["not_ready"] == [draft, foreign, 99999]
    assert sent == []
    assert research_work_items_repo.get(draft)["status"] == "PROPOSED"
    assert research_work_items_repo.get(foreign)["status"] == "READY"


def test_a_card_cannot_be_started_twice(pid, monkeypatch):
    sent = _fake_openai(monkeypatch)
    card_id = _ready_card(pid)
    research_execution_service.start_runs("P", [card_id])
    second = research_execution_service.start_runs("P", [card_id])
    assert second["not_ready"] == [card_id]
    assert len(sent) == 1


def test_one_failed_start_does_not_stop_the_others(pid, monkeypatch):
    _fake_openai(monkeypatch, fail_for={"Bad prompt that OpenAI rejects for this test case."})
    bad = _ready_card(pid, prompt="Bad prompt that OpenAI rejects for this test case.", title="Bad")
    good = _ready_card(pid, title="Good")
    result = research_execution_service.start_runs("P", [bad, good])
    assert result["started"] == [good]
    assert result["failed"][0]["id"] == bad
    assert "OpenAI is down" in result["failed"][0]["error"]
    assert research_work_items_repo.get(bad)["status"] == "FAILED"
    assert research_runs_repo.find_latest_for_work_item(bad)["status"] == "failed"


@pytest.mark.parametrize("method,prompt", [(None, PROMPT), ("TARGETED_WEB", None), ("TARGETED_WEB", "   ")])
def test_cards_from_before_the_board_without_prompt_or_method_are_refused(pid, monkeypatch, method, prompt):
    sent = _fake_openai(monkeypatch)
    card_id = _ready_card(pid, method=method, prompt=prompt)
    result = research_execution_service.start_runs("P", [card_id])
    assert result["failed"][0]["id"] == card_id
    assert "back to draft" in result["failed"][0]["error"]
    assert research_work_items_repo.get(card_id)["status"] == "READY"
    assert research_runs_repo.find_latest_for_work_item(card_id) is None
    assert sent == []


def test_file_analysis_uses_the_card_prompt_and_completes_the_run(pid, monkeypatch):
    calls = []
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: calls.append((system, user, max_tokens)) or "## Findings\nFees found.")
    card_id = _ready_card(pid, method="FILE_ANALYSIS")
    research_execution_service.start_runs("P", [card_id])
    run = research_runs_repo.find_latest_for_work_item(card_id)
    assert run["status"] == "completed"
    assert run["output_text"] == "## Findings\nFees found."
    assert PROMPT in calls[0][0]
    assert calls[0][2] == research_execution_service.FILE_ANALYSIS_MAX_TOKENS
    # The card waits on the board's refresh to be reviewed.
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"


def test_file_analysis_failure_fails_the_run_but_leaves_the_card_for_refresh(pid, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("Anthropic is down")
    monkeypatch.setattr(llm_service, "prompt_completion", boom)
    card_id = _ready_card(pid, method="FILE_ANALYSIS")
    research_execution_service.start_runs("P", [card_id])
    run = research_runs_repo.find_latest_for_work_item(card_id)
    assert run["status"] == "failed"
    assert "Anthropic is down" in run["error"]
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"


def _complete_phases_1_to_6(pid):
    for phase in ("1", "2", "3", "4", "5", "6"):
        tid = research_work_items_repo.create(pid, phase, f"Done {phase}")
        research_work_items_repo.update_fields(tid, status="COMPLETE")


def test_synthesis_refused_while_phase_7_is_locked(pid, monkeypatch):
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    result = research_execution_service.start_runs("P", [card_id])
    assert result["failed"][0]["id"] == card_id
    assert research_work_items_repo.get(card_id)["status"] == "READY"


def test_synthesis_writes_phase_7_and_completes_the_card(pid, monkeypatch):
    _complete_phases_1_to_6(pid)
    insights_service.save_insights("P", {
        "generated_at": "2026-10-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
        "phases": {str(i): {"title": f"Phase {i}", "summary": f"Summary {i}", "confidence": "high",
                             "evidence_sources": [], "gaps": [], "suggested_topics": [],
                             "linked_files": [], "linked_file_ids": []} for i in range(1, 8)},
    })
    calls = []
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: calls.append(user) or "Options: do X.")
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    research_execution_service.start_runs("P", [card_id])
    assert research_work_items_repo.get(card_id)["status"] == "COMPLETE"
    assert PROMPT in calls[0] and "Summary 3" in calls[0]
    current = insights_service.load_current_insights("P")
    assert current["phases"]["7"]["summary"] == "Options: do X."
    assert current["generated_at"] == "2026-10-01T00:00:00"
    assert research_runs_repo.find_latest_for_work_item(card_id)["output_text"] == "Options: do X."


def test_synthesis_keeps_insights_changes_made_while_it_was_writing(pid, monkeypatch):
    _complete_phases_1_to_6(pid)
    insights_service.save_insights("P", {
        "generated_at": "2026-10-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
        "phases": {str(i): {"title": f"Phase {i}", "summary": f"Summary {i}", "confidence": "high",
                             "evidence_sources": [], "gaps": [], "suggested_topics": [],
                             "linked_files": [], "linked_file_ids": []} for i in range(1, 8)},
    })
    research_execution_service.save_project_file("P", "Linked meanwhile.md", "# Report")
    stable_id = research_execution_service.ensure_file_id("P", "Linked meanwhile.md")

    def slow_claude(system, user, max_tokens=4000):
        # While Claude is writing the options, a report is linked and a phase summary refreshed.
        current = insights_service.load_current_insights("P")
        phases = dict(current["phases"])
        phase_4 = dict(phases["4"], linked_file_ids=[stable_id], linked_files=["Linked meanwhile.md"])
        phase_3 = dict(phases["3"], summary="Refreshed summary 3")
        phases["3"], phases["4"] = phase_3, phase_4
        insights_service.save_insights("P", {
            "generated_at": "2026-10-03T00:00:00", "competitors": [],
            "competitor_landscape_markdown": "", "phases": phases,
        })
        return "Options: do X."

    monkeypatch.setattr(llm_service, "prompt_completion", slow_claude)
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    research_execution_service.start_runs("P", [card_id])
    current = insights_service.load_current_insights("P")
    assert current["phases"]["7"]["summary"] == "Options: do X."
    assert current["phases"]["7"]["confidence"] == "medium"
    assert current["phases"]["3"]["summary"] == "Refreshed summary 3"
    assert current["phases"]["4"]["linked_file_ids"] == [stable_id]
    assert current["generated_at"] == "2026-10-03T00:00:00"
