import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import projects_repo, research_work_items_repo
from services import research_task_service


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


def test_list_tasks_returns_a_current_summary_for_every_phase(client):
    # The page redraws each phase's "N of M tasks complete" line from this after every
    # change; without it the line stays frozen at whatever it said when the page loaded.
    _create_project(client)
    done_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"}).get_json()["id"]
    client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "B"})
    for status in ("READY", "RUNNING", "COMPLETE"):
        research_task_service.transition_task(done_id, status)

    summaries = client.get("/api/research-tasks", query_string={"project": "P"}).get_json()["phase_summaries"]

    assert sorted(summaries) == ["1", "2", "3", "4", "5", "6", "7"]
    assert summaries["4"]["status_counts"] == {"COMPLETE": 1, "PROPOSED": 1}
    assert summaries["1"]["status_counts"] == {}  # a phase with no tasks is present, and empty


def test_add_and_remove_dependency_routes(client):
    _create_project(client)
    a_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"}).get_json()["id"]
    b_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "B"}).get_json()["id"]

    resp = client.post(f"/api/research-tasks/{a_id}/dependencies", json={"depends_on_task_id": b_id})
    assert resp.get_json()["success"] is True

    # A depends on B, which is still PROPOSED -- A cannot go READY yet.
    with pytest.raises(ValueError):
        research_task_service.transition_task(a_id, "READY")

    del_resp = client.delete(f"/api/research-tasks/{a_id}/dependencies/{b_id}")
    assert del_resp.get_json()["success"] is True

    # Dependency removed -- A can go READY now.
    research_task_service.transition_task(a_id, "READY")
    assert research_work_items_repo.get(a_id)["status"] == "READY"


def test_add_dependency_cycle_returns_400(client):
    _create_project(client)
    a_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"}).get_json()["id"]
    b_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "B"}).get_json()["id"]
    client.post(f"/api/research-tasks/{a_id}/dependencies", json={"depends_on_task_id": b_id})

    resp = client.post(f"/api/research-tasks/{b_id}/dependencies", json={"depends_on_task_id": a_id})
    assert resp.status_code == 400


def test_generic_transition_endpoint_is_gone(client):
    client.post("/api/projects", json={"name": "P"})
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    resp = client.post(f"/api/research-tasks/{task_id}/transition", json={"project": "P", "status": "RUNNING"})
    assert resp.status_code in (404, 405)
    assert research_work_items_repo.get(task_id)["status"] == "PROPOSED"
