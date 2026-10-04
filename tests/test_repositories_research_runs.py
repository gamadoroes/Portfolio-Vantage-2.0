from datetime import datetime, timedelta

from db.repositories import projects_repo, research_runs_repo


def test_create_and_get(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "prompt preview")
    row = research_runs_repo.get("run_1")
    assert row["status"] == "running"
    assert row["response_id"] == "resp_1"


def test_update_fields(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "preview")
    from db.repositories import artefacts_repo
    artefacts_repo.upsert("art_1", pid, "Artefact")
    assert research_runs_repo.update("run_1", status="completed", artefact_id="art_1") is True
    row = research_runs_repo.get("run_1")
    assert row["status"] == "completed"
    assert row["artefact_id"] == "art_1"


def test_update_unknown_run_returns_false(temp_db):
    assert research_runs_repo.update("nope", status="failed") is False


def test_list_for_project(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "preview")
    research_runs_repo.create("run_2", pid, "resp_2", None, "preview")
    assert {r["id"] for r in research_runs_repo.list_for_project(pid)} == {"run_1", "run_2"}


def test_find_recent_duplicate_by_preview(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "same preview")
    cutoff = (datetime.now() - timedelta(seconds=30)).isoformat()
    found = research_runs_repo.find_recent_running_or_queued_with_preview(pid, "same preview", cutoff)
    assert found is not None
    not_found = research_runs_repo.find_recent_running_or_queued_with_preview(pid, "different", cutoff)
    assert not_found is None


def test_find_recent_duplicate_ignores_completed_runs(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "same preview")
    research_runs_repo.update("run_1", status="completed")
    cutoff = (datetime.now() - timedelta(seconds=30)).isoformat()
    found = research_runs_repo.find_recent_running_or_queued_with_preview(pid, "same preview", cutoff)
    assert found is None


def test_find_latest_for_work_item(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, None, None, "first")
    research_runs_repo.update("run_1", research_work_item_id=5)
    research_runs_repo.create("run_2", pid, None, None, "second")
    research_runs_repo.update("run_2", research_work_item_id=5)

    latest = research_runs_repo.find_latest_for_work_item(5)
    assert latest["id"] == "run_2"


def test_find_latest_for_work_item_none_when_no_runs(temp_db):
    assert research_runs_repo.find_latest_for_work_item(9999) is None


def test_find_latest_for_work_item_scoped_to_correct_item(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_a", pid, None, None, "for item 1")
    research_runs_repo.update("run_a", research_work_item_id=1)
    research_runs_repo.create("run_b", pid, None, None, "for item 2")
    research_runs_repo.update("run_b", research_work_item_id=2)

    assert research_runs_repo.find_latest_for_work_item(1)["id"] == "run_a"
    assert research_runs_repo.find_latest_for_work_item(2)["id"] == "run_b"
