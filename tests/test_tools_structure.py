import json
import re
from pathlib import Path

import pytest

from db.repositories import projects_repo, research_work_items_repo
from services import supervisor_service, tools

TOOLS_DIR = Path(__file__).resolve().parents[1] / "services" / "tools"

EXPECTED_CALLERS = {
    "get_project_state": {"supervisor", "user", "system"},
    "get_task": {"supervisor", "user", "system"},
    "create_research_task": {"supervisor", "user"},
    "create_followup_task": {"supervisor", "user"},
    "request_human_review": {"supervisor"},
    "evaluate_research_output": {"supervisor"},
    "no_action": {"supervisor"},
    "update_task_status": {"user", "system"},
    "launch_deep_research": {"user"},
    "check_research_run": {"system"},
    "generate_synthesis": {"user"},
}


def test_the_registry_is_exactly_the_phase_4a_tools_with_the_agreed_callers():
    registered = {name: set(t.callers) for name, t in tools.all_tools().items() if not name.startswith("test_")}
    assert registered == EXPECTED_CALLERS


def test_the_supervisor_menus_only_hold_tools_it_may_call():
    for name in supervisor_service.DRAFTING_MENU + supervisor_service.REVIEW_MENU:
        assert "supervisor" in tools.get_tool(name).callers


@pytest.mark.parametrize("name", sorted(n for n, c in EXPECTED_CALLERS.items() if "supervisor" not in c))
def test_every_other_tool_refuses_the_supervisor(temp_db, name):
    pid = projects_repo.get_or_create_id("P")
    card = research_work_items_repo.create(pid, "4", "Card")
    inputs = {"task_ids": [card]} if name == "launch_deep_research" else {"task_id": card, "action": "approve"}
    result = tools.run_tool(name, "supervisor", "P", inputs)
    assert result.error["code"] == "not_allowed"
    assert research_work_items_repo.get(card)["status"] == "PROPOSED"


FORBIDDEN_IMPORTS = re.compile(r"^\s*(from|import)\s+(db\.connection|sqlite3|os|pathlib|shutil)\b|from \.\.\.?db\.connection", re.M)


@pytest.mark.parametrize("path", sorted(TOOLS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_tool_modules_cannot_touch_the_database_or_files_directly(path):
    source = path.read_text(encoding="utf-8")
    assert not FORBIDDEN_IMPORTS.search(source), f"{path.name} imports a forbidden module"
    assert not re.search(r"(?<![\w.])open\(", source), f"{path.name} calls open("


FORBIDDEN_FIELDS = re.compile(r"(path|file|filename|sql|query|table)", re.I)


def _property_names(schema):
    names = []
    if isinstance(schema, dict):
        for key, value in schema.get("properties", {}).items():
            names.append(key)
            names.extend(_property_names(value))
        for value in schema.values():
            if isinstance(value, (dict, list)):
                names.extend(_property_names(value))
    elif isinstance(schema, list):
        for value in schema:
            names.extend(_property_names(value))
    return names


@pytest.mark.parametrize("name", sorted(EXPECTED_CALLERS))
def test_no_tool_input_has_a_path_sql_or_table_field(name):
    schema = tools.claude_tools([name])[0]["input_schema"]
    assert [n for n in _property_names(schema) if FORBIDDEN_FIELDS.search(n)] == []
    assert "$ref" not in json.dumps(schema)
