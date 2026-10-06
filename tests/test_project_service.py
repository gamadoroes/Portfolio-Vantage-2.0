import services.project_service as project_service
from db.repositories import projects_repo


def test_create_project_registers_db_row_and_creates_dirs(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert project_service.create_project("New Proj") is True
    assert projects_repo.exists("New Proj") is True
    assert (tmp_path / "projects" / "New Proj" / "files").is_dir()
    assert (tmp_path / "projects" / "New Proj" / "outputs").is_dir()


def test_create_project_returns_false_if_already_exists(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_service.create_project("Dup")
    assert project_service.create_project("Dup") is False


def test_list_projects_reflects_db_not_disk(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_service.create_project("Alpha")
    project_service.create_project("Beta")
    assert project_service.list_projects() == ["Alpha", "Beta"]


def test_metadata_round_trip(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_service.create_project("P")
    project_service.save_project_metadata("P", {"description": "desc", "archived": True})
    assert project_service.get_project_metadata("P") == {"description": "desc", "archived": True}


def test_config_round_trip_selected_files(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_service.create_project("P")
    config = project_service.load_project_config("P")
    assert config["selected_files"] == []
    assert config["selected_file_ids"] == []
    assert config["created"] is not None


# ---- the projects folder does not depend on the folder the app is started from ----

def test_projects_folder_sits_beside_the_app_whatever_the_start_folder(tmp_path, monkeypatch):
    from config import APP_ROOT
    monkeypatch.delenv("PROJECTS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert project_service.get_projects_dir().resolve() == (APP_ROOT / "projects").resolve()
    assert not (tmp_path / "projects").exists()


def test_projects_folder_can_be_set(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere" / "projects"
    monkeypatch.setenv("PROJECTS_DIR", str(target))
    assert project_service.get_projects_dir() == target
    assert target.is_dir()


def test_tests_keep_using_their_own_temporary_folder(tmp_path, monkeypatch):
    # conftest points PROJECTS_DIR at a relative "projects", so a test that changes into tmp_path stays there.
    monkeypatch.chdir(tmp_path)
    assert project_service.get_projects_dir().resolve() == (tmp_path / "projects").resolve()
