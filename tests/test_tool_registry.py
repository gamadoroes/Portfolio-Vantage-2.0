import json

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from db.repositories import projects_repo, research_work_items_repo, tool_calls_repo
from services import tools
from services.tools import registry
from services.tools.types import Text, Title, ToolInput, text_list


class _Nested(BaseModel):
    title: str
    focus: text_list(5, 40) = []


class _EchoInput(ToolInput):
    task_id: int | None = None
    title: Title | None = None
    note: Text(10) = ""
    items: text_list(5, 40) = []
    nested: _Nested | None = None
    behaviour: str = "echo"


def _handler(ctx, inputs):
    if inputs.behaviour == "conflict":
        raise tools.ToolError("conflict", "That card has moved on.")
    if inputs.behaviour == "value":
        raise ValueError("Cannot transition from COMPLETE to READY")
    if inputs.behaviour == "boom":
        raise RuntimeError("kaboom secret detail")
    if inputs.behaviour == "api":
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    if inputs.behaviour == "child":
        child = tools.run_tool("test_echo", ctx.caller, ctx.project_name, {"note": "kid"}, parent_call_id=ctx.call_id)
        return {"child_call_id": child.call_id}
    return {"caller": ctx.caller, "items": inputs.items, "note": inputs.note}


@pytest.fixture
def echo(temp_db):
    tool = tools.register(tools.Tool(
        name="test_echo", description="Echo for tests.", input_model=_EchoInput, handler=_handler,
        callers=frozenset({"user", "system"}), id_fields=("task_id",),
    ))
    yield tool
    registry._REGISTRY.pop("test_echo", None)


@pytest.fixture
def pid(echo):
    return projects_repo.get_or_create_id("P")


def _last_log(pid):
    return tool_calls_repo.list_for_project(pid)[0]


def test_success_returns_data_and_is_logged(pid):
    card = research_work_items_repo.create(pid, "4", "Card")
    result = tools.run_tool("test_echo", "user", "P", {"task_id": card, "note": "hi"})
    assert result.ok and result.data["caller"] == "user" and result.data["note"] == "hi"
    log = _last_log(pid)
    assert (log["tool"], log["caller"], log["ok"], log["research_work_item_id"]) == ("test_echo", "user", 1, card)
    assert log["duration_ms"] is not None and json.loads(log["input_json"])["note"] == "hi"


def test_unknown_tool_is_refused_and_logged(pid):
    result = tools.run_tool("drop_tables", "user", "P", {})
    assert (result.ok, result.error["code"]) == (False, "not_allowed")
    assert _last_log(pid)["tool"] == "drop_tables" and _last_log(pid)["ok"] == 0


@pytest.mark.parametrize("caller", ["supervisor", "hacker"])
def test_caller_not_allowed_is_refused_and_handler_not_run(pid, caller, monkeypatch):
    ran = []
    monkeypatch.setattr(registry._REGISTRY["test_echo"], "handler", lambda ctx, i: ran.append(1) or {}, raising=False)
    result = tools.run_tool("test_echo", caller, "P", {})
    assert result.error["code"] == "not_allowed" and ran == []
    assert _last_log(pid)["error_code"] == "not_allowed"


def test_invalid_input_names_the_field(pid):
    result = tools.run_tool("test_echo", "user", "P", {"note": "x" * 50})
    assert result.error["code"] == "invalid_input"
    assert "note" in result.error["fields"] and "note" in result.error["message"]


def test_extra_fields_are_ignored(pid):
    assert tools.run_tool("test_echo", "user", "P", {"note": "ok", "surprise": 1}).ok


def test_a_string_is_accepted_for_a_list_field(pid):
    result = tools.run_tool("test_echo", "user", "P", {"items": "\n- First\n* Second\n"})
    assert result.data["items"] == ["First", "Second"]


@pytest.mark.parametrize("make_card", [lambda: 99999, lambda: research_work_items_repo.create(projects_repo.get_or_create_id("Other"), "4", "Theirs")])
def test_card_from_another_project_or_missing_is_not_found(pid, make_card):
    result = tools.run_tool("test_echo", "user", "P", {"task_id": make_card()})
    assert result.error["code"] == "not_found"
    assert _last_log(pid)["research_work_item_id"] is None


def test_unknown_project_is_not_found(echo):
    assert tools.run_tool("test_echo", "user", "Nope", {}).error["code"] == "not_found"


def test_tool_error_keeps_code_and_message(pid):
    result = tools.run_tool("test_echo", "user", "P", {"behaviour": "conflict"})
    assert result.error == {"code": "conflict", "message": "That card has moved on.", "fields": []}


def test_value_error_from_a_service_is_a_conflict(pid):
    assert tools.run_tool("test_echo", "user", "P", {"behaviour": "value"}).error["code"] == "conflict"


def test_unexpected_exception_is_internal_with_details_only_in_the_log(pid):
    result = tools.run_tool("test_echo", "user", "P", {"behaviour": "boom"})
    assert result.error["code"] == "internal"
    assert result.error["message"] == "Something went wrong running test_echo."
    assert "kaboom" not in result.error["message"]
    assert "RuntimeError" in _last_log(pid)["error_message"]


def test_outside_service_error_is_unavailable(pid):
    assert tools.run_tool("test_echo", "user", "P", {"behaviour": "api"}).error["code"] == "unavailable"


def test_child_calls_record_their_parent(pid):
    parent = tools.run_tool("test_echo", "user", "P", {"behaviour": "child"})
    child = tool_calls_repo.get(parent.data["child_call_id"])
    assert child["parent_call_id"] == parent.call_id


def test_clip_for_log_limits_strings_and_documents():
    one = json.loads(tools.clip_for_log({"a": "x" * 2000}))
    assert len(one["a"]) < 600 and one["a"].endswith("...[clipped]")
    many = tools.clip_for_log({f"k{i}": "y" * 400 for i in range(40)})
    assert len(many) <= 4000 + len("...[clipped]")


def test_claude_schema_inlines_nested_models_and_keeps_a_title_field(echo):
    (definition,) = tools.claude_tools(["test_echo"])
    text = json.dumps(definition)
    assert "$ref" not in text and "$defs" not in text
    props = definition["input_schema"]["properties"]
    assert "title" in props                      # a field literally named "title" survives
    assert '"title"' in json.dumps(props["nested"])  # and so does the nested model's title field
    assert definition["name"] == "test_echo" and definition["description"] == "Echo for tests."
