from db.repositories import artefacts_repo, projects_repo


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
