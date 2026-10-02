# tests/test_repositories_chat.py
from db.repositories import chat_repo, projects_repo


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
