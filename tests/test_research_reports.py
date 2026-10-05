import re
from pathlib import Path

import pytest

from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
from services import insights_service, research_execution_service, research_task_service


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return projects_repo.get_or_create_id("P")


def _finished(pid, title="Fee structures", run_id="run_1", phase="4"):
    card_id = research_task_service.create_task(pid, phase, title, research_method="TARGETED_WEB", prompt_text="The prompt we sent.")
    research_work_items_repo.update_fields(card_id, status="RUNNING")
    research_runs_repo.create(run_id, pid, "resp_1", None, "The prompt we sent.")
    research_runs_repo.update(run_id, research_work_item_id=card_id, status="completed",
                              output_text="Sources (1):\n- A - https://a.example\n\nThe report body.",
                              prompt_text="The prompt we sent.", completed_at="2026-10-05T10:00:00")
    return card_id


def _files_dir():
    return Path("projects") / "P" / "files"


def test_save_report_writes_a_source_file_and_records_it(pid):
    card_id = _finished(pid)
    stable_id = research_execution_service.save_report("P", card_id, "run_1")
    names = [p.name for p in _files_dir().iterdir()]
    assert len(names) == 1
    assert re.fullmatch(r"Research P4 - Fee structures \(\d{4}-\d{2}-\d{2}\)\.md", names[0])
    content = (_files_dir() / names[0]).read_text(encoding="utf-8")
    assert "# Fee structures" in content
    assert "The prompt we sent." in content
    assert "The report body." in content
    assert stable_id.startswith("f_")
    assert research_runs_repo.get("run_1")["report_stable_file_id"] == stable_id


def test_save_report_is_saved_once(pid):
    card_id = _finished(pid)
    first = research_execution_service.save_report("P", card_id, "run_1")
    second = research_execution_service.save_report("P", card_id, "run_1")
    assert first == second
    assert len(list(_files_dir().iterdir())) == 1


def test_titles_with_characters_windows_forbids_still_save(pid):
    card_id = _finished(pid, title='Fees: 2025/26 <draft> "final"? | *all*')
    research_execution_service.save_report("P", card_id, "run_1")
    (name,) = [p.name for p in _files_dir().iterdir()]
    assert not re.search(r'[<>:"/\\|?*]', name)
    assert name.startswith("Research P4 - Fees 2025 26 draft final all")


def test_two_reports_with_the_same_title_on_the_same_day_get_distinct_names(pid):
    a = _finished(pid, run_id="run_a")
    b = _finished(pid, run_id="run_b")
    research_execution_service.save_report("P", a, "run_a")
    research_execution_service.save_report("P", b, "run_b")
    names = sorted(p.name for p in _files_dir().iterdir())
    assert len(names) == 2
    assert names[1].endswith(" 2.md") or names[0].endswith(" 2.md")


def test_link_report_links_to_the_phase_and_keeps_generated_at(pid):
    insights_service.save_insights("P", {"generated_at": "2026-10-01T09:00:00.000Z", "competitors": [],
                                         "competitor_landscape_markdown": "", "phases": {}})
    card_id = _finished(pid)
    stable_id = research_execution_service.save_report("P", card_id, "run_1")
    assert research_execution_service.link_report("P", card_id, "run_1") is True
    current = insights_service.load_current_insights("P")
    assert stable_id in current["phases"]["4"]["linked_file_ids"]
    assert current["generated_at"] == "2026-10-01T09:00:00.000Z"
    assert research_runs_repo.get("run_1")["report_linked_at"]


def test_a_report_the_user_unlinked_is_not_linked_again(pid):
    card_id = _finished(pid)
    research_execution_service.save_report("P", card_id, "run_1")
    research_execution_service.link_report("P", card_id, "run_1")
    current = insights_service.load_current_insights("P")
    current["phases"]["4"]["linked_file_ids"] = []
    current["phases"]["4"]["linked_files"] = []
    insights_service.save_insights("P", current)

    assert research_execution_service.link_report("P", card_id, "run_1") is False
    assert insights_service.load_current_insights("P")["phases"]["4"]["linked_file_ids"] == []


def test_link_report_does_nothing_when_the_file_was_deleted(pid):
    card_id = _finished(pid)
    research_execution_service.save_report("P", card_id, "run_1")
    for path in _files_dir().iterdir():
        path.unlink()
    assert research_execution_service.link_report("P", card_id, "run_1") is False
    assert research_runs_repo.get("run_1")["report_linked_at"]


def test_link_report_without_a_saved_report_does_nothing(pid):
    card_id = _finished(pid)
    assert research_execution_service.link_report("P", card_id, "run_1") is False
    assert research_runs_repo.get("run_1")["report_linked_at"] is None
