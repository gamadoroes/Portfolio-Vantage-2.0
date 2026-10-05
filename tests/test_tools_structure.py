import ast
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
    "save_source": {"supervisor", "system"},
    "save_evidence": {"supervisor", "system"},
    "create_finding": {"supervisor", "system"},
    "record_research_facts": {"supervisor"},
    "extract_research_facts": {"user", "system"},
    "search_existing_evidence": {"supervisor", "user", "system"},
    "update_evidence_status": {"user"},
}


def test_the_registry_is_exactly_the_agreed_tools_with_the_agreed_callers():
    registered = {name: set(t.callers) for name, t in tools.all_tools().items() if not name.startswith("test_")}
    assert registered == EXPECTED_CALLERS


SUPERVISOR_MENUS = ("DRAFTING_MENU", "REVIEW_MENU", "EXTRACT_MENU")


def _menu_tools():
    return [name for menu in SUPERVISOR_MENUS for name in getattr(supervisor_service, menu)]


def test_the_supervisor_menus_only_hold_tools_it_may_call():
    for name in _menu_tools():
        assert "supervisor" in tools.get_tool(name).callers


def test_the_supervisor_saves_facts_only_through_record_research_facts():
    assert supervisor_service.EXTRACT_MENU == ("record_research_facts",)
    assert set(_menu_tools()).isdisjoint({"save_source", "save_evidence", "create_finding",
                                          "update_evidence_status", "extract_research_facts"})


REVIEW_INPUT_FIELDS = {"task_id", "completeness_score", "evidence_score", "identified_gaps", "outcome", "reason", "followup"}


def test_the_review_menu_is_unchanged_and_the_review_has_no_fact_fields():
    assert supervisor_service.REVIEW_MENU == ("evaluate_research_output",)
    props = tools.claude_tools(["evaluate_research_output"])[0]["input_schema"]["properties"]
    assert set(props) == REVIEW_INPUT_FIELDS  # the exact set: any added field (facts or otherwise) fails here


@pytest.mark.parametrize("name,caller", [
    ("update_evidence_status", "supervisor"), ("update_evidence_status", "system"),
    ("extract_research_facts", "supervisor"),  # the system may extract (after a review); the Supervisor never
])
def test_the_supervisor_cannot_reject_restore_or_start_a_paid_extraction(temp_db, name, caller):
    pid = projects_repo.get_or_create_id("P")
    card = research_work_items_repo.create(pid, "4", "Card")
    inputs = {"kind": "fact", "id": 1, "action": "reject"} if name == "update_evidence_status" else {"task_id": card}
    assert tools.run_tool(name, caller, "P", inputs).error["code"] == "not_allowed"


@pytest.mark.parametrize("name", sorted(n for n, c in EXPECTED_CALLERS.items() if "supervisor" not in c))
def test_every_other_tool_refuses_the_supervisor(temp_db, name):
    pid = projects_repo.get_or_create_id("P")
    card = research_work_items_repo.create(pid, "4", "Card")
    inputs = {"task_ids": [card]} if name == "launch_deep_research" else {"task_id": card, "action": "approve"}
    result = tools.run_tool(name, "supervisor", "P", inputs)
    assert result.error["code"] == "not_allowed"
    assert research_work_items_repo.get(card)["status"] == "PROPOSED"


FORBIDDEN_TOP_LEVEL = {"os", "shutil", "sqlite3", "subprocess", "pathlib", "importlib", "io", "builtins"}


def _forbidden_module(module, names=()):
    """True if `from <module> import <names>` (or `import <module>`) reaches the database connection or files."""
    parts = module.split(".") if module else []
    if parts[:1] and parts[0] in FORBIDDEN_TOP_LEVEL:
        return True
    if "db" in parts and "connection" in parts[parts.index("db"):]:
        return True
    return parts[-1:] == ["db"] and "connection" in names


def forbidden_things(source):
    """Everything in a module's source that lets it reach the database connection or the file system directly."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if _forbidden_module(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if _forbidden_module(node.module or "", [a.name for a in node.names]):
                found.append(f"from {'.' * node.level}{node.module or ''} import ...")
        elif isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Name) and callee.id in ("open", "__import__"):
                found.append(f"{callee.id}(...)")
            elif (isinstance(callee, ast.Attribute) and callee.attr == "open"
                  and isinstance(callee.value, ast.Name) and callee.value.id in ("io", "builtins", "os")):
                found.append(f"{callee.value.id}.open(...)")
    return found


@pytest.mark.parametrize("path", sorted(TOOLS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_tool_modules_cannot_touch_the_database_or_files_directly(path):
    assert forbidden_things(path.read_text(encoding="utf-8")) == [], f"{path.name} reaches the database or files directly"


@pytest.mark.parametrize("source", [
    "import json, os", "import os.path", "from db import connection", "from db.connection import get_connection",
    "from ..db.connection import x", "from .. import db; from ...db import connection as c", "import sqlite3",
    "from pathlib import Path", "import shutil as sh", "import subprocess", "io.open('x')", "builtins.open('x')",
    "os.open('x', 0)", "open('x')", "__import__('os')",
])
def test_the_structure_check_catches_forbidden_access(source):
    assert forbidden_things(source), source


@pytest.mark.parametrize("source", [
    "import json", "import osmosis", "from db.repositories import projects_repo", "from .. import research_task_service",
    "from .registry import Tool", "x = reopen(1); self.open_door()",
])
def test_the_structure_check_allows_harmless_code(source):
    assert forbidden_things(source) == [], source


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
