import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import agent_decisions_repo, projects_repo


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            yield c
    set_database_path(None)


def test_run_supervisor_route_is_retired(client):
    client.post("/api/projects", json={"name": "P"})
    resp = client.post("/api/supervisor/run", json={"project": "P"})
    assert resp.status_code == 410
    assert "Research tab" in resp.get_json()["error"]


def test_list_decisions_route(client):
    client.post("/api/projects", json={"name": "P"})
    pid = projects_repo.get_or_create_id("P")
    agent_decisions_repo.record(pid, "propose_tasks", '{"input": {"reason": "No research yet"}}')

    resp = client.get("/api/supervisor/decisions", query_string={"project": "P"})
    assert resp.status_code == 200
    decisions = resp.get_json()["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["decision_type"] == "propose_tasks"
    assert decisions[0]["detail"]["input"]["reason"] == "No research yet"
