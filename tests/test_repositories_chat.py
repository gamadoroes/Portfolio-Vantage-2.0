# tests/test_repositories_chat.py
from db.repositories import chat_repo, projects_repo, research_runs_repo


def test_create_and_get_session(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Research May 01")
    row = chat_repo.get_session("chat_1")
    assert row["name"] == "Research May 01"


def test_rename_session(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Old")
    chat_repo.rename_session("chat_1", "New")
    assert chat_repo.get_session("chat_1")["name"] == "New"


def test_append_message_increments_seq(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    seq1 = chat_repo.append_message("chat_1", "user", "hello")
    seq2 = chat_repo.append_message("chat_1", "assistant", "hi there")
    assert seq1 == 1
    assert seq2 == 2
    messages = chat_repo.list_messages("chat_1")
    assert [m["content"] for m in messages] == ["hello", "hi there"]


def test_link_artefact_to_message(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    seq = chat_repo.append_message("chat_1", "assistant", "output")
    from db.repositories import artefacts_repo
    artefacts_repo.upsert("art_1", pid, "Artefact")
    assert chat_repo.link_artefact_to_message("chat_1", seq, "art_1") is True
    messages = chat_repo.list_messages("chat_1")
    assert messages[0]["artefact_id"] == "art_1"


def test_link_artefact_to_missing_message_returns_false(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    assert chat_repo.link_artefact_to_message("chat_1", 99, "art_1") is False


def test_delete_session_removes_messages(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    chat_repo.append_message("chat_1", "user", "hi")
    chat_repo.delete_session("chat_1")
    assert chat_repo.get_session("chat_1") is None
    assert chat_repo.list_messages("chat_1") == []


def test_delete_session_referenced_by_research_run_does_not_raise(temp_db):
    """Regression test: research_runs.chat_session_id has no ON DELETE
    clause and foreign_keys=ON, so deleting a chat session still referenced
    by a research run used to raise sqlite3.IntegrityError. delete_session()
    must detach the run's chat_session_id instead of cascading, so the run
    row survives with chat_session_id NULL.
    """
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    research_runs_repo.create("run_1", pid, "resp_1", "chat_1", "prompt preview")

    chat_repo.delete_session("chat_1")  # must not raise

    assert chat_repo.get_session("chat_1") is None
    run = research_runs_repo.get("run_1")
    assert run is not None
    assert run["chat_session_id"] is None
