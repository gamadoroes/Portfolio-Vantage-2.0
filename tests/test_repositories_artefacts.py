from db.repositories import artefacts_repo, chat_repo, projects_repo, research_runs_repo


def test_upsert_then_get(temp_db):
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "My Artefact")
    row = artefacts_repo.get("art_1")
    assert row["name"] == "My Artefact"
    assert row["project_id"] == pid


def test_upsert_is_idempotent_update(temp_db):
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "First Name")
    artefacts_repo.upsert("art_1", pid, "Renamed")
    assert artefacts_repo.get("art_1")["name"] == "Renamed"


def test_list_for_project(temp_db):
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "A")
    artefacts_repo.upsert("art_2", pid, "B")
    names = {row["name"] for row in artefacts_repo.list_for_project(pid)}
    assert names == {"A", "B"}


def test_delete(temp_db):
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "A")
    artefacts_repo.delete("art_1")
    assert artefacts_repo.get("art_1") is None


def test_delete_artefact_referenced_by_research_run_does_not_raise(temp_db):
    """Regression test: research_runs.artefact_id has no ON DELETE clause
    and foreign_keys=ON, so deleting an artefact still referenced by a
    research run used to raise sqlite3.IntegrityError. delete() must detach
    the run's artefact_id instead of cascading, so the run row survives with
    artefact_id NULL.
    """
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "My Artefact")
    research_runs_repo.create("run_1", pid, "resp_1", None, "prompt preview")
    research_runs_repo.update("run_1", artefact_id="art_1")

    artefacts_repo.delete("art_1")  # must not raise

    run = research_runs_repo.get("run_1")
    assert run is not None
    assert run["artefact_id"] is None


def test_delete_artefact_referenced_by_message_does_not_raise(temp_db):
    """Regression test: project_messages.artefact_id has no ON DELETE clause
    and foreign_keys=ON, so deleting an artefact still referenced by a chat
    message used to raise sqlite3.IntegrityError. delete() must detach the
    message's artefact_id instead of cascading, so the message row survives
    with artefact_id NULL.
    """
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "My Artefact")
    chat_repo.create_session("chat_1", pid, "Chat")
    chat_repo.append_message("chat_1", "assistant", "output", artefact_id="art_1")

    artefacts_repo.delete("art_1")  # must not raise

    messages = chat_repo.list_messages("chat_1")
    assert len(messages) == 1
    assert messages[0]["artefact_id"] is None
