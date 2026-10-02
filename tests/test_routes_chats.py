import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import artefacts_repo, projects_repo


@pytest.fixture
def client(tmp_path):
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            yield c
    set_database_path(None)


def _create_project_and_chat(client, monkeypatch, tmp_path, name="P"):
    monkeypatch.chdir(tmp_path)
    client.post("/api/projects", json={"name": name})
    resp = client.post("/api/chats", json={"project": name})
    return resp.get_json()["chat_id"]


def test_append_then_delete_chat(client, tmp_path, monkeypatch):
    chat_id = _create_project_and_chat(client, monkeypatch, tmp_path)
    resp = client.post(
        f"/api/chats/{chat_id}/append",
        json={"project": "P", "role": "user", "content": "hello"},
    )
    assert resp.get_json()["success"] is True

    resp = client.delete(f"/api/chats/{chat_id}", query_string={"project": "P"})
    assert resp.get_json()["success"] is True

    project_resp = client.get("/api/projects/P")
    assert chat_id not in project_resp.get_json()["chats"]


def test_rename_chat(client, tmp_path, monkeypatch):
    chat_id = _create_project_and_chat(client, monkeypatch, tmp_path)
    resp = client.post(f"/api/chats/{chat_id}/rename", json={"project": "P", "name": "Renamed"})
    assert resp.get_json()["success"] is True
    project_resp = client.get("/api/projects/P")
    assert project_resp.get_json()["chats"][chat_id]["name"] == "Renamed"


def test_append_to_missing_chat_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client.post("/api/projects", json={"name": "P"})
    resp = client.post(
        "/api/chats/nope/append", json={"project": "P", "role": "user", "content": "hi"}
    )
    assert resp.status_code == 404


def test_link_artifact_to_message_by_index(client, tmp_path, monkeypatch):
    chat_id = _create_project_and_chat(client, monkeypatch, tmp_path)
    client.post(f"/api/chats/{chat_id}/append", json={"project": "P", "role": "user", "content": "first"})
    client.post(f"/api/chats/{chat_id}/append", json={"project": "P", "role": "assistant", "content": "second"})
    pid = projects_repo.get_id("P")
    artefacts_repo.upsert("art_1", pid, "Artefact")

    resp = client.post(
        f"/api/chats/{chat_id}/link-artifact",
        json={"project": "P", "index": 1, "artifact_id": "art_1"},
    )
    assert resp.get_json()["success"] is True

    project_resp = client.get("/api/projects/P")
    messages = project_resp.get_json()["chats"][chat_id]["messages"]
    assert messages[1]["artifact_id"] == "art_1"


def test_link_artifact_out_of_range_returns_400(client, tmp_path, monkeypatch):
    chat_id = _create_project_and_chat(client, monkeypatch, tmp_path)
    client.post(f"/api/chats/{chat_id}/append", json={"project": "P", "role": "user", "content": "only"})
    resp = client.post(
        f"/api/chats/{chat_id}/link-artifact",
        json={"project": "P", "index": 5, "artifact_id": "art_1"},
    )
    assert resp.status_code == 400
