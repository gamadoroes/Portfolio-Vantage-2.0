from db.repositories import projects_repo
import services.chat_service as chat_service


def test_create_new_chat_returns_unique_ids(temp_db):
    projects_repo.get_or_create_id("P")
    id1 = chat_service.create_new_chat("P")
    id2 = chat_service.create_new_chat("P")
    assert id1 != id2


def test_append_message_and_load_sessions(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    assert chat_service.append_message_to_chat("P", chat_id, {"role": "user", "content": "hi"}) is True
    sessions = chat_service.load_chat_sessions("P")
    assert sessions[chat_id]["messages"][0]["content"] == "hi"


def test_append_message_to_missing_chat_returns_false(temp_db):
    projects_repo.get_or_create_id("P")
    assert chat_service.append_message_to_chat("P", "nope", {"role": "user", "content": "hi"}) is False


def test_load_chat_sessions_locked_matches_unlocked(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    chat_service.append_message_to_chat("P", chat_id, {"role": "user", "content": "hi"})
    assert chat_service.load_chat_sessions_locked("P") == chat_service.load_chat_sessions("P")


def test_delete_chat(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    assert chat_service.delete_chat("P", chat_id) is True
    assert chat_id not in chat_service.load_chat_sessions("P")


def test_delete_chat_missing_returns_false(temp_db):
    projects_repo.get_or_create_id("P")
    assert chat_service.delete_chat("P", "nope") is False


def test_rename_chat(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    assert chat_service.rename_chat("P", chat_id, "New Name") is True
    assert chat_service.load_chat_sessions("P")[chat_id]["name"] == "New Name"


def test_link_artifact_to_message_by_index(temp_db):
    from db.repositories import artefacts_repo
    projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", projects_repo.get_id("P"), "Artefact")
    chat_id = chat_service.create_new_chat("P")
    chat_service.append_message_to_chat("P", chat_id, {"role": "user", "content": "first"})
    chat_service.append_message_to_chat("P", chat_id, {"role": "assistant", "content": "second"})

    assert chat_service.link_artifact_to_message_by_index("P", chat_id, 1, "art_1") == "ok"
    messages = chat_service.load_chat_sessions("P")[chat_id]["messages"]
    assert messages[1]["artifact_id"] == "art_1"


def test_link_artifact_to_message_out_of_range(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    chat_service.append_message_to_chat("P", chat_id, {"role": "user", "content": "only one"})
    assert chat_service.link_artifact_to_message_by_index("P", chat_id, 5, "art_1") == "out_of_range"


def test_link_artifact_to_message_chat_not_found(temp_db):
    projects_repo.get_or_create_id("P")
    assert chat_service.link_artifact_to_message_by_index("P", "nope", 0, "art_1") == "not_found"
