import re
from types import SimpleNamespace

import pytest

from db.repositories import evidence_repo, projects_repo, research_runs_repo, research_work_items_repo
from services import evidence_service, insights_service, llm_service, research_execution_service, research_task_service

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


def _cited_phases(pid):
    """Phases 1-6 finished, a fact saved for Phase 4, and every phase write-up citing it. Returns the fact id."""
    _complete_phases_1_to_6(pid)
    card = research_work_items_repo.create(pid, "4", "Fees research")
    source, _ = evidence_repo.get_or_create_source(pid, "https://deakin.example/fees", "Deakin fees",
                                                   publisher="Deakin University")
    fact, _ = evidence_repo.get_or_create_fact(pid, "4", card, "Deakin charges $3,000", "deakin charges $3,000",
                                               cited_source_id=source, as_of="2026")
    insights_service.save_insights("P", {
        "generated_at": "2026-10-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
        "phases": {str(i): {"title": f"Phase {i}", "summary": f"Summary {i} [F{fact}]", "confidence": "high",
                             "evidence_sources": [], "gaps": [], "suggested_topics": [],
                             "linked_files": [], "linked_file_ids": []} for i in range(1, 8)},
    })
    return fact


def test_the_marker_instruction_uses_a_placeholder_not_a_real_looking_id():
    instruction = evidence_service.KEEP_MARKERS_INSTRUCTION
    assert "[F#]" in instruction and "[C#]" in instruction
    assert not re.search(r"\[[FC][0-9]", instruction)


def test_the_options_report_keeps_the_markers_and_its_saved_report_ends_with_its_sources(pid, monkeypatch):
    fact = _cited_phases(pid)
    seen = {}

    def fake_claude(system, user, max_tokens=4000):
        seen["system"], seen["user"] = system, user
        return f"Option A is cheap [F{fact}]."

    monkeypatch.setattr(llm_service, "prompt_completion", fake_claude)
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    research_execution_service.start_runs("P", [card_id])
    assert evidence_service.KEEP_MARKERS_INSTRUCTION in seen["system"]
    assert f"Summary 4 [F{fact}]" in seen["user"]
    assert research_runs_repo.find_latest_for_work_item(card_id)["output_text"] == (
        f"Option A is cheap [F{fact}].\n\n## Sources\n\n"
        f"- F{fact} — Deakin University — Deakin fees (2026) — https://deakin.example/fees")
    # Insights Phase 7 keeps the plain text: its card links the markers, and the Word report adds its own list.
    assert insights_service.load_current_insights("P")["phases"]["7"]["summary"] == f"Option A is cheap [F{fact}]."
    assert research_work_items_repo.get(card_id)["status"] == "COMPLETE"


def test_a_failed_sources_lookup_still_saves_the_options_report(pid, monkeypatch):
    fact = _cited_phases(pid)

    def broken(project_name):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(evidence_service, "citation_index", broken)
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: f"Option A [F{fact}].")
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    research_execution_service.start_runs("P", [card_id])
    assert research_runs_repo.find_latest_for_work_item(card_id)["output_text"] == f"Option A [F{fact}]."
    assert insights_service.load_current_insights("P")["phases"]["7"]["summary"] == f"Option A [F{fact}]."
    assert research_work_items_repo.get(card_id)["status"] == "COMPLETE"


def test_a_console_that_cannot_show_the_lookup_error_still_saves_the_options_report(pid, monkeypatch):
    fact = _cited_phases(pid)

    def broken(project_name):
        raise RuntimeError("could not read “Deakin” — é")

    def cp1252_console(*args, **kwargs):
        raise UnicodeEncodeError("charmap", "“", 0, 1, "character maps to <undefined>")

    monkeypatch.setattr(evidence_service, "citation_index", broken)
    # A module-level name shadows the builtin only for this module's own print calls.
    monkeypatch.setattr(research_execution_service, "print", cp1252_console, raising=False)
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: f"Option A [F{fact}].")
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    research_execution_service.start_runs("P", [card_id])
    run = research_runs_repo.find_latest_for_work_item(card_id)
    assert run["status"] == "completed"
    assert run["output_text"] == f"Option A [F{fact}]."
    assert insights_service.load_current_insights("P")["phases"]["7"]["summary"] == f"Option A [F{fact}]."
    assert research_work_items_repo.get(card_id)["status"] == "COMPLETE"
