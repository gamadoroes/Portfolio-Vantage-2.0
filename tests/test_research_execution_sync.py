from types import SimpleNamespace

import pytest

from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
from services import research_execution_service


def _web_task_with_run(pid, title, run_id, response_id="resp_1", run_status="running", output_text=None):
    task_id = research_work_items_repo.create(pid, "4", title)
    research_work_items_repo.update_fields(task_id, status="RUNNING")
    research_runs_repo.create(run_id, pid, response_id, None, "prompt")
    research_runs_repo.update(
        run_id, research_work_item_id=task_id, status=run_status, output_text=output_text
    )
    return task_id


def _openai_response(status, text=None, citation=None, error=None, citations=()):
    """Shaped like a real OpenAI Responses object (see test_deep_research_citations)."""
    content = []
    if text:
        all_citations = ([citation] if citation else []) + list(citations)
        annotations = [
            SimpleNamespace(type="url_citation", start_index=0, end_index=0, title=title, url=url)
            for title, url in all_citations
        ]
        content = [SimpleNamespace(type="output_text", text=text, annotations=annotations)]
    return SimpleNamespace(
        id="resp_1",
        status=status,
        output_text=text,
        output=[SimpleNamespace(type="message", content=content)] if content else [],
        error=error,
        last_error=None,
    )


def _serve(monkeypatch, responses):
    """Make retrieve_deep_research answer from {response_id: response-or-exception}; record the lookups."""
    looked_up = []

    def retrieve(response_id):
        looked_up.append(response_id)
        answer = responses[response_id]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(research_execution_service.openai_service, "retrieve_deep_research", retrieve)
    return looked_up


def test_sync_saves_finished_web_research_text_and_sources(temp_db, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _web_task_with_run(pid, "Fees", "run_1")
    _serve(monkeypatch, {"resp_1": _openai_response(
        "completed", "The fee is $40,000.", citation=("Fee schedule", "https://example.com/fees"),
    )})

    research_execution_service.sync_web_research_runs("P")

    run = research_runs_repo.get("run_1")
    assert run["status"] == "completed"
    assert "The fee is $40,000." in run["output_text"]
    # The supervisor scores evidence, so it must be able to see where claims came from.
    assert "Fee schedule" in run["output_text"]
    assert "https://example.com/fees" in run["output_text"]


def test_sync_lists_a_page_cited_many_times_only_once(temp_db, monkeypatch):
    # Real deep-research reports cite one page dozens of times with different
    # "#:~:text=..." fragments (63 citations were only 12 pages), which would
    # otherwise fill the supervisor's whole view of the report with a source list.
    pid = projects_repo.get_or_create_id("P")
    _web_task_with_run(pid, "Fees", "run_1")
    page = "https://online.example.edu/mba"
    _serve(monkeypatch, {"resp_1": _openai_response("completed", "Body text.", citations=[
        ("Online MBA", f"{page}#:~:text=Tuition%20is"),
        ("Online MBA", f"{page}#:~:text=Duration%20is"),
        ("Other page", "https://other.example.edu/fees"),
    ])})

    research_execution_service.sync_web_research_runs("P")

    saved = research_runs_repo.get("run_1")["output_text"]
    assert saved.count(page) == 1
    assert "#:~:text" not in saved
    assert "https://other.example.edu/fees" in saved
    assert "Sources (2)" in saved


def test_sync_caps_a_very_long_source_list_but_says_how_many_were_left_out(temp_db, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _web_task_with_run(pid, "Fees", "run_1")
    many = [(f"Page {i}", f"https://example.edu/page-{i}") for i in range(1, 26)]
    _serve(monkeypatch, {"resp_1": _openai_response("completed", "Body text.", citations=many)})

    research_execution_service.sync_web_research_runs("P")

    saved = research_runs_repo.get("run_1")["output_text"]
    assert "Sources (25)" in saved
    assert "https://example.edu/page-20" in saved
    assert "https://example.edu/page-21" not in saved
    assert "(+5 more)" in saved
    assert "Body text." in saved  # the report itself is still there after the list


def test_sync_fills_in_text_for_a_run_the_browser_already_marked_completed(temp_db, monkeypatch):
    # The existing browser poller flips status to "completed" but never saves the text.
    pid = projects_repo.get_or_create_id("P")
    _web_task_with_run(pid, "Fees", "run_1", run_status="completed", output_text=None)
    _serve(monkeypatch, {"resp_1": _openai_response("completed", "The fee is $40,000.")})

    research_execution_service.sync_web_research_runs("P")

    assert "The fee is $40,000." in research_runs_repo.get("run_1")["output_text"]


@pytest.mark.parametrize("openai_status", ["queued", "in_progress"])
def test_sync_leaves_research_that_is_still_running_alone(temp_db, monkeypatch, openai_status):
    pid = projects_repo.get_or_create_id("P")
    _web_task_with_run(pid, "Fees", "run_1")
    _serve(monkeypatch, {"resp_1": _openai_response(openai_status)})

    research_execution_service.sync_web_research_runs("P")

    run = research_runs_repo.get("run_1")
    assert run["status"] == "running"
    assert not run["output_text"]


def test_sync_marks_research_that_failed_as_failed_with_the_reason(temp_db, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _web_task_with_run(pid, "Fees", "run_1")
    _serve(monkeypatch, {"resp_1": _openai_response("failed", error=SimpleNamespace(message="rate limit hit"))})

    research_execution_service.sync_web_research_runs("P")

    run = research_runs_repo.get("run_1")
    assert run["status"] == "failed"
    assert "rate limit hit" in run["error"]


def test_sync_marks_research_that_finished_without_any_text_as_failed(temp_db, monkeypatch):
    # Otherwise the supervisor would review an empty report, and the run would be
    # looked up again on every cycle forever.
    pid = projects_repo.get_or_create_id("P")
    _web_task_with_run(pid, "Fees", "run_1")
    _serve(monkeypatch, {"resp_1": _openai_response("completed", text=None)})

    research_execution_service.sync_web_research_runs("P")

    assert research_runs_repo.get("run_1")["status"] == "failed"


def test_sync_does_not_look_up_runs_that_need_nothing(temp_db, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    # A file-analysis run still in flight: no text yet, but nothing at OpenAI to look up.
    _web_task_with_run(pid, "File analysis", "run_a", response_id=None, run_status="running", output_text=None)
    _web_task_with_run(pid, "Already saved", "run_b", response_id="resp_b", run_status="completed", output_text="saved")
    _web_task_with_run(pid, "Already failed", "run_c", response_id="resp_c", run_status="failed")
    looked_up = _serve(monkeypatch, {})

    research_execution_service.sync_web_research_runs("P")

    assert looked_up == []


def test_sync_survives_a_failed_lookup_and_still_syncs_the_other_runs(temp_db, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _web_task_with_run(pid, "Flaky", "run_1", response_id="resp_1")
    _web_task_with_run(pid, "Fine", "run_2", response_id="resp_2")
    _serve(monkeypatch, {
        "resp_1": RuntimeError("network down"),
        "resp_2": _openai_response("completed", "All good."),
    })

    research_execution_service.sync_web_research_runs("P")  # must not raise

    assert research_runs_repo.get("run_1")["status"] == "running"  # untouched, retried next cycle
    assert "All good." in research_runs_repo.get("run_2")["output_text"]
