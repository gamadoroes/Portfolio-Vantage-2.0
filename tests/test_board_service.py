import json
from datetime import datetime, timedelta, timezone

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo
from services import (
    board_service,
    insights_service,
    llm_service,
    prompt_drafting_service,
    research_execution_service,
    research_task_service,
    supervisor_service,
)

GOOD_PROMPT = "Research the fee structures of every online Psychology postgraduate program."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: "ROLE: analyst. A complete drafted research prompt for this card.")
    monkeypatch.setattr(research_execution_service, "sync_web_research_runs", lambda project_name: None)
    return projects_repo.get_or_create_id("P")


def _card(pid, status="PROPOSED", prompt=GOOD_PROMPT, method="TARGETED_WEB", phase="4", title="Fees", **kw):
    card_id = research_task_service.create_task(pid, phase, title, research_method=method, prompt_text=prompt, **kw)
    if status != "PROPOSED":
        research_work_items_repo.update_fields(card_id, status=status)
    return card_id


def _status(card_id):
    return research_work_items_repo.get(card_id)["status"]


def _last_decision(pid):
    return agent_decisions_repo.list_for_project(pid)[0]["decision_type"]


# ---- editing and approval ----

def test_editing_an_approved_card_sends_it_back_for_approval(pid):
    card_id = _card(pid, status="READY")
    board_service.edit_card("P", card_id, {"prompt_text": GOOD_PROMPT + " Include FEE-HELP."})
    assert _status(card_id) == "PROPOSED"
    assert research_work_items_repo.get(card_id)["prompt_text"].endswith("Include FEE-HELP.")
    assert _last_decision(pid) == "user_edit_unapproved"


def test_saving_an_unchanged_approved_card_keeps_it_approved(pid):
    card_id = _card(pid, status="READY")
    board_service.edit_card("P", card_id, {"prompt_text": GOOD_PROMPT, "title": "Fees"})
    assert _status(card_id) == "READY"


def test_focus_is_saved_as_a_list(pid):
    card_id = _card(pid)
    board_service.edit_card("P", card_id, {"focus": "Fees, , FEE-HELP "})
    assert json.loads(research_work_items_repo.get(card_id)["entities_json"]) == ["Fees", "FEE-HELP"]


@pytest.mark.parametrize("status", ["RUNNING", "COMPLETE", "SKIPPED"])
def test_cards_outside_draft_or_approved_cannot_be_edited(pid, status):
    card_id = _card(pid, status=status)
    with pytest.raises(board_service.BoardStateError):
        board_service.edit_card("P", card_id, {"title": "New"})


def test_approve_moves_a_valid_draft_to_approved(pid):
    card_id = _card(pid)
    board_service.ACTIONS["approve"]("P", card_id)
    assert _status(card_id) == "READY"
    assert _last_decision(pid) == "user_approve"


@pytest.mark.parametrize("prompt,method", [
    ("Too short", "TARGETED_WEB"),
    (None, "TARGETED_WEB"),
    (prompt_drafting_service.FALLBACK_MARKER + "\n\nResearch question: Fees and everything about them", "TARGETED_WEB"),
    (GOOD_PROMPT, None),
])
def test_approve_refuses_cards_that_are_not_ready_to_send(pid, prompt, method):
    card_id = _card(pid, prompt=prompt, method=method)
    with pytest.raises(ValueError):
        board_service.ACTIONS["approve"]("P", card_id)
    assert _status(card_id) == "PROPOSED"


def test_approve_waits_for_dependencies(pid):
    first = _card(pid, title="First")
    second = _card(pid, title="Second")
    research_task_service.add_dependency(second, first)
    with pytest.raises(board_service.BoardStateError, match="waits for another research"):
        board_service.ACTIONS["approve"]("P", second)


def test_approve_of_options_report_needs_phase_7_unlocked(pid):
    card_id = _card(pid, method="SYNTHESIS", phase="7", title="Options")
    with pytest.raises(board_service.BoardStateError):
        board_service.ACTIONS["approve"]("P", card_id)


def test_a_card_from_another_project_is_not_found(pid):
    other = projects_repo.get_or_create_id("Other")
    card_id = _card(other)
    with pytest.raises(board_service.CardNotFound):
        board_service.ACTIONS["approve"]("P", card_id)


# ---- other card actions ----

@pytest.mark.parametrize("action,start,end", [
    ("unapprove", "READY", "PROPOSED"),
    ("skip", "PROPOSED", "SKIPPED"),
    ("skip", "FAILED", "SKIPPED"),
    ("restore", "SKIPPED", "PROPOSED"),
    ("retry", "FAILED", "READY"),
    ("back-to-draft", "FAILED", "PROPOSED"),
    ("back-to-draft", "READY", "PROPOSED"),
    ("accept", "WAITING_FOR_HUMAN", "COMPLETE"),
    ("mark-failed", "WAITING_FOR_HUMAN", "FAILED"),
])
def test_simple_actions(pid, action, start, end):
    card_id = _card(pid, status=start)
    board_service.ACTIONS[action]("P", card_id)
    assert _status(card_id) == end


@pytest.mark.parametrize("action,start", [("restore", "PROPOSED"), ("accept", "COMPLETE"), ("retry", "READY")])
def test_actions_in_the_wrong_state_are_refused(pid, action, start):
    card_id = _card(pid, status=start)
    with pytest.raises(board_service.BoardStateError):
        board_service.ACTIONS[action]("P", card_id)
    assert _status(card_id) == start


def test_retry_is_refused_at_the_retry_limit(pid):
    card_id = _card(pid, status="FAILED")
    research_work_items_repo.update_fields(card_id, retry_count=3)
    with pytest.raises(board_service.BoardStateError, match="too many times"):
        board_service.ACTIONS["retry"]("P", card_id)


def test_needs_followup_creates_a_drafted_follow_up(pid):
    card_id = _card(pid, status="WAITING_FOR_HUMAN")
    research_work_items_repo.update_fields(card_id, identified_gaps_json=json.dumps(["No intake dates"]))
    board_service.ACTIONS["needs-followup"]("P", card_id)
    assert _status(card_id) == "FOLLOW_UP_REQUIRED"
    follow = [c for c in research_work_items_repo.list_for_project(pid) if c["suggested_from_work_item_id"] == card_id][0]
    assert follow["status"] == "PROPOSED"
    assert "No intake dates" in follow["rationale"]


def test_redraft_replaces_the_prompt_and_unapproves(pid):
    card_id = _card(pid, status="READY", framework_key="oes-product-features")
    board_service.ACTIONS["redraft"]("P", card_id)
    row = research_work_items_repo.get(card_id)
    assert row["prompt_text"].startswith("ROLE: analyst.")
    assert row["status"] == "PROPOSED"


def test_redraft_failure_keeps_the_users_prompt(pid, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(llm_service, "prompt_completion", boom)
    card_id = _card(pid, framework_key="oes-product-features")
    with pytest.raises(board_service.DraftingUnavailable):
        board_service.ACTIONS["redraft"]("P", card_id)
    assert research_work_items_repo.get(card_id)["prompt_text"] == GOOD_PROMPT


def test_redraft_drops_the_draft_if_the_card_was_approved_during_the_call(pid, monkeypatch):
    card_id = _card(pid, framework_key="oes-product-features")

    def approve_meanwhile(system, user, max_tokens=4000):
        research_work_items_repo.update_fields(card_id, status="READY")
        return "ROLE: analyst. A complete drafted research prompt for this card."
    monkeypatch.setattr(llm_service, "prompt_completion", approve_meanwhile)
    with pytest.raises(board_service.BoardStateError):
        board_service.ACTIONS["redraft"]("P", card_id)
    row = research_work_items_repo.get(card_id)
    assert (row["status"], row["prompt_text"]) == ("READY", GOOD_PROMPT)


def test_approve_is_refused_if_the_card_was_edited_after_it_was_checked(pid, monkeypatch):
    card_id = _card(pid)
    real = research_task_service.dependencies_satisfied

    def edit_meanwhile(work_item_id):
        research_work_items_repo.update_fields(work_item_id, prompt_text="Edited by someone else just now, quite different.")
        return real(work_item_id)
    monkeypatch.setattr(research_task_service, "dependencies_satisfied", edit_meanwhile)
    with pytest.raises(board_service.BoardStateError):
        board_service.ACTIONS["approve"]("P", card_id)
    assert _status(card_id) == "PROPOSED"


def test_create_card_with_my_own_prompt(pid):
    card_id = board_service.create_card("P", {"phase_key": "2", "title": "Personas", "research_method": "FILE_ANALYSIS",
                                              "focus": ["Career changers"], "prompt_text": GOOD_PROMPT, "draft_prompt": False})
    row = research_work_items_repo.get(card_id)
    assert (row["status"], row["phase_key"], row["prompt_text"]) == ("PROPOSED", "2", GOOD_PROMPT)
    assert row["framework_key"] == "oes-student-persona"


def test_create_card_drafted_for_me(pid):
    card_id = board_service.create_card("P", {"phase_key": "3", "title": "Sentiment", "research_method": "TARGETED_WEB",
                                              "framework_key": "oes-marketing-sentiment", "draft_prompt": True})
    row = research_work_items_repo.get(card_id)
    assert row["prompt_text"].startswith("ROLE: analyst.")
    assert row["framework_key"] == "oes-marketing-sentiment"


@pytest.mark.parametrize("data", [
    {"phase_key": "7", "title": "X", "research_method": "TARGETED_WEB"},
    {"phase_key": "4", "title": "  ", "research_method": "TARGETED_WEB"},
    {"phase_key": "4", "title": "X", "research_method": "SYNTHESIS"},
])
def test_create_card_validation(pid, data):
    with pytest.raises(ValueError):
        board_service.create_card("P", data)


def _unlock_phase_7(pid):
    for phase in ("1", "2", "3", "4", "5", "6"):
        _card(pid, status="COMPLETE", phase=phase, title=f"Done {phase}")


def test_options_report_draft_needs_phase_7_unlocked_and_only_one_open(pid):
    with pytest.raises(board_service.BoardStateError):
        board_service.draft_options_report("P")
    _unlock_phase_7(pid)
    card_id = board_service.draft_options_report("P")
    row = research_work_items_repo.get(card_id)
    assert (row["phase_key"], row["research_method"], row["framework_key"]) == ("7", "SYNTHESIS", "oes-options-whitespace")
    with pytest.raises(board_service.BoardStateError):
        board_service.draft_options_report("P")


def test_run_records_the_users_run(pid, monkeypatch):
    monkeypatch.setattr(research_execution_service, "start_runs",
                        lambda project_name, ids: {"started": list(ids), "not_ready": [], "failed": []})
    result = board_service.run("P", ["5", 6])
    assert result["started"] == [5, 6]
    assert _last_decision(pid) == "user_run"


# ---- refresh ----

def _finished_run(pid, card_id, run_id="run_1", prompt_text=GOOD_PROMPT, status="completed", output="Report body."):
    research_runs_repo.create(run_id, pid, "resp", None, "preview")
    research_runs_repo.update(run_id, research_work_item_id=card_id, status=status, output_text=output,
                              prompt_text=prompt_text, completed_at=datetime.now().isoformat() if status != "running" else None)


@pytest.fixture
def reviews(monkeypatch):
    calls = []
    monkeypatch.setattr(supervisor_service, "review_card", lambda project_name, card_id: calls.append(card_id) or {"reviewed": True})
    return calls


def test_refresh_saves_links_and_reviews_a_finished_research(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id)
    state = board_service.refresh("P")
    run = research_runs_repo.get("run_1")
    assert run["report_stable_file_id"] and run["report_linked_at"]
    assert state["linked_reports"] == 1
    assert reviews == [card_id]


def test_refresh_defers_linking_while_insights_are_generating(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id)
    board_service.refresh("P", defer_linking=True)
    assert research_runs_repo.get("run_1")["report_stable_file_id"]
    assert research_runs_repo.get("run_1")["report_linked_at"] is None
    assert board_service.refresh("P")["linked_reports"] == 1


def test_refresh_fails_a_card_whose_run_failed(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id, status="failed", output=None)
    board_service.refresh("P")
    assert _status(card_id) == "FAILED"
    assert reviews == []


def test_runs_from_before_the_board_get_no_report_file(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id, prompt_text=None)
    board_service.refresh("P")
    assert research_runs_repo.get("run_1")["report_stable_file_id"] is None
    assert reviews == [card_id]


def test_refresh_fails_a_my_files_run_cut_off_by_a_restart(pid, reviews):
    card_id = _card(pid, status="RUNNING", method="FILE_ANALYSIS")
    _finished_run(pid, card_id, status="running", output=None)
    old = (datetime.now() - timedelta(seconds=research_execution_service.ABANDONED_LOCAL_RUN_SECONDS + 60)).isoformat()
    research_runs_repo.update("run_1", created_at=old)
    board_service.refresh("P")
    assert research_runs_repo.get("run_1")["status"] == "failed"
    assert _status(card_id) == "FAILED"


def test_refresh_releases_a_stale_review_claim(pid, reviews):
    card_id = _card(pid, status="REVIEWING")
    old = (datetime.now() - timedelta(seconds=board_service.STALE_REVIEW_CLAIM_SECONDS + 60)).isoformat()
    from db.connection import get_connection
    with get_connection() as conn:
        conn.execute("UPDATE research_work_items SET updated_at = ? WHERE id = ?", (old, card_id))
    board_service.refresh("P")
    assert _status(card_id) == "RUNNING"


def test_a_refresh_already_in_progress_is_not_repeated(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id)
    lock = board_service._lock_for("P")
    lock.acquire()
    try:
        state = board_service.refresh("P")
    finally:
        lock.release()
    assert state["linked_reports"] == 0
    assert reviews == []


# ---- state ----

def test_state_describes_cards_for_the_board(pid):
    origin = _card(pid, status="FOLLOW_UP_REQUIRED", title="Origin")
    follow = _card(pid, title="Follow", suggested_from_work_item_id=origin, framework_key="oes-product-features")
    state = board_service.get_board_state("P")
    cards = {c["id"]: c for c in state["cards"]}
    assert cards[follow]["suggested_from"] == {"id": origin, "title": "Origin"}
    assert cards[origin]["followups"][0]["id"] == follow
    assert cards[follow]["framework_label"] == "Phase 4: Product Features"
    assert cards[follow]["method_label"] == "Web research"
    assert [p["key"] for p in state["phases"]] == ["1", "2", "3", "4", "5", "6", "7"]
    assert len(state["phases"][2]["frameworks"]) == 3


def test_state_flags_placeholder_prompts(pid):
    card_id = _card(pid, prompt=prompt_drafting_service.FALLBACK_MARKER + "\n\nResearch question: Fees")
    card = next(c for c in board_service.get_board_state("P")["cards"] if c["id"] == card_id)
    assert card["needs_prompt_edit"] is True


def _local_naive(utc_dt):
    return utc_dt.astimezone().replace(tzinfo=None).isoformat()


@pytest.mark.parametrize("generated_at,stale", [
    ("2026-10-05T01:00:00.000Z", True),   # summaries refreshed 30 minutes BEFORE the newest report
    ("2026-10-05T02:00:00.000Z", False),  # summaries refreshed AFTER it
])
def test_stale_summaries_compare_browser_utc_with_server_local_time(pid, generated_at, stale):
    insights_service.save_insights("P", {"generated_at": generated_at, "competitors": [],
                                         "competitor_landscape_markdown": "", "phases": {}})
    card_id = _card(pid, status="COMPLETE")
    _finished_run(pid, card_id)
    report_time = _local_naive(datetime(2026, 10, 5, 1, 30, tzinfo=timezone.utc))
    research_runs_repo.update("run_1", completed_at=report_time, report_stable_file_id="f_x")
    assert board_service.get_board_state("P")["phase7"]["summaries_stale"] is stale


def test_activity_reads_in_plain_words(pid):
    card_id = _card(pid)
    board_service.ACTIONS["approve"]("P", card_id)
    activity = board_service.get_board_state("P")["activity"]
    assert activity[0] == {"at": activity[0]["at"], "actor": "you", "text": 'You approved "Fees".'}
