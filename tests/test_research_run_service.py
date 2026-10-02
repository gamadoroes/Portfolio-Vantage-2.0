import pytest

import services.research_run_service as research_run_service
from db.repositories import artefacts_repo, projects_repo
from services.chat_service import create_new_chat


def _make_chat(project_name):
    """Create a project and a real chat session, since research_runs.chat_session_id
    is a foreign key into chat_sessions and must reference a row that exists."""
    projects_repo.get_or_create_id(project_name)
    return create_new_chat(project_name)


def test_create_and_load_runs(temp_db):
    chat_id = _make_chat("P")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "running"


def test_complete_run(temp_db):
    chat_id = _make_chat("P")
    artefacts_repo.upsert("art_1", projects_repo.get_id("P"), "Artefact")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    assert research_run_service.complete_run("P", run_id, artifact_id="art_1") is True
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "completed"
    assert runs[run_id]["artifact_id"] == "art_1"


def test_is_duplicate_run_detects_recent_identical_prompt(temp_db):
    chat_id = _make_chat("P")
    research_run_service.create_run("P", "resp_1", chat_id, "same prompt")
    assert research_run_service.is_duplicate_run("P", "same prompt") is True
    assert research_run_service.is_duplicate_run("P", "different prompt") is False


def test_is_duplicate_run_ignores_completed(temp_db):
    chat_id = _make_chat("P")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "same prompt")
    research_run_service.complete_run("P", run_id)
    assert research_run_service.is_duplicate_run("P", "same prompt") is False


def test_create_run_rejects_invalid_project_name(temp_db):
    with pytest.raises(ValueError):
        research_run_service.create_run(None, "resp_1", None, "a prompt")
    with pytest.raises(ValueError):
        research_run_service.create_run("../evil", "resp_1", None, "a prompt")


def test_fail_run(temp_db):
    chat_id = _make_chat("P")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    assert research_run_service.fail_run("P", run_id, "boom") is True
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "failed"
    assert runs[run_id]["error"] == "boom"


def test_cancel_run(temp_db):
    chat_id = _make_chat("P")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    assert research_run_service.cancel_run("P", run_id) is True
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "cancelled"


def test_update_run_missing_returns_false(temp_db):
    projects_repo.get_or_create_id("P")
    assert research_run_service.update_run("P", "nope", status="completed") is False


def test_load_runs_unknown_project_returns_empty(temp_db):
    assert research_run_service.load_runs("NoSuchProject") == {}


def test_complete_run_from_wrong_project_returns_false(temp_db):
    chat_id = _make_chat("P")
    projects_repo.get_or_create_id("Q")
    artefacts_repo.upsert("art_1", projects_repo.get_id("P"), "Artefact")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    assert research_run_service.complete_run("Q", run_id, artifact_id="art_1") is False
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "running"
    assert runs[run_id]["artifact_id"] is None


def test_fail_run_from_wrong_project_returns_false(temp_db):
    chat_id = _make_chat("P")
    projects_repo.get_or_create_id("Q")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    assert research_run_service.fail_run("Q", run_id, "boom") is False
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "running"
    assert runs[run_id]["error"] is None


def test_cancel_run_from_wrong_project_returns_false(temp_db):
    chat_id = _make_chat("P")
    projects_repo.get_or_create_id("Q")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    assert research_run_service.cancel_run("Q", run_id) is False
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "running"


def test_update_run_from_wrong_project_returns_false(temp_db):
    chat_id = _make_chat("P")
    projects_repo.get_or_create_id("Q")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    assert research_run_service.update_run("Q", run_id, status="completed") is False
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "running"


def test_update_run_unknown_project_returns_false(temp_db):
    chat_id = _make_chat("P")
    run_id = research_run_service.create_run("P", "resp_1", chat_id, "a prompt")
    assert research_run_service.update_run("NoSuchProject", run_id, status="completed") is False
