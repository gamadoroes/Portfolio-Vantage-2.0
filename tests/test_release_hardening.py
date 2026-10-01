from io import BytesIO

import pytest

from app import app
from services.project_service import create_project, get_project_dir


@pytest.fixture()
def isolated_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.project_service.get_projects_dir",
        lambda: tmp_path / "projects",
    )
    return tmp_path / "projects"


@pytest.fixture()
def client(isolated_projects):
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_duplicate_project_name_rejected(client, isolated_projects):
    first = client.post("/api/projects", json={"name": "Demo Project"})
    second = client.post("/api/projects", json={"name": "Demo Project"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert sorted(p.name for p in isolated_projects.iterdir() if p.is_dir()) == ["Demo Project"]


def test_invalid_project_name_rejected(client, tmp_path):
    response = client.post("/api/projects", json={"name": r"..\outside"})

    assert response.status_code == 400
    assert not (tmp_path / "outside").exists()


def test_multipart_upload_succeeds_for_existing_project(client):
    create_project("Demo Project")

    response = client.post(
        "/api/files",
        data={
            "project": "Demo Project",
            "file": (BytesIO(b"hello world"), "notes.txt"),
        },
    )

    assert response.status_code == 200
    saved = get_project_dir("Demo Project") / "files" / "notes.txt"
    assert saved.read_text(encoding="utf-8") == "hello world"


def test_path_traversal_filename_rejected(client, tmp_path):
    create_project("Demo Project")

    response = client.post(
        "/api/files",
        json={
            "project": "Demo Project",
            "filename": r"..\..\escaped.txt",
            "content": "nope",
        },
    )

    assert response.status_code == 400
    assert not any(path.name == "escaped.txt" for path in tmp_path.rglob("escaped.txt"))
