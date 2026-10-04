import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import projects_repo, research_work_items_repo


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


class _FakeToolUseBlock:
    def __init__(self, name, input):
        self.type = "tool_use"
        self.name = name
        self.input = input


class _FakeAnthropicResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


def _patch_anthropic(monkeypatch, tool_name, tool_input):
    from services import supervisor_service
    response = _FakeAnthropicResponse([_FakeToolUseBlock(tool_name, tool_input)])
    monkeypatch.setattr(
        supervisor_service.anthropic, "Anthropic",
        lambda api_key: _FakeAnthropicClient(response),
    )


def test_run_supervisor_route(client, monkeypatch):
    client.post("/api/projects", json={"name": "P"})
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    _patch_anthropic(monkeypatch, "mark_ready", {"task_id": task_id, "reason": "ready"})

    resp = client.post("/api/supervisor/run", json={"project": "P"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["decision"]["action"] == "mark_ready"
    assert body["execution"]["success"] is True


def test_run_supervisor_route_requires_project(client):
    resp = client.post("/api/supervisor/run", json={})
    assert resp.status_code == 400


def test_run_supervisor_route_illegal_transition_returns_200_with_failed_execution(client, monkeypatch):
    client.post("/api/projects", json={"name": "P"})
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="COMPLETE")
    _patch_anthropic(monkeypatch, "mark_ready", {"task_id": task_id, "reason": "trying anyway"})

    resp = client.post("/api/supervisor/run", json={"project": "P"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["execution"]["success"] is False
    assert "error" in body["execution"]


def test_list_decisions_route(client, monkeypatch):
    client.post("/api/projects", json={"name": "P"})
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    _patch_anthropic(monkeypatch, "mark_ready", {"task_id": task_id, "reason": "ready"})
    client.post("/api/supervisor/run", json={"project": "P"})

    resp = client.get("/api/supervisor/decisions", query_string={"project": "P"})
    assert resp.status_code == 200
    decisions = resp.get_json()["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["decision_type"] == "mark_ready"
    assert decisions[0]["detail"]["input"]["task_id"] == task_id
