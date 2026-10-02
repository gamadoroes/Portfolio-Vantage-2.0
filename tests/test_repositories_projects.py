from db.repositories import projects_repo


def test_get_or_create_id_creates_then_reuses(temp_db):
    id1 = projects_repo.get_or_create_id("Proj A")
    id2 = projects_repo.get_or_create_id("Proj A")
    assert id1 == id2
    assert projects_repo.get_id("Proj A") == id1


def test_get_id_returns_none_for_unknown_project(temp_db):
    assert projects_repo.get_id("Nope") is None


def test_metadata_round_trip(temp_db):
    projects_repo.get_or_create_id("Proj B")
    projects_repo.save_metadata("Proj B", "a description", True)
    assert projects_repo.get_metadata("Proj B") == {"description": "a description", "archived": True}


def test_metadata_defaults_for_unknown_project(temp_db):
    assert projects_repo.get_metadata("Nope") == {"description": "", "archived": False}


def test_list_all(temp_db):
    projects_repo.get_or_create_id("Z Project")
    projects_repo.get_or_create_id("A Project")
    names = [p["name"] for p in projects_repo.list_all()]
    assert names == ["A Project", "Z Project"]


def test_exists(temp_db):
    assert projects_repo.exists("Ghost") is False
    projects_repo.get_or_create_id("Ghost")
    assert projects_repo.exists("Ghost") is True


def test_get_created_at(temp_db):
    projects_repo.get_or_create_id("P")
    created = projects_repo.get_created_at("P")
    assert created is not None
    assert projects_repo.get_created_at("Nope") is None
