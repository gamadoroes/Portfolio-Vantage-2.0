from types import SimpleNamespace

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo
from services import board_service, research_execution_service, research_task_service, tools

PROMPT = "Research the fee structures of every online Psychology postgraduate program in Australia."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(research_execution_service, "_spawn", lambda target, *args: target(*args))
    return projects_repo.get_or_create_id("P")


def _ready(pid, method="TARGETED_WEB", phase="4"):
    card = research_task_service.create_task(pid, phase, "Card", research_method=method, prompt_text=PROMPT)
    research_work_items_repo.update_fields(card, status="READY")
    return card


def _fake_openai(monkeypatch):
    sent = []
    monkeypatch.setattr(research_execution_service.openai_service, "start_deep_research",
                        lambda prompt: sent.append(prompt) or SimpleNamespace(id=f"resp_{len(sent)}", status="queued"))
    return sent


def test_launch_starts_approved_web_research(pid, monkeypatch):
    sent = _fake_openai(monkeypatch)
    card = _ready(pid)
    result = tools.run_tool("launch_deep_research", "user", "P", {"task_ids": [card]})
    assert result.data == {"started": [card], "not_ready": [], "failed": []}
    assert sent == [PROMPT]


def test_launch_refuses_the_options_report(pid, monkeypatch):
    _fake_openai(monkeypatch)
    card = _ready(pid, method="SYNTHESIS", phase="7")
    data = tools.run_tool("launch_deep_research", "user", "P", {"task_ids": [card]}).data
    assert data["failed"][0]["id"] == card and research_work_items_repo.get(card)["status"] == "READY"


@pytest.mark.parametrize("name,inputs", [("launch_deep_research", {"task_ids": [1]}), ("generate_synthesis", {"task_id": 1}),
                                         ("check_research_run", {"task_id": 1})])
def test_the_supervisor_cannot_start_or_check_runs(pid, name, inputs):
    _ready(pid)
    assert tools.run_tool(name, "supervisor", "P", inputs).error["code"] == "not_allowed"


def test_generate_synthesis_only_for_the_options_report(pid):
    card = _ready(pid)
    assert tools.run_tool("generate_synthesis", "user", "P", {"task_id": card}).error["code"] == "conflict"


def test_generate_synthesis_while_locked_reports_failed(pid):
    card = _ready(pid, method="SYNTHESIS", phase="7")
    data = tools.run_tool("generate_synthesis", "user", "P", {"task_id": card}).data
    assert data["failed"][0]["id"] == card


def test_check_research_run_collects_a_finished_web_report(pid, monkeypatch):
    card = _ready(pid)
    research_work_items_repo.update_fields(card, status="RUNNING")
    research_runs_repo.create("run_1", pid, "resp_1", None, "p")
    research_runs_repo.update("run_1", research_work_item_id=card, status="running")
    text = "The report."
    content = [SimpleNamespace(type="output_text", text=text, annotations=[])]
    monkeypatch.setattr(research_execution_service.openai_service, "retrieve_deep_research",
                        lambda rid: SimpleNamespace(id=rid, status="completed", output_text=text,
                                                    output=[SimpleNamespace(type="message", content=content)],
                                                    error=None, last_error=None))
    data = tools.run_tool("check_research_run", "system", "P", {"task_id": card}).data
    assert data == {"task_id": card, "run_status": "completed"}
    assert research_runs_repo.get("run_1")["output_text"].endswith("The report.")


def test_check_research_run_without_a_run(pid):
    assert tools.run_tool("check_research_run", "system", "P", {"task_id": _ready(pid)}).error["code"] == "not_found"


def test_board_run_routes_each_card_to_the_right_tool(pid, monkeypatch):
    _fake_openai(monkeypatch)
    web = _ready(pid)
    synth = _ready(pid, method="SYNTHESIS", phase="7")
    result = board_service.run("P", [web, synth])
    assert result["started"] == [web]
    assert [f["id"] for f in result["failed"]] == [synth]  # Phase 7 is still locked


def test_board_run_treats_an_unknown_card_as_not_ready(pid, monkeypatch):
    _fake_openai(monkeypatch)
    card = _ready(pid)
    result = board_service.run("P", [99999, card])
    assert result == {"started": [card], "not_ready": [99999], "failed": []}


def test_board_run_starts_more_than_twenty_cards(pid, monkeypatch):
    _fake_openai(monkeypatch)
    cards = [_ready(pid) for _ in range(21)]
    assert board_service.run("P", cards)["started"] == cards


def test_board_run_keeps_started_runs_when_a_later_part_fails(pid, monkeypatch):
    _fake_openai(monkeypatch)
    web = _ready(pid)
    synth = _ready(pid, method="SYNTHESIS", phase="7")
    real = tools.run_tool

    def flaky(name, *args, **kwargs):
        if name == "generate_synthesis":
            return tools.ToolResult(ok=False, error={"code": "internal", "message": "boom", "fields": []})
        return real(name, *args, **kwargs)

    monkeypatch.setattr(board_service, "run_tool", flaky)
    result = board_service.run("P", [web, synth])
    assert result == {"started": [web], "not_ready": [], "failed": [{"id": synth, "error": "boom"}]}
    assert any(d["decision_type"] == "user_run" for d in agent_decisions_repo.list_for_project(pid, limit=10))
