from types import SimpleNamespace

import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
from services import llm_service, research_execution_service, research_task_service, supervisor_service

GOOD_PROMPT = "Research the fee structures of every online Psychology postgraduate program."


def _no_openai(*args, **kwargs):
    raise AssertionError("tests must not call OpenAI")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: "ROLE: analyst. A complete drafted research prompt for this card.")
    monkeypatch.setattr(research_execution_service.openai_service, "retrieve_deep_research", _no_openai)
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            c.post("/api/projects", json={"name": "P"})
            yield c
    set_database_path(None)


def _card(status="PROPOSED", prompt=GOOD_PROMPT, project="P"):
    pid = projects_repo.get_or_create_id(project)
    card_id = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB", prompt_text=prompt)
    if status != "PROPOSED":
        research_work_items_repo.update_fields(card_id, status=status)
    return card_id


def _status(card_id):
    return research_work_items_repo.get(card_id)["status"]


def test_board_needs_an_existing_project(client):
    assert client.get("/api/board?project=Nope").status_code == 400


def test_get_board(client):
    _card()
    body = client.get("/api/board?project=P").get_json()
    assert body["success"] is True
    assert [p["key"] for p in body["board"]["phases"]] == ["1", "2", "3", "4", "5", "6", "7"]
    assert body["board"]["cards"][0]["title"] == "Fees"


def test_add_edit_and_approve(client):
    resp = client.post("/api/board/cards", json={"project": "P", "phase_key": "4", "title": "Fees",
                                                 "research_method": "TARGETED_WEB", "draft_prompt": True})
    card_id = resp.get_json()["card_id"]
    assert resp.status_code == 200
    assert client.patch(f"/api/board/cards/{card_id}", json={"project": "P", "title": "Fee structures"}).status_code == 200
    resp = client.post(f"/api/board/cards/{card_id}/approve", json={"project": "P"})
    assert resp.status_code == 200
    card = next(c for c in resp.get_json()["board"]["cards"] if c["id"] == card_id)
    assert (card["title"], card["status"]) == ("Fee structures", "READY")


def test_approve_validation_is_a_400_with_a_readable_message(client):
    card_id = _card(prompt="Too short")
    resp = client.post(f"/api/board/cards/{card_id}/approve", json={"project": "P"})
    assert resp.status_code == 400
    assert "too short" in resp.get_json()["error"]


def test_wrong_state_is_a_409(client):
    card_id = _card(status="COMPLETE")
    assert client.post(f"/api/board/cards/{card_id}/restore", json={"project": "P"}).status_code == 409


def test_unknown_action_and_foreign_card_are_404(client):
    card_id = _card()
    assert client.post(f"/api/board/cards/{card_id}/launch", json={"project": "P"}).status_code == 404
    client.post("/api/projects", json={"name": "Other"})
    foreign = _card(project="Other")
    assert client.post(f"/api/board/cards/{foreign}/approve", json={"project": "P"}).status_code == 404


def test_redraft_unavailable_is_a_503(client, monkeypatch):
    card_id = _card()

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(llm_service, "prompt_completion", boom)
    assert client.post(f"/api/board/cards/{card_id}/redraft", json={"project": "P"}).status_code == 503


def test_run_starts_only_approved_cards(client, monkeypatch):
    monkeypatch.setattr(research_execution_service.openai_service, "start_deep_research",
                        lambda prompt: SimpleNamespace(id="resp_1", status="queued"))
    ready = _card(status="READY")
    draft = _card()
    resp = client.post("/api/board/run", json={"project": "P", "card_ids": [ready, draft]})
    body = resp.get_json()
    assert body["result"]["started"] == [ready]
    assert body["result"]["not_ready"] == [draft]
    assert _status(ready) == "RUNNING"


def test_run_needs_a_list_of_ids(client):
    assert client.post("/api/board/run", json={"project": "P", "card_ids": "all"}).status_code == 400


def test_report(client):
    card_id = _card(status="COMPLETE")
    assert client.get(f"/api/board/cards/{card_id}/report?project=P").status_code == 404
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, None, None, "p")
    research_runs_repo.update("run_1", research_work_item_id=card_id, status="completed", output_text="The report.")
    body = client.get(f"/api/board/cards/{card_id}/report?project=P").get_json()
    assert body["report"]["text"] == "The report."


class _Block:
    def __init__(self, name, input):
        self.type, self.name, self.input = "tool_use", name, input


def test_draft_creates_cards_and_explains_itself(client, monkeypatch):
    block = _Block("create_research_task", {"reason": "Empty", "tasks": [
        {"phase_key": "1", "title": "Landscape", "research_method": "TARGETED_WEB", "rationale": "Nothing yet"}]})
    fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[block])))
    monkeypatch.setattr(supervisor_service.anthropic, "Anthropic", lambda api_key: fake)
    body = client.post("/api/board/draft", json={"project": "P"}).get_json()
    assert body["success"] is True
    assert body["note"].startswith("Drafted 1 new research")
    assert body["board"]["cards"][0]["status"] == "PROPOSED"


def test_options_report_draft_is_409_while_locked(client):
    assert client.post("/api/board/phase7/draft", json={"project": "P"}).status_code == 409


def test_objective_save(client):
    body = client.put("/api/board/objective", json={"project": "P", "objective": "Assess the MBA market."}).get_json()
    assert body["board"]["objective"] == "Assess the MBA market."


def test_refresh(client):
    body = client.post("/api/board/refresh", json={"project": "P", "defer_linking": True}).get_json()
    assert body["success"] is True
    assert body["board"]["linked_reports"] == 0


def test_run_starts_more_than_twenty_cards(client, monkeypatch):
    calls = []
    monkeypatch.setattr(research_execution_service.openai_service, "start_deep_research",
                        lambda prompt: calls.append(prompt) or SimpleNamespace(id=f"resp_{len(calls)}", status="queued"))
    ids = [_card(status="READY") for _ in range(21)]
    resp = client.post("/api/board/run", json={"project": "P", "card_ids": ids})
    assert resp.status_code == 200
    assert resp.get_json()["result"]["started"] == ids
