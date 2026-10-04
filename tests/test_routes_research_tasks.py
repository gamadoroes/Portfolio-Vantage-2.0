import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations


@pytest.fixture
def client(tmp_path, monkeypatch):
    # services.project_service.create_project() writes real directories
    # relative to the process cwd -- chdir into the test's tmp_path so those
    # filesystem side effects don't leak into the repo (see test_routes_chats.py).
    monkeypatch.chdir(tmp_path)
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            yield c
    set_database_path(None)


def _create_project(client, name="P"):
    client.post("/api/projects", json={"name": name})


def test_create_task_route(client):
    _create_project(client)
    resp = client.post("/api/research-tasks", json={
        "project": "P", "phase_key": "4", "title": "Product / La Trobe",
        "entities": ["La Trobe"], "priority": "high",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert isinstance(body["id"], int)


def test_create_task_requires_title(client):
    _create_project(client)
    resp = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": ""})
    assert resp.status_code == 400


def test_create_task_rejects_invalid_priority(client):
    _create_project(client)
    resp = client.post("/api/research-tasks", json={
        "project": "P", "phase_key": "4", "title": "Task", "priority": "urgent",
    })
    assert resp.status_code == 400


def test_list_tasks_filters_by_phase(client):
    _create_project(client)
    client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"})
    client.post("/api/research-tasks", json={"project": "P", "phase_key": "1", "title": "B"})

    resp = client.get("/api/research-tasks", query_string={"project": "P", "phase_key": "4"})
    tasks = resp.get_json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "A"
    assert tasks[0]["entities"] == []

    resp_all = client.get("/api/research-tasks", query_string={"project": "P"})
    assert len(resp_all.get_json()["tasks"]) == 2


def test_list_tasks_serializes_json_array_fields(client):
    _create_project(client)
    client.post("/api/research-tasks", json={
        "project": "P", "phase_key": "4", "title": "A", "entities": ["La Trobe", "Torrens"],
    })
    resp = client.get("/api/research-tasks", query_string={"project": "P"})
    assert resp.get_json()["tasks"][0]["entities"] == ["La Trobe", "Torrens"]


def test_transition_task_route(client):
    _create_project(client)
    create_resp = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"})
    task_id = create_resp.get_json()["id"]

    resp = client.post(f"/api/research-tasks/{task_id}/transition", json={"status": "READY"})
    assert resp.get_json()["success"] is True

    list_resp = client.get("/api/research-tasks", query_string={"project": "P"})
    assert list_resp.get_json()["tasks"][0]["status"] == "READY"


def test_transition_task_illegal_returns_400_with_error(client):
    _create_project(client)
    create_resp = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"})
    task_id = create_resp.get_json()["id"]

    resp = client.post(f"/api/research-tasks/{task_id}/transition", json={"status": "COMPLETE"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_add_and_remove_dependency_routes(client):
    _create_project(client)
    a_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"}).get_json()["id"]
    b_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "B"}).get_json()["id"]

    resp = client.post(f"/api/research-tasks/{a_id}/dependencies", json={"depends_on_task_id": b_id})
    assert resp.get_json()["success"] is True

    # A depends on B, which is still PROPOSED -- A cannot go READY yet.
    blocked = client.post(f"/api/research-tasks/{a_id}/transition", json={"status": "READY"})
    assert blocked.status_code == 400

    del_resp = client.delete(f"/api/research-tasks/{a_id}/dependencies/{b_id}")
    assert del_resp.get_json()["success"] is True

    # Dependency removed -- A can go READY now.
    allowed = client.post(f"/api/research-tasks/{a_id}/transition", json={"status": "READY"})
    assert allowed.get_json()["success"] is True


def test_add_dependency_cycle_returns_400(client):
    _create_project(client)
    a_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"}).get_json()["id"]
    b_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "B"}).get_json()["id"]
    client.post(f"/api/research-tasks/{a_id}/dependencies", json={"depends_on_task_id": b_id})

    resp = client.post(f"/api/research-tasks/{b_id}/dependencies", json={"depends_on_task_id": a_id})
    assert resp.status_code == 400
