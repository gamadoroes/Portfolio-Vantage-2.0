# Phase 4a: Supervisor Tool Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every Supervisor action, and the board buttons and refresh steps on Phase 4's list, through one registry of explicit, validated, logged tools with per-tool caller lists. The Supervisor can only act through the tools on its menus.

**Architecture:** A new package `services/tools/` holds the registry and `run_tool()` (the single door), the tool definitions (pydantic inputs, structured results, caller lists) and their handlers. Handlers call the existing services, never the database connection or files. `supervisor_service` builds its briefings from the read tools and executes the model's choice through `run_tool(caller="supervisor")`. `board_service` keeps its public functions, but the ones on Phase 4's list become thin wrappers that call `run_tool(caller="user")` and turn failures back into the board's existing exceptions. The board's routes, HTTP codes and screen therefore don't change. Every call is logged to a new `tool_calls` table.

**Tech Stack:** Python 3.13, Flask, SQLite, pydantic 2 (already installed, now pinned), pytest, Anthropic SDK, OpenAI SDK.

**Spec:** `docs/superpowers/specs/2026-10-05-supervisor-tool-layer-design.md`

**Refinements to the spec made while planning (deliberate):**
- `create_research_task` gains `draft_prompt: bool = True`, so the board's "write my own prompt" path (which may leave the prompt empty) keeps working. `prompt_text` has no minimum length; Approve still enforces 40 characters.
- `get_project_state` returns each card's latest run **including its report text**. `build_context` needs it to show reports that are awaiting review, exactly as today. The log clips it.
- The review-limit constants move to `services/review_limits.py`, with values unchanged, so the read tools can use them without importing the Supervisor (which would be a circular import). `docs/TO-TEST.md` is updated to point there.
- `as_text_list` and the clipping helper move to `services/text_utils.py`. `supervisor_service` re-exports both names.
- The board's "Unapprove" button maps to the `back_to_draft` action. Its Activity line therefore reads "moved back to draft", which is the same wording as before.
- Supervisor decisions keep the decision type `review_outcome` for reviews, so the Activity feed works unchanged. Drafting decisions are recorded under the tool name `create_research_task`, which the Activity feed learns to read alongside the old `propose_tasks`.

## Global Constraints

- Migrations are additive only. Never edit `0001`–`0005`. The new file is `db/migrations/0006_tool_calls.sql`.
- Do not change the values of `MAX_RUN_OUTPUT_REVIEW_CHARS = 8000`, `MAX_REVIEW_OUTPUT_TOTAL_CHARS = 24000`, `MAX_CONTEXT_CHARS = 60000`, `MAX_RUN_OUTPUT_PREVIEW_CHARS = 300` or `MAX_WEB_SOURCES_LISTED = 20`.
- The Supervisor may call only `get_project_state`, `get_task`, `create_research_task`, `create_followup_task`, `request_human_review`, `evaluate_research_output` and `no_action`. `update_task_status`, `launch_deep_research`, `check_research_run` and `generate_synthesis` must refuse it.
- Modules in `services/tools/` must not import `db.connection`, `sqlite3`, `os`, `pathlib` or `shutil`, and must not call `open(`.
- No tool input has a field for SQL, a table, a file path or a filename.
- Logged inputs and results are clipped: each string to 500 characters, each JSON document to 4,000 characters.
- Board routes, HTTP status codes, response shapes and on-screen messages stay as they are today.
- Tests never call Anthropic or OpenAI. Any test that creates a project, reads or writes `project_prompt`, or saves files calls `monkeypatch.chdir(tmp_path)` first.
- Bash on Windows: the repo path contains `!!`, so start bash commands with `set +H;` or use PowerShell.
- Ruff: no new findings, and every touched file must be clean (`python -m ruff check <file>`). Pin `pydantic==2.12.5` in `requirements_flask.txt`; add no other dependency.
- Full suite: `python -m pytest tests/ -q`. The baseline on `vantage-fresh` is 450 passed, and in a worktree 1 test is skipped. Also run `node tests/js/test_board_view.js` (20 passed).

## Review Focus

1. **The model sends list fields as a single string.** This happened in the live run. `identified_gaps` and `focus` must be coerced, never rejected (Task 2 type test, Task 5 review test).
2. **The model sends extra or unknown fields.** Pydantic must ignore them, not fail the review (Task 2: `extra="ignore"`, with a test).
3. **The schema shown to Claude.** Nested models and a property literally named `title` must survive the schema clean-up. Stripping metadata `title` keys must not delete the `title` field (Task 2 test).
4. **A card id belonging to another project, or none.** It must return `not_found` without revealing anything, and the log row must not violate the foreign key (Task 2 test).
5. **An unexpected exception inside a handler.** The user sees "Something went wrong running X"; the log keeps the exception type and message (Task 2 test).

---

### Task 1: Shared helpers, review limits, migration 0006 and the tool-call log

**Files:**
- Create: `services/text_utils.py`, `services/review_limits.py`, `db/migrations/0006_tool_calls.sql`, `db/repositories/tool_calls_repo.py`, `tests/test_repositories_tool_calls.py`
- Modify: `services/supervisor_service.py` (import the moved names), `services/board_service.py` (import `as_text_list` from `text_utils`), `docs/TO-TEST.md`, `tests/test_db_migrations.py`

**Interfaces:**
- Produces:
  - `text_utils.clip_text(text, limit) -> str`, which appends `"...[truncated]"` when clipping;
  - `text_utils.as_text_list(value) -> list[str]`;
  - `review_limits.MAX_CONTEXT_CHARS`, `MAX_RUN_OUTPUT_PREVIEW_CHARS`, `MAX_RUN_OUTPUT_REVIEW_CHARS`, `MAX_REVIEW_OUTPUT_TOTAL_CHARS`;
  - `tool_calls_repo.create(project_id, tool, caller, input_json, parent_call_id=None) -> int`;
  - `tool_calls_repo.finish(id, ok, error_code=None, error_message=None, result_json=None, duration_ms=None, research_work_item_id=None)`;
  - `tool_calls_repo.get(id)` and `tool_calls_repo.list_for_project(project_id, limit=100)` (newest first).

- [ ] **Step 1: Write the failing tests**

In `tests/test_db_migrations.py` change `assert count == 5` to `assert count == 6` and append:

```python
def test_tool_calls_table(temp_db):
    with get_connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
    assert {"id", "project_id", "tool", "caller", "research_work_item_id", "parent_call_id", "input_json",
            "ok", "error_code", "error_message", "result_json", "started_at", "duration_ms"}.issubset(cols)
```

`tests/test_repositories_tool_calls.py`:

```python
from db.repositories import projects_repo, research_work_items_repo, tool_calls_repo


def test_create_then_finish_records_the_outcome(temp_db):
    pid = projects_repo.get_or_create_id("P")
    card = research_work_items_repo.create(pid, "4", "Card")
    call_id = tool_calls_repo.create(pid, "get_task", "system", '{"task_id": 1}')
    row = tool_calls_repo.get(call_id)
    assert (row["tool"], row["caller"], row["ok"]) == ("get_task", "system", 0)
    assert row["started_at"]
    tool_calls_repo.finish(call_id, True, result_json='{"x": 1}', duration_ms=12, research_work_item_id=card)
    row = tool_calls_repo.get(call_id)
    assert (row["ok"], row["result_json"], row["duration_ms"], row["research_work_item_id"]) == (1, '{"x": 1}', 12, card)


def test_failure_and_parent_link(temp_db):
    pid = projects_repo.get_or_create_id("P")
    parent = tool_calls_repo.create(pid, "evaluate_research_output", "supervisor", "{}")
    child = tool_calls_repo.create(pid, "create_followup_task", "supervisor", "{}", parent_call_id=parent)
    tool_calls_repo.finish(child, False, error_code="conflict", error_message="Wrong state")
    row = tool_calls_repo.get(child)
    assert (row["parent_call_id"], row["ok"], row["error_code"], row["error_message"]) == (parent, 0, "conflict", "Wrong state")


def test_list_for_project_is_newest_first_and_scoped(temp_db):
    pid = projects_repo.get_or_create_id("P")
    other = projects_repo.get_or_create_id("Other")
    first = tool_calls_repo.create(pid, "a", "user", "{}")
    second = tool_calls_repo.create(pid, "b", "user", "{}")
    tool_calls_repo.create(other, "c", "user", "{}")
    assert [r["id"] for r in tool_calls_repo.list_for_project(pid)] == [second, first]
```

Append to `tests/test_supervisor_service.py` (an import check that the helpers moved without changing behaviour):

```python
def test_helpers_and_limits_moved_without_changing_values():
    from services import review_limits, text_utils
    assert supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS == review_limits.MAX_RUN_OUTPUT_REVIEW_CHARS == 8000
    assert supervisor_service.MAX_REVIEW_OUTPUT_TOTAL_CHARS == 24000
    assert supervisor_service.MAX_CONTEXT_CHARS == 60000
    assert supervisor_service.MAX_RUN_OUTPUT_PREVIEW_CHARS == 300
    assert supervisor_service.as_text_list is text_utils.as_text_list
    assert text_utils.clip_text("abcdef", 3) == "abc...[truncated]"
    assert text_utils.clip_text("abc", 3) == "abc"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_db_migrations.py tests/test_repositories_tool_calls.py tests/test_supervisor_service.py::test_helpers_and_limits_moved_without_changing_values -v`
Expected: FAIL (count is 5, there's no `tool_calls` table, and the modules don't exist).

- [ ] **Step 3: Implement**

`services/text_utils.py`. Move `as_text_list` here **verbatim** from `services/supervisor_service.py`, and add `clip_text`, which has the same body as today's `_clip_run_output`:

```python
# services/text_utils.py
"""Small text helpers shared by the Supervisor, the board and the tools."""


def clip_text(text, limit):
    text = text if isinstance(text, str) else str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def as_text_list(value):
    """A list of non-empty strings from a field that should be a list.

    The tool schemas ask for arrays, but the live model sometimes sends one string instead,
    often a bulleted block ("\\n- first\\n- second"). Split that into its lines.
    """
    if value is None:
        return []
    if isinstance(value, str):
        lines = (line.strip().lstrip("-*•").strip() for line in value.splitlines())
        return [line for line in lines if line]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]
```

`services/review_limits.py`:

```python
# services/review_limits.py
"""How much of the project the Supervisor is shown.

These values are the subject of the user's own experiment (docs/TO-TEST.md). Do not change them.
"""
MAX_CONTEXT_CHARS = 60000
# A task awaiting review needs enough of its run output for the supervisor to
# score completeness/evidence; every other task only needs a short reminder.
MAX_RUN_OUTPUT_PREVIEW_CHARS = 300
MAX_RUN_OUTPUT_REVIEW_CHARS = 8000
MAX_REVIEW_OUTPUT_TOTAL_CHARS = 24000
```

In `services/supervisor_service.py`:
- delete the four constant definitions, the `_clip_run_output` function and the `as_text_list` function;
- add these imports, keeping the `MAX_FOCUS_CHARS`, `MAX_RATIONALE_CHARS` and `MAX_GAPS_CHARS` definitions where they are:

```python
from .review_limits import (
    MAX_CONTEXT_CHARS,
    MAX_REVIEW_OUTPUT_TOTAL_CHARS,
    MAX_RUN_OUTPUT_PREVIEW_CHARS,
    MAX_RUN_OUTPUT_REVIEW_CHARS,
)
from .text_utils import as_text_list, clip_text

_clip_run_output = clip_text  # existing call sites keep their name
```

In `services/board_service.py`, replace `supervisor_service.as_text_list(value)` in `_json_text_list` with `as_text_list(value)`, and add `from .text_utils import as_text_list`.

`db/migrations/0006_tool_calls.sql`:

```sql
-- Phase 4a (Supervisor tool layer). Fully additive: one new table.
-- Every tool call is logged here (who called what, with which inputs, and the outcome).
-- See docs/superpowers/specs/2026-10-05-supervisor-tool-layer-design.md section 2.4.

CREATE TABLE tool_calls (
    id                    INTEGER PRIMARY KEY,
    project_id            INTEGER NOT NULL REFERENCES projects(id),
    tool                  TEXT NOT NULL,
    caller                TEXT NOT NULL,
    research_work_item_id INTEGER REFERENCES research_work_items(id),
    parent_call_id        INTEGER REFERENCES tool_calls(id),
    input_json            TEXT,
    ok                    INTEGER NOT NULL,
    error_code            TEXT,
    error_message         TEXT,
    result_json           TEXT,
    started_at            TEXT NOT NULL,
    duration_ms           INTEGER
);

CREATE INDEX idx_tool_calls_project ON tool_calls(project_id, started_at);
```

`db/repositories/tool_calls_repo.py`:

```python
from datetime import datetime

from ..connection import get_connection


def create(project_id, tool, caller, input_json, parent_call_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO tool_calls (project_id, tool, caller, parent_call_id, input_json, ok, started_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (project_id, tool, caller, parent_call_id, input_json, now),
        )
        return cur.lastrowid


def finish(id, ok, error_code=None, error_message=None, result_json=None, duration_ms=None,
           research_work_item_id=None):
    with get_connection() as conn:
        conn.execute(
            "UPDATE tool_calls SET ok = ?, error_code = ?, error_message = ?, result_json = ?, "
            "duration_ms = ?, research_work_item_id = ? WHERE id = ?",
            (1 if ok else 0, error_code, error_message, result_json, duration_ms, research_work_item_id, id),
        )


def get(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM tool_calls WHERE id = ?", (id,)).fetchone()


def list_for_project(project_id, limit=100):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM tool_calls WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit)
        ).fetchall()
```

In `docs/TO-TEST.md`, under "Where the limits live", change the location of `MAX_RUN_OUTPUT_REVIEW_CHARS`, `MAX_REVIEW_OUTPUT_TOTAL_CHARS` and `MAX_CONTEXT_CHARS` to `services/review_limits.py`. Values are unchanged.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_db_migrations.py tests/test_repositories_tool_calls.py tests/test_supervisor_service.py -q`. Then run `python -m ruff check services/text_utils.py services/review_limits.py services/supervisor_service.py services/board_service.py db/repositories/tool_calls_repo.py tests/test_repositories_tool_calls.py` and `python -m pytest tests/ -q`.
Expected: all pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/text_utils.py services/review_limits.py services/supervisor_service.py services/board_service.py db/migrations/0006_tool_calls.sql db/repositories/tool_calls_repo.py docs/TO-TEST.md tests/test_db_migrations.py tests/test_repositories_tool_calls.py tests/test_supervisor_service.py
git commit -m "Move shared text helpers and review limits; add the tool-call log table"
```

---

### Task 2: The tool framework — registry, run_tool, results, logging, schemas

**Files:**
- Create: `services/tools/__init__.py`, `services/tools/registry.py`, `services/tools/schema.py`, `services/tools/types.py`, `tests/test_tool_registry.py`
- Modify: `requirements_flask.txt` (pin pydantic)

**Interfaces:**
- Consumes: `tool_calls_repo` (Task 1), `text_utils.as_text_list` (Task 1).
- Produces (importable from `services.tools`):
  - `CALLERS = ("supervisor", "user", "system")`;
  - `ToolError(code, message, fields=None)`, with codes `not_allowed`, `invalid_input`, `not_found`, `conflict`, `unavailable`, `internal`;
  - `ToolResult(ok, data=None, error=None, call_id=None)`, where `error` is `{"code", "message", "fields"}`;
  - `ToolContext(project_name, project_id, caller, call_id)`;
  - `Tool(name, description, input_model, handler, callers, id_fields=())`, where `handler(ctx, inputs) -> dict`;
  - `register(tool) -> tool`, `get_tool(name)` and `all_tools() -> dict`;
  - `run_tool(name, caller, project_name, inputs=None, parent_call_id=None) -> ToolResult`;
  - `claude_tools(names) -> list[dict]`, giving the Anthropic tool definitions;
  - `clip_for_log(obj) -> str`;
  - `HTTP_STATUS = {code: int}`.
  - From `services.tools.types`: `ToolInput` (the base model, which ignores extra fields), `Text(max_chars, min_chars=0)`, `text_list(max_items, max_item_chars)`, and `Title`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tool_registry.py`:

```python
import json
from typing import Optional

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
    task_id: Optional[int] = None
    title: Optional[Title] = None
    note: Text(10) = ""
    items: text_list(5, 40) = []
    nested: Optional[_Nested] = None
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tool_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: services.tools`.

- [ ] **Step 3: Implement**

Append `pydantic==2.12.5` to `requirements_flask.txt`.

`services/tools/types.py`:

```python
# services/tools/types.py
"""Input building blocks shared by every tool."""
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from ..text_utils import as_text_list


class ToolInput(BaseModel):
    """Base for tool inputs. Unknown extra fields are ignored rather than failing the call."""

    model_config = ConfigDict(extra="ignore")


def Text(max_chars, min_chars=0):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=min_chars, max_length=max_chars)]


def text_list(max_items, max_item_chars):
    """A list of short strings that also accepts one string (split into lines, bullets removed)."""
    item = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_item_chars)]
    return Annotated[list[item], BeforeValidator(as_text_list), Field(max_length=max_items)]


Title = Text(200, min_chars=1)
```

`services/tools/schema.py`:

```python
# services/tools/schema.py
"""Turn a pydantic input model into the plain JSON schema Claude is given."""

_DROP = ("$defs", "title")


def _inline(node, defs):
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = _inline(defs[node["$ref"].split("/")[-1]], defs)
        rest = {k: _inline(v, defs) for k, v in node.items() if k != "$ref" and k not in _DROP}
        return {**target, **rest}
    out = {}
    for key, value in node.items():
        if key == "properties":
            # Property NAMES are kept as-is (a field may be called "title"); only their schemas are cleaned.
            out[key] = {name: _inline(schema, defs) for name, schema in value.items()}
        elif key in _DROP:
            continue
        else:
            out[key] = _inline(value, defs)
    return out


def claude_schema(model):
    schema = model.model_json_schema()
    return _inline(schema, schema.get("$defs", {}))
```

`services/tools/registry.py`:

```python
# services/tools/registry.py
"""The single door to every tool: caller check, input validation, project scoping,
clean failures and a log row for every call."""
import json
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import anthropic
import openai
from pydantic import BaseModel, ValidationError

from db.repositories import projects_repo, research_work_items_repo, tool_calls_repo

from .schema import claude_schema

CALLERS = ("supervisor", "user", "system")
ERROR_CODES = ("not_allowed", "invalid_input", "not_found", "conflict", "unavailable", "internal")
HTTP_STATUS = {"not_allowed": 403, "invalid_input": 400, "not_found": 404, "conflict": 409,
               "unavailable": 503, "internal": 500}
LOG_STRING_LIMIT = 500
LOG_JSON_LIMIT = 4000
_CLIP_MARK = "...[clipped]"


class ToolError(Exception):
    """An expected failure a handler reports, with one of ERROR_CODES."""

    def __init__(self, code, message, fields=None):
        if code not in ERROR_CODES:
            raise ValueError(f"Unknown tool error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = list(fields or [])


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Optional[dict] = None
    error: Optional[dict] = None
    call_id: Optional[int] = None


@dataclass(frozen=True)
class ToolContext:
    project_name: str
    project_id: int
    caller: str
    call_id: int


@dataclass
class Tool:
    name: str
    description: str
    input_model: type
    handler: Callable
    callers: frozenset
    id_fields: tuple = field(default_factory=tuple)


_REGISTRY = {}


def register(tool):
    if not set(tool.callers) <= set(CALLERS):
        raise ValueError(f"{tool.name}: unknown caller in {sorted(tool.callers)}")
    if tool.name in _REGISTRY:
        raise ValueError(f"Tool already registered: {tool.name}")
    if not (isinstance(tool.input_model, type) and issubclass(tool.input_model, BaseModel)):
        raise ValueError(f"{tool.name}: input_model must be a pydantic model")
    _REGISTRY[tool.name] = tool
    return tool


def get_tool(name):
    return _REGISTRY.get(name)


def all_tools():
    return dict(_REGISTRY)


def claude_tools(names):
    """Anthropic tool definitions for exactly these tools (KeyError if one is unknown)."""
    return [
        {"name": _REGISTRY[n].name, "description": _REGISTRY[n].description,
         "input_schema": claude_schema(_REGISTRY[n].input_model)}
        for n in names
    ]


def _clip_value(value):
    if isinstance(value, str):
        return value if len(value) <= LOG_STRING_LIMIT else value[:LOG_STRING_LIMIT] + _CLIP_MARK
    if isinstance(value, dict):
        return {str(k): _clip_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip_value(v) for v in value]
    return value


def clip_for_log(obj):
    text = json.dumps(_clip_value(obj), default=str)
    return text if len(text) <= LOG_JSON_LIMIT else text[:LOG_JSON_LIMIT] + _CLIP_MARK


def _error(code, message, fields=None):
    return {"code": code, "message": message, "fields": list(fields or [])}


def _validation_error(exc):
    fields = [".".join(str(p) for p in e["loc"]) or "input" for e in exc.errors()]
    first = exc.errors()[0]
    return _error("invalid_input", f"Check {', '.join(dict.fromkeys(fields))}: {first['msg']}", fields)


def _card_ids(tool, parsed):
    ids = []
    for name in tool.id_fields:
        value = getattr(parsed, name, None)
        if value is None:
            continue
        ids.extend(value if isinstance(value, (list, tuple)) else [value])
    return ids


def run_tool(name, caller, project_name, inputs=None, parent_call_id=None):
    started = time.monotonic()
    project_id = projects_repo.get_id(project_name) if project_name else None
    if project_id is None:
        return ToolResult(ok=False, error=_error("not_found", "No such project."))
    raw = inputs if isinstance(inputs, dict) else {}
    call_id = tool_calls_repo.create(project_id, name, caller, clip_for_log(raw), parent_call_id=parent_call_id)

    def finish(ok, data=None, error=None, work_item_id=None, log_message=None):
        tool_calls_repo.finish(
            call_id, ok,
            error_code=error["code"] if error else None,
            error_message=(log_message or error["message"]) if error else None,
            result_json=clip_for_log(data) if ok else None,
            duration_ms=int((time.monotonic() - started) * 1000),
            research_work_item_id=work_item_id,
        )
        return ToolResult(ok=ok, data=data if ok else None, error=error, call_id=call_id)

    tool = _REGISTRY.get(name)
    if tool is None:
        return finish(False, error=_error("not_allowed", f"There is no tool called {name}."))
    if caller not in tool.callers:
        return finish(False, error=_error("not_allowed", f"{name} cannot be used by the {caller}."))
    try:
        parsed = tool.input_model.model_validate(raw)
    except ValidationError as exc:
        return finish(False, error=_validation_error(exc))

    ids = _card_ids(tool, parsed)
    for card_id in ids:
        row = research_work_items_repo.get(card_id)
        if row is None or row["project_id"] != project_id:
            return finish(False, error=_error("not_found", f"No such research: {card_id}"))
    work_item_id = ids[0] if len(ids) == 1 else None

    ctx = ToolContext(project_name=project_name, project_id=project_id, caller=caller, call_id=call_id)
    try:
        data = tool.handler(ctx, parsed) or {}
        return finish(True, data=data, work_item_id=work_item_id)
    except ToolError as exc:
        return finish(False, error=_error(exc.code, exc.message, exc.fields), work_item_id=work_item_id)
    except (anthropic.APIError, openai.APIError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(f"[tools] {name}: outside service error: {detail}")
        return finish(False, error=_error("unavailable", f"{name} could not reach an outside service just now. Try again shortly."),
                      work_item_id=work_item_id, log_message=detail[:LOG_STRING_LIMIT])
    except ValueError as exc:  # the services raise ValueError when a card is not in a state that allows the action
        return finish(False, error=_error("conflict", str(exc)), work_item_id=work_item_id)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(f"[tools] {name} failed: {detail}")
        return finish(False, error=_error("internal", f"Something went wrong running {name}."),
                      work_item_id=work_item_id, log_message=detail[:LOG_STRING_LIMIT])
```

Note: pydantic's `ValidationError` is a subclass of `ValueError`. That's why validation is caught separately, before the handler runs.

`services/tools/__init__.py`:

```python
# services/tools/__init__.py
"""The Supervisor's and the board's only way to act on the app: explicit, checked, logged tools.

Each tool module registers its tools when imported; the imports at the bottom do that.
"""
from .registry import (  # noqa: F401
    CALLERS,
    HTTP_STATUS,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    all_tools,
    claude_tools,
    clip_for_log,
    get_tool,
    register,
    run_tool,
)
```

(Later tasks append `from . import state` and similar lines at the bottom of this file.)

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tool_registry.py -v`, then `python -m ruff check services/tools tests/test_tool_registry.py`, then `python -m pytest tests/ -q`.
Expected: all pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add requirements_flask.txt services/tools tests/test_tool_registry.py
git commit -m "Add the tool framework: registry, run_tool door, results, logging and Claude schemas"
```

---

### Task 3: Read tools, with the Supervisor's briefings built from them

**Files:**
- Create: `services/tools/state.py`, `tests/test_tools_state.py`
- Modify: `services/tools/__init__.py` (register), `services/supervisor_service.py` (`_format_work_item`, `build_context`, `_review_context`, `review_card` context only)

**Interfaces:**
- Consumes: `run_tool`, `register`, `Tool`, `ToolInput` (Task 2); `review_limits`, `text_utils` (Task 1).
- Produces:
  - Tool `get_project_state` (callers: supervisor, user, system; inputs: none). It returns `{"objective", "phases": [{"key","title","frameworks":[{"key","label"}]}], "phase_summaries": {key: text}, "cards": [card], "phase7": {...}, "recent_decisions": [{"decision_type","detail","created_at"}]}`. Each card is every `research_work_items` column plus `dependency_ids: [int]` and `latest_run: None | {"id","status","error","output_text","prompt_text","created_at","completed_at"}`.
  - Tool `get_task` (callers: supervisor, user, system; inputs `{task_id: int}`). It returns `{"card": card (as above, without latest_run), "latest_run": None | {"id","status","error","prompt_text","created_at","completed_at","report_excerpt","report_chars_total"}}`, where `report_excerpt = clip_text(output_text, MAX_RUN_OUTPUT_REVIEW_CHARS)`.
  - `supervisor_service.format_briefing(state) -> str` (pure). `supervisor_service._format_work_item(item, dep_ids, run=None, review_output_limit=None)` now takes a **list of ids** instead of dependency rows.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools_state.py`:

```python
import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo, tool_calls_repo
from services import insights_service, project_service, supervisor_service, tools


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return projects_repo.get_or_create_id("P")


def _card_with_run(pid, title="Fees", output="Report " * 10, status="RUNNING", run_status="completed", run_id="run_1"):
    card = research_work_items_repo.create(pid, "4", title, research_method="TARGETED_WEB", prompt_text="Find the fees.")
    research_work_items_repo.update_fields(card, status=status)
    research_runs_repo.create(run_id, pid, "resp", None, "preview")
    research_runs_repo.update(run_id, research_work_item_id=card, status=run_status, output_text=output, prompt_text="Find the fees.")
    return card


def test_project_state_describes_the_project(pid):
    project_service.save_project_prompt("P", "project_prompt", "Assess the MBA market.")
    a = research_work_items_repo.create(pid, "1", "Landscape")
    b = _card_with_run(pid)
    research_work_items_repo.add_dependency(b, a)
    agent_decisions_repo.record(pid, "no_action", '{"input": {"reason": "x"}}')
    result = tools.run_tool("get_project_state", "supervisor", "P", {})
    assert result.ok
    state = result.data
    assert state["objective"] == "Assess the MBA market."
    assert [p["key"] for p in state["phases"]] == ["1", "2", "3", "4", "5", "6", "7"]
    assert len(state["phases"][2]["frameworks"]) == 3
    cards = {c["id"]: c for c in state["cards"]}
    assert cards[b]["dependency_ids"] == [a]
    assert cards[b]["latest_run"]["output_text"].startswith("Report")
    assert cards[a]["latest_run"] is None
    assert state["recent_decisions"][0]["decision_type"] == "no_action"
    assert state["phase7"]["unlocked"] is False


def test_project_state_only_includes_this_project(pid):
    other = projects_repo.get_or_create_id("Other")
    research_work_items_repo.create(other, "4", "Theirs")
    assert tools.run_tool("get_project_state", "system", "P", {}).data["cards"] == []


def test_project_state_includes_existing_phase_summaries(pid):
    insights_service.save_insights("P", {"generated_at": "", "competitors": [], "competitor_landscape_markdown": "",
                                         "phases": {"1": {"title": "L", "summary": "Established fact", "confidence": "high",
                                                          "evidence_sources": [], "gaps": [], "suggested_topics": [],
                                                          "linked_files": [], "linked_file_ids": []}}})
    assert tools.run_tool("get_project_state", "system", "P", {}).data["phase_summaries"] == {"1": "Established fact"}


def test_get_task_clips_the_report_to_the_review_limit(pid):
    card = _card_with_run(pid, output="x" * (supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS + 5000))
    data = tools.run_tool("get_task", "supervisor", "P", {"task_id": card}).data
    assert data["card"]["id"] == card and data["card"]["title"] == "Fees"
    run = data["latest_run"]
    assert run["report_excerpt"].startswith("x" * supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS)
    assert run["report_excerpt"].endswith("...[truncated]")
    assert run["report_chars_total"] == supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS + 5000
    assert "output_text" not in run


def test_get_task_without_a_run(pid):
    card = research_work_items_repo.create(pid, "4", "No run yet")
    assert tools.run_tool("get_task", "user", "P", {"task_id": card}).data["latest_run"] is None


def test_get_task_needs_a_task_in_this_project(pid):
    other = research_work_items_repo.create(projects_repo.get_or_create_id("Other"), "4", "Theirs")
    assert tools.run_tool("get_task", "supervisor", "P", {"task_id": other}).error["code"] == "not_found"
    assert tools.run_tool("get_task", "supervisor", "P", {}).error["code"] == "invalid_input"


def test_build_context_reads_the_project_through_the_tool(pid):
    supervisor_service.build_context("P")
    log = tool_calls_repo.list_for_project(pid)[0]
    assert (log["tool"], log["caller"], log["ok"]) == ("get_project_state", "system", 1)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_state.py -v`
Expected: FAIL (`get_project_state` is not registered: `not_allowed`).

- [ ] **Step 3: Implement**

`services/tools/state.py`:

```python
# services/tools/state.py
"""Read-only tools: the project as the Supervisor sees it, and one card in detail."""
from db.repositories import agent_decisions_repo, research_runs_repo, research_work_items_repo

from .. import insights_service, research_task_service
from ..phases import PHASE_DEFINITIONS
from ..project_service import load_project_prompt
from ..prompt_frameworks import FRAMEWORK_LABELS, frameworks_for_phase
from ..review_limits import MAX_RUN_OUTPUT_REVIEW_CHARS
from ..text_utils import clip_text
from .registry import Tool, register
from .types import ToolInput

RECENT_DECISIONS = 10
ALL_CALLERS = frozenset({"supervisor", "user", "system"})


def card_dict(row):
    card = dict(row)
    card["dependency_ids"] = [
        r["depends_on_work_item_id"] for r in research_work_items_repo.list_dependencies(row["id"])
    ]
    return card


def _run_dict(run):
    if run is None:
        return None
    return {k: run[k] for k in ("id", "status", "error", "output_text", "prompt_text", "created_at", "completed_at")}


class ProjectStateInput(ToolInput):
    pass


def get_project_state(ctx, inputs):
    current = insights_service.load_current_insights(ctx.project_name)
    summaries = {
        key: phase["summary"] for key, phase in current["phases"].items()
        if phase.get("summary") and phase["summary"] != "MISSING"
    }
    cards = []
    for row in research_work_items_repo.list_for_project(ctx.project_id):
        card = card_dict(row)
        card["latest_run"] = _run_dict(research_runs_repo.find_latest_for_work_item(row["id"]))
        cards.append(card)
    decisions = [
        {"decision_type": d["decision_type"], "detail": d["detail"], "created_at": d["created_at"]}
        for d in agent_decisions_repo.list_for_project(ctx.project_id, limit=RECENT_DECISIONS)
    ]
    return {
        "objective": load_project_prompt(ctx.project_name, "project_prompt") or "",
        "phases": [
            {"key": key, "title": definition["title"],
             "frameworks": [{"key": fk, "label": FRAMEWORK_LABELS[fk]} for fk in frameworks_for_phase(key)]}
            for key, definition in PHASE_DEFINITIONS.items()
        ],
        "phase_summaries": summaries,
        "cards": cards,
        "phase7": research_task_service.phase7_readiness(ctx.project_id),
        "recent_decisions": decisions,
    }


class TaskInput(ToolInput):
    task_id: int


def get_task(ctx, inputs):
    row = research_work_items_repo.get(inputs.task_id)
    run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    latest = None
    if run is not None:
        output = run["output_text"] or ""
        latest = {k: run[k] for k in ("id", "status", "error", "prompt_text", "created_at", "completed_at")}
        latest["report_excerpt"] = clip_text(output, MAX_RUN_OUTPUT_REVIEW_CHARS) if output else ""
        latest["report_chars_total"] = len(output)
    return {"card": card_dict(row), "latest_run": latest}


register(Tool(
    name="get_project_state",
    description="Read the whole project: objective, phases and their frameworks, every research card "
                "with its latest run, existing phase findings, Phase 7 readiness and recent decisions.",
    input_model=ProjectStateInput, handler=get_project_state, callers=ALL_CALLERS,
))
register(Tool(
    name="get_task",
    description="Read one research card in detail, with its latest run and the start of its report.",
    input_model=TaskInput, handler=get_task, callers=ALL_CALLERS, id_fields=("task_id",),
))
```

Append to `services/tools/__init__.py`: `from . import state  # noqa: E402,F401  (registers the read tools)`.

In `services/supervisor_service.py`:

1. Add `from .tools import run_tool` to the imports.
2. Change `_format_work_item` to take ids:

```python
def _format_work_item(item, dep_ids, run=None, review_output_limit=None):
    dep_text = f" | depends on: {list(dep_ids)}" if dep_ids else ""
```

(The rest of the function body stays the same.)

3. Replace `build_context` with a tool call plus a pure formatter. The output is the same, so every existing `build_context` test must keep passing:

```python
def build_context(project_name):
    result = run_tool("get_project_state", "system", project_name, {})
    if not result.ok:
        raise RuntimeError(result.error["message"])
    return format_briefing(result.data)


def _decision_block(decisions, truncated):
    heading = "# RECENT SUPERVISOR DECISIONS (most recent first" + (", truncated" if truncated else "") + ")"
    lines = [f"- {d['decision_type']}: {d['detail']}" for d in decisions]
    return f"{heading}\n\n" + ("\n".join(lines) if lines else "(no prior decisions)")


def format_briefing(state):
    titles = {p["key"]: p["title"] for p in state["phases"]}
    blocks = [f"# PROJECT OBJECTIVE\n\n{state['objective'] or '(none set)'}"]
    blocks.append(
        "# PHASE DEFINITIONS\n\n"
        + "\n".join(
            f"- {p['key']}: {p['title']} (frameworks: "
            + ", ".join(f"{f['key']} = {f['label']}" for f in p["frameworks"])
            + ")"
            for p in state["phases"]
        )
    )
    phase_lines = [f"- Phase {key} ({titles[key]}): {summary}" for key, summary in state["phase_summaries"].items()]
    blocks.append(
        "# EXISTING PHASE FINDINGS (read-only; you never modify these directly)\n\n"
        + ("\n".join(phase_lines) if phase_lines else "(no phase findings established yet)")
    )
    item_lines = []
    review_budget = MAX_REVIEW_OUTPUT_TOTAL_CHARS
    for card in state["cards"]:
        run = card["latest_run"]
        review_output_limit = None
        if _awaiting_review(card, run) and review_budget >= MAX_RUN_OUTPUT_REVIEW_CHARS:
            review_output_limit = MAX_RUN_OUTPUT_REVIEW_CHARS
            review_budget -= min(len(run["output_text"]), review_output_limit)
        item_lines.append(_format_work_item(card, card["dependency_ids"], run, review_output_limit))
    blocks.append("# RESEARCH TASKS\n\n" + ("\n".join(item_lines) if item_lines else "(no research tasks yet)"))
    blocks.append(_decision_block(state["recent_decisions"], truncated=False))

    text = "\n\n".join(blocks)
    if len(text) > MAX_CONTEXT_CHARS:
        # Drop the oldest decisions first (least useful context), then hard-clip.
        blocks[-1] = _decision_block(state["recent_decisions"][:3], truncated=True)
        text = "\n\n".join(blocks)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n\n[...context truncated for length...]"
    return text
```

4. Replace `_review_context` so it builds from `get_task`'s result. The view is the same: the excerpt is already clipped to the review limit, so it's passed through without clipping again:

```python
def _review_context(project_name, task):
    card, run = task["card"], task["latest_run"]
    objective = load_project_prompt(project_name, "project_prompt") or "(none set)"
    phase_title = PHASE_DEFINITIONS.get(card["phase_key"], {}).get("title", card["phase_key"])
    prompt = _clip_run_output(run["prompt_text"] or card["prompt_text"] or "", MAX_REVIEW_PROMPT_CHARS)
    excerpt = run["report_excerpt"]
    item_line = _format_work_item(
        card, card["dependency_ids"], {"status": run["status"], "output_text": excerpt},
        review_output_limit=max(len(excerpt), 1),
    )
    return (
        f"# PROJECT OBJECTIVE\n\n{objective}\n\n"
        f"# RESEARCH BEING REVIEWED\n\nPhase {card['phase_key']}: {phase_title}\n{item_line}\n\n"
        f"# PROMPT THAT WAS SENT\n\n{prompt}"
    )
```

5. In `review_card`, inside the `try:` and before the Claude call, fetch the task through the tool and pass it in:

```python
        task = run_tool("get_task", "system", project_name, {"task_id": card_id})
        if not task.ok:
            raise RuntimeError(task.error["message"])
```

Then change the `messages=` argument to `[{"role": "user", "content": _review_context(project_name, task.data)}]`. Everything else in `review_card` stays for now; Task 5 changes it.

Remove imports from `supervisor_service.py` that ruff then reports unused (`review_card` still uses `research_runs_repo`, so keep that one).

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_state.py tests/test_supervisor_service.py -v` (expected: all pass; this includes every existing `build_context_*` and review-context test). Then run `python -m ruff check services/tools services/supervisor_service.py tests/test_tools_state.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add services/tools/state.py services/tools/__init__.py services/supervisor_service.py tests/test_tools_state.py
git commit -m "Add read tools and build the Supervisor's briefings from them"
```

---

### Task 4: Card tools — create research, follow-ups, human review, no action

**Files:**
- Create: `services/tools/activity.py`, `services/tools/cards.py`, `tests/test_tools_cards.py`
- Modify: `services/tools/__init__.py` (register), `services/board_service.py` (`create_card` becomes a wrapper; add `_user_tool`)

**Interfaces:**
- Consumes: Tasks 2–3; `prompt_drafting_service.create_drafted_card`; `research_task_service.create_task/add_dependency/flag_for_human_review/transition_task`; `prompt_frameworks.resolve_framework`.
- Produces:
  - Tool `create_research_task` (callers: supervisor, user). Inputs: `{tasks: [1..10 NewTask], reason?}`, where `NewTask = {phase_key: "1".."6", title, research_method: TARGETED_WEB|FILE_ANALYSIS, focus?, rationale?, framework_key?, priority?, prompt_text?, draft_prompt=True, depends_on_existing_ids?, depends_on_batch_indices?}`. Returns `{"created_task_ids": [int], "drafted_from_framework": int}`.
  - Tool `create_followup_task` (callers: supervisor, user). Inputs `{task_id, title, focus?, research_method?, rationale?}`; returns `{"followup_task_id": int}`.
  - Tool `request_human_review` (caller: supervisor). Inputs `{task_id, reason?}`; returns `{"task_id", "new_status"}`.
  - Tool `no_action` (caller: supervisor). Inputs `{reason?}`; returns `{}`.
  - `tools.activity.record_user_action(ctx, decision_type, card_id=None, **detail)`.
  - `board_service._user_tool(project_name, name, inputs) -> dict`, which turns a failed `ToolResult` back into `CardNotFound` / `BoardStateError` / `DraftingUnavailable` / `ValueError` / `RuntimeError`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools_cards.py`:

```python
import json

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo
from services import llm_service, research_task_service, tools

DRAFT = "ROLE: analyst. A complete drafted research prompt for this card."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_service, "prompt_completion", lambda system, user, max_tokens=4000: DRAFT)
    return projects_repo.get_or_create_id("P")


def _task(**over):
    return dict({"phase_key": "1", "title": "Landscape overview", "research_method": "TARGETED_WEB",
                 "focus": ["Providers"], "rationale": "Phase 1 has nothing yet"}, **over)


def _create(caller="supervisor", *tasks, **extra):
    return tools.run_tool("create_research_task", caller, "P", dict({"tasks": list(tasks)}, **extra))


def test_supervisor_creates_drafted_cards_that_wait_for_approval(pid):
    result = _create("supervisor", _task(), _task(phase_key="3", title="Website review", framework_key="oes-marketing-website"))
    assert result.ok and result.data["drafted_from_framework"] == 2
    items = research_work_items_repo.list_for_project(pid)
    assert [i["status"] for i in items] == ["PROPOSED", "PROPOSED"]
    assert items[0]["prompt_text"] == DRAFT
    assert (items[0]["framework_key"], items[1]["framework_key"]) == ("oes-landscape", "oes-marketing-website")
    assert agent_decisions_repo.list_for_project(pid) == []  # the Supervisor's decision is recorded by supervisor_service


def test_user_can_write_their_own_prompt_and_it_is_recorded(pid):
    result = _create("user", _task(prompt_text="Research every provider and cite each claim, please.", draft_prompt=False))
    (card_id,) = result.data["created_task_ids"]
    assert research_work_items_repo.get(card_id)["prompt_text"].startswith("Research every provider")
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "user_add"


def test_user_can_add_a_card_with_no_prompt_yet(pid):
    (card_id,) = _create("user", _task(draft_prompt=False)).data["created_task_ids"]
    assert research_work_items_repo.get(card_id)["prompt_text"] is None


def test_batch_dependencies_are_wired(pid):
    existing = research_work_items_repo.create(pid, "1", "Earlier")
    a, b = _create("supervisor", _task(title="A", depends_on_existing_ids=[existing]),
                   _task(title="B", depends_on_batch_indices=[0])).data["created_task_ids"]
    deps = lambda i: [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(i)]
    assert deps(a) == [existing] and deps(b) == [a]


def test_focus_sent_as_one_string_is_a_list(pid):
    (card_id,) = _create("supervisor", _task(focus="Fees\nDelivery")).data["created_task_ids"]
    assert json.loads(research_work_items_repo.get(card_id)["entities_json"]) == ["Fees", "Delivery"]


@pytest.mark.parametrize("bad", [
    _task(phase_key="7"), _task(title="   "), _task(research_method="DEEP_MAGIC"),
    _task(depends_on_batch_indices=[5]), _task(depends_on_existing_ids=[99999]), _task(priority="urgent"),
])
def test_invalid_requests_create_nothing(pid, bad):
    result = _create("supervisor", _task(title="Fine"), bad)
    assert result.error["code"] == "invalid_input"
    assert research_work_items_repo.list_for_project(pid) == []


def test_dependency_on_another_projects_card_creates_nothing(pid):
    theirs = research_work_items_repo.create(projects_repo.get_or_create_id("Other"), "1", "Theirs")
    assert _create("supervisor", _task(depends_on_existing_ids=[theirs])).error["code"] == "invalid_input"
    assert research_work_items_repo.list_for_project(pid) == []


def test_a_loop_between_new_cards_creates_nothing(pid):
    result = _create("supervisor", _task(title="A", depends_on_batch_indices=[1]), _task(title="B", depends_on_batch_indices=[0]))
    assert result.error["code"] == "invalid_input"
    assert research_work_items_repo.list_for_project(pid) == []


def test_too_many_cards_in_one_call(pid):
    assert _create("supervisor", *[_task(title=f"T{i}") for i in range(11)]).error["code"] == "invalid_input"


def test_followup_is_a_draft_linked_to_its_original(pid):
    original = research_task_service.create_task(pid, "4", "Fees", research_method="FILE_ANALYSIS", framework_key="oes-product-features")
    result = tools.run_tool("create_followup_task", "supervisor", "P",
                            {"task_id": original, "title": "Verify intakes", "focus": "Intakes"})
    follow = research_work_items_repo.get(result.data["followup_task_id"])
    assert (follow["status"], follow["phase_key"], follow["suggested_from_work_item_id"]) == ("PROPOSED", "4", original)
    assert follow["research_method"] == "FILE_ANALYSIS"  # falls back to the original's method
    assert json.loads(follow["entities_json"]) == ["Intakes"]
    assert follow["prompt_text"] == DRAFT


def test_request_human_review_moves_a_card_being_reviewed(pid):
    card = research_task_service.create_task(pid, "4", "Fees")
    research_work_items_repo.update_fields(card, status="REVIEWING")
    data = tools.run_tool("request_human_review", "supervisor", "P", {"task_id": card, "reason": "Unsure"}).data
    assert data == {"task_id": card, "new_status": "WAITING_FOR_HUMAN"}
    assert research_work_items_repo.get(card)["human_review_required"] == 1


def test_request_human_review_only_flags_other_cards(pid):
    card = research_task_service.create_task(pid, "4", "Fees")
    data = tools.run_tool("request_human_review", "supervisor", "P", {"task_id": card}).data
    assert data["new_status"] == "PROPOSED" and research_work_items_repo.get(card)["human_review_required"] == 1


@pytest.mark.parametrize("name,inputs", [("request_human_review", {"task_id": 1}), ("no_action", {})])
def test_supervisor_only_tools_refuse_the_user(pid, name, inputs):
    research_task_service.create_task(pid, "4", "Card")
    assert tools.run_tool(name, "user", "P", inputs).error["code"] == "not_allowed"


def test_no_action(pid):
    assert tools.run_tool("no_action", "supervisor", "P", {"reason": "Nothing to add"}).data == {}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_cards.py -v`
Expected: FAIL (the tools aren't registered: `not_allowed`).

- [ ] **Step 3: Implement**

`services/tools/activity.py`:

```python
# services/tools/activity.py
"""Activity-feed entries for actions the user takes through tools (same decision types as before)."""
import json

from db.repositories import agent_decisions_repo, research_work_items_repo


def record_user_action(ctx, decision_type, card_id=None, **detail):
    if card_id is not None:
        row = research_work_items_repo.get(card_id)
        if row is not None:
            detail.setdefault("title", row["title"])
    agent_decisions_repo.record(ctx.project_id, decision_type, json.dumps(detail), research_work_item_id=card_id)
```

`services/tools/cards.py`:

```python
# services/tools/cards.py
"""Tools that create draft cards or flag a card for the user. Nothing here approves or runs research."""
from typing import Literal, Optional

from pydantic import Field

from db.repositories import research_work_items_repo

from .. import research_task_service
from ..prompt_drafting_service import create_drafted_card
from ..prompt_frameworks import resolve_framework
from .activity import record_user_action
from .registry import Tool, ToolError, register
from .types import Text, Title, ToolInput, text_list

DraftPhase = Literal["1", "2", "3", "4", "5", "6"]
DraftMethod = Literal["TARGETED_WEB", "FILE_ANALYSIS"]
Priority = Literal["low", "medium", "high"]
MAX_TASKS_PER_CALL = 10
SUPERVISOR_AND_USER = frozenset({"supervisor", "user"})
SUPERVISOR_ONLY = frozenset({"supervisor"})


class NewTask(ToolInput):
    phase_key: DraftPhase = Field(description="Phase '1' to '6'. Phase 7 is never drafted here.")
    title: Title
    research_method: DraftMethod = Field(
        description="TARGETED_WEB for public web information; FILE_ANALYSIS when the answer is in the project's uploaded files.")
    focus: text_list(10, 120) = []
    rationale: Text(1000) = Field(default="", description="One or two plain sentences on why this research is needed now.")
    framework_key: Optional[Text(60)] = Field(default=None, description="One of the frameworks listed for this phase.")
    priority: Optional[Priority] = None
    prompt_text: Optional[Text(30000)] = Field(default=None, description="Leave out to have the prompt drafted from the phase framework.")
    draft_prompt: bool = True
    depends_on_existing_ids: list[int] = Field(default_factory=list, max_length=20)
    depends_on_batch_indices: list[int] = Field(default_factory=list, max_length=20,
                                                description="Zero-based indices into this same tasks array.")


class CreateResearchTaskInput(ToolInput):
    tasks: list[NewTask] = Field(min_length=1, max_length=MAX_TASKS_PER_CALL)
    reason: Text(2000) = ""


def _has_loop(tasks):
    edges = {i: list(t.depends_on_batch_indices) for i, t in enumerate(tasks)}
    state = {}

    def visit(i):
        if state.get(i) == "visiting":
            return True
        if state.get(i) == "done":
            return False
        state[i] = "visiting"
        if any(visit(j) for j in edges[i]):
            return True
        state[i] = "done"
        return False

    return any(visit(i) for i in edges)


def _check_dependencies(ctx, tasks):
    for t in tasks:
        for dep_id in t.depends_on_existing_ids:
            row = research_work_items_repo.get(dep_id)
            if row is None or row["project_id"] != ctx.project_id:
                raise ToolError("invalid_input", f"depends_on_existing_ids value {dep_id} is not a research in this project",
                                ["depends_on_existing_ids"])
        for index in t.depends_on_batch_indices:
            if index < 0 or index >= len(tasks):
                raise ToolError("invalid_input", f"depends_on_batch_indices value {index} is out of range for a batch of {len(tasks)}",
                                ["depends_on_batch_indices"])
    if _has_loop(tasks):
        raise ToolError("invalid_input", "These researches depend on each other in a loop", ["depends_on_batch_indices"])


def create_research_task(ctx, inputs):
    tasks = inputs.tasks
    _check_dependencies(ctx, tasks)  # everything is checked before anything is created
    new_ids = []
    drafted = 0
    for t in tasks:
        framework = resolve_framework(t.phase_key, t.framework_key)
        if t.prompt_text or not t.draft_prompt:
            card_id = research_task_service.create_task(
                ctx.project_id, t.phase_key, t.title, priority=t.priority, research_method=t.research_method,
                entities=t.focus or None, rationale=t.rationale or None, framework_key=framework,
                prompt_text=t.prompt_text or None,
            )
        else:
            created = create_drafted_card(
                ctx.project_name, t.phase_key, t.title, research_method=t.research_method, focus=t.focus or None,
                rationale=t.rationale or None, framework_key=framework, priority=t.priority,
            )
            card_id = created["card_id"]
            drafted += 1 if created["drafted"] else 0
        new_ids.append(card_id)
        if ctx.caller == "user":
            record_user_action(ctx, "user_add", card_id)
    for i, t in enumerate(tasks):
        for dep_id in t.depends_on_existing_ids:
            research_task_service.add_dependency(new_ids[i], dep_id)
        for index in t.depends_on_batch_indices:
            research_task_service.add_dependency(new_ids[i], new_ids[index])
    return {"created_task_ids": new_ids, "drafted_from_framework": drafted}


class FollowupInput(ToolInput):
    task_id: int
    title: Title
    focus: text_list(10, 120) = []
    research_method: Optional[DraftMethod] = None
    rationale: Text(1000) = ""


def create_followup_task(ctx, inputs):
    original = research_work_items_repo.get(inputs.task_id)
    method = inputs.research_method or (
        original["research_method"] if original["research_method"] in ("TARGETED_WEB", "FILE_ANALYSIS") else "TARGETED_WEB"
    )
    created = create_drafted_card(
        ctx.project_name, original["phase_key"], inputs.title, research_method=method, focus=inputs.focus or None,
        rationale=inputs.rationale or f"Fills gaps found in \"{original['title']}\".",
        framework_key=original["framework_key"], suggested_from_work_item_id=original["id"],
    )
    return {"followup_task_id": created["card_id"]}


class HumanReviewInput(ToolInput):
    task_id: int
    reason: Text(1000) = ""


def request_human_review(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    research_task_service.flag_for_human_review(inputs.task_id)
    if card["status"] == "REVIEWING":
        research_task_service.transition_task(inputs.task_id, "WAITING_FOR_HUMAN")
        return {"task_id": inputs.task_id, "new_status": "WAITING_FOR_HUMAN"}
    return {"task_id": inputs.task_id, "new_status": card["status"]}


class NoActionInput(ToolInput):
    reason: Text(2000) = ""


def no_action(ctx, inputs):
    return {}


register(Tool(
    name="create_research_task",
    description="Draft one or more researches. Each becomes a card that waits for the person's approval before it can run.",
    input_model=CreateResearchTaskInput, handler=create_research_task, callers=SUPERVISOR_AND_USER,
))
register(Tool(
    name="create_followup_task",
    description="Draft a follow-up research that fills gaps in an existing one. It waits for the person's approval.",
    input_model=FollowupInput, handler=create_followup_task, callers=SUPERVISOR_AND_USER, id_fields=("task_id",),
))
register(Tool(
    name="request_human_review",
    description="Flag a research as needing the person's judgement before it can be accepted.",
    input_model=HumanReviewInput, handler=request_human_review, callers=SUPERVISOR_ONLY, id_fields=("task_id",),
))
register(Tool(
    name="no_action",
    description="Nothing useful can be done right now.",
    input_model=NoActionInput, handler=no_action, callers=SUPERVISOR_ONLY,
))
```

Append to `services/tools/__init__.py`: `from . import cards  # noqa: E402,F401  (registers the card tools)`.

In `services/board_service.py`:
- add `from .tools import run_tool`;
- add the error translation helper;
- replace `create_card`'s body.

Keep the three friendly checks so the on-screen messages are unchanged:

```python
def _user_tool(project_name, name, inputs):
    """Run a tool as the user and turn a failure back into the board's existing exceptions."""
    result = run_tool(name, "user", project_name, inputs)
    if result.ok:
        return result.data
    code, message = result.error["code"], result.error["message"]
    if code == "not_found":
        raise CardNotFound(message)
    if code == "conflict":
        raise BoardStateError(message)
    if code == "unavailable":
        raise DraftingUnavailable(message)
    if code == "invalid_input":
        raise ValueError(message)
    raise RuntimeError(message)


def create_card(project_name, data):
    phase_key = str(data.get("phase_key") or "")
    title = (data.get("title") or "").strip()
    method = data.get("research_method")
    if phase_key not in USER_PHASES:
        raise ValueError("Choose a phase from 1 to 6. The options report has its own button in Phase 7.")
    if not title:
        raise ValueError("Add a title.")
    if method not in USER_METHODS:
        raise ValueError("Choose how it runs: web research or your files.")
    task = {
        "phase_key": phase_key, "title": title, "research_method": method,
        "focus": _clean_focus(data.get("focus")), "rationale": (data.get("rationale") or "").strip(),
        "framework_key": data.get("framework_key") or None,
        "prompt_text": (data.get("prompt_text") or "").strip() or None,
        "draft_prompt": bool(data.get("draft_prompt")),
    }
    return _user_tool(project_name, "create_research_task", {"tasks": [task]})["created_task_ids"][0]
```

`create_card` no longer calls `_record` itself, because the tool records `user_add`. Remove imports that ruff then reports unused.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_cards.py tests/test_board_service.py tests/test_routes_board.py -v` (expected: all pass; the board's existing `create_card` tests and route tests are the regression check). Then run `python -m ruff check services/tools services/board_service.py tests/test_tools_cards.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add services/tools/activity.py services/tools/cards.py services/tools/__init__.py services/board_service.py tests/test_tools_cards.py
git commit -m "Add card tools (create research, follow-ups, human review, no action); Add research goes through them"
```

---

### Task 5: evaluate_research_output, and the Supervisor's two jobs switched to fixed menus

**Files:**
- Create: `services/tools/review.py`, `tests/test_tools_review.py`
- Modify:
  - `services/tools/registry.py`: add the `menu=` parameter to `run_tool`;
  - `services/tools/__init__.py`: register the review tool;
  - `services/supervisor_service.py`: `draft_researches` and `review_card` go through the registry; the old tool dicts and handlers are deleted;
  - `services/board_service.py`: the Activity text reads the new decision name;
  - `tests/test_supervisor_service.py`, `tests/test_routes_board.py`, `tests/test_tool_registry.py`.

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `run_tool(..., menu=None)`. When `menu` is given and `name` isn't in it, the call is refused as `not_allowed` ("X is not on this menu.") and logged.
  - Tool `evaluate_research_output` (caller: supervisor). Inputs: `{task_id, completeness_score: 0–1, evidence_score: 0–1, identified_gaps?, outcome: COMPLETE|FOLLOW_UP_REQUIRED|NEEDS_HUMAN|FAILED, reason?, followup?: {title, focus?, research_method?, rationale?}}`. Returns `{"task_id","outcome","new_status","followup_task_id"}`.
  - `supervisor_service.DRAFTING_MENU = ("create_research_task", "no_action")` and `supervisor_service.REVIEW_MENU = ("evaluate_research_output",)`.
  - `review_card(...)` returns `{"reviewed": True, "task_id", "outcome", "new_status", "followup_task_id"}` on success. Note the renamed key, `followup_task_id`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tool_registry.py`:

```python
def test_a_tool_not_on_the_menu_is_refused_and_logged(pid):
    result = tools.run_tool("test_echo", "user", "P", {}, menu=("something_else",))
    assert result.error["code"] == "not_allowed" and "menu" in result.error["message"]
    assert _last_log(pid)["error_code"] == "not_allowed"
```

`tests/test_tools_review.py`:

```python
import json

import pytest

from db.repositories import projects_repo, research_work_items_repo, tool_calls_repo
from services import llm_service, research_task_service, tools


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_service, "prompt_completion", lambda s, u, max_tokens=4000: "ROLE: analyst. A drafted follow-up prompt.")
    return projects_repo.get_or_create_id("P")


def _reviewing(pid, **kw):
    card = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB", **kw)
    research_work_items_repo.update_fields(card, status="REVIEWING")
    return card


def _evaluate(card, outcome, **extra):
    inputs = dict({"task_id": card, "completeness_score": 0.8, "evidence_score": 0.7,
                   "identified_gaps": ["No intake dates"], "outcome": outcome, "reason": "Because"}, **extra)
    return tools.run_tool("evaluate_research_output", "supervisor", "P", inputs)


@pytest.mark.parametrize("outcome,status", [
    ("COMPLETE", "COMPLETE"), ("FOLLOW_UP_REQUIRED", "FOLLOW_UP_REQUIRED"),
    ("NEEDS_HUMAN", "WAITING_FOR_HUMAN"), ("FAILED", "FAILED"),
])
def test_outcomes(pid, outcome, status):
    card = _reviewing(pid)
    result = _evaluate(card, outcome)
    assert result.ok and result.data["new_status"] == status
    row = research_work_items_repo.get(card)
    assert (row["status"], row["completeness_score"]) == (status, 0.8)
    assert json.loads(row["identified_gaps_json"]) == ["No intake dates"]


def test_follow_up_is_created_as_a_logged_child_call(pid):
    card = _reviewing(pid)
    result = _evaluate(card, "FOLLOW_UP_REQUIRED", followup={"title": "Verify intakes", "focus": "Intakes"})
    follow = research_work_items_repo.get(result.data["followup_task_id"])
    assert (follow["status"], follow["suggested_from_work_item_id"]) == ("PROPOSED", card)
    children = [r for r in tool_calls_repo.list_for_project(pid) if r["parent_call_id"] == result.call_id]
    assert [c["tool"] for c in children] == ["create_followup_task"]


def test_needs_human_goes_through_request_human_review(pid):
    card = _reviewing(pid)
    result = _evaluate(card, "NEEDS_HUMAN")
    assert research_work_items_repo.get(card)["human_review_required"] == 1
    assert [r["tool"] for r in tool_calls_repo.list_for_project(pid) if r["parent_call_id"] == result.call_id] == ["request_human_review"]


def test_complete_on_a_flagged_card_waits_for_the_person(pid):
    card = _reviewing(pid, human_review_required=True)
    assert _evaluate(card, "COMPLETE").data["new_status"] == "WAITING_FOR_HUMAN"


def test_gaps_sent_as_one_bulleted_string(pid):
    card = _reviewing(pid)
    _evaluate(card, "FAILED", identified_gaps="\n- One gap\n- Two gap")
    assert json.loads(research_work_items_repo.get(card)["identified_gaps_json"]) == ["One gap", "Two gap"]


def test_only_a_card_being_reviewed_can_be_evaluated(pid):
    card = research_task_service.create_task(pid, "4", "Fees")
    assert _evaluate(card, "COMPLETE").error["code"] == "conflict"
    assert research_work_items_repo.get(card)["status"] == "PROPOSED"


@pytest.mark.parametrize("bad", [{"completeness_score": 1.5}, {"outcome": "GREAT"}, {"evidence_score": "lots"}])
def test_invalid_review_input(pid, bad):
    card = _reviewing(pid)
    assert _evaluate(card, "COMPLETE", **bad).error["code"] == "invalid_input"
    assert research_work_items_repo.get(card)["status"] == "REVIEWING"


@pytest.mark.parametrize("caller", ["user", "system"])
def test_only_the_supervisor_evaluates(pid, caller):
    card = _reviewing(pid)
    result = tools.run_tool("evaluate_research_output", caller, "P",
                            {"task_id": card, "completeness_score": 1, "evidence_score": 1, "outcome": "COMPLETE"})
    assert result.error["code"] == "not_allowed"
```

In `tests/test_supervisor_service.py` (the Phase 3 Research Board tests), update to the new names:
- every `_Block("propose_tasks", ...)` / `_propose(...)` block name becomes `create_research_task`;
- every `_Block("review_outcome", ...)` / `_review(...)` block name becomes `evaluate_research_output`;
- `test_drafting_offers_only_propose_and_no_action` asserts `{t["name"] for t in call["tools"]} == {"create_research_task", "no_action"}` and `supervisor_service.DRAFTING_MENU == ("create_research_task", "no_action")`;
- `test_review_is_forced_to_the_review_tool` asserts `[t["name"] for t in call["tools"]] == ["evaluate_research_output"]` and `call["tool_choice"] == {"type": "tool", "name": "evaluate_research_output"}`;
- every `result["followup_card_id"]` becomes `result["followup_task_id"]`;
- add `"handle_propose_tasks", "DRAFTING_HANDLERS", "create_followup_card", "_apply_review", "PROPOSE_TASKS_TOOL", "REVIEW_TOOL"` to the attributes `test_the_supervisor_has_no_way_to_approve_or_start_research` asserts are gone;
- the decision recorded for a draft is now `create_research_task`. Change `== "propose_tasks"` assertions accordingly. Reviews still record `review_outcome` and `review_error`.

Then append:

```python
def test_an_off_menu_tool_from_the_model_runs_nothing(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB", prompt_text="x" * 50)
    research_work_items_repo.update_fields(card, status="READY")
    _install(monkeypatch, _FakeClient([_Block("launch_deep_research", {"task_ids": [card]})]))
    result = supervisor_service.draft_researches("P")
    assert result["execution"]["success"] is False
    assert research_work_items_repo.get(card)["status"] == "READY"
    from db.repositories import tool_calls_repo
    log = tool_calls_repo.list_for_project(pid)[0]
    assert (log["tool"], log["caller"], log["error_code"]) == ("launch_deep_research", "supervisor", "not_allowed")


def test_the_reviewed_card_is_chosen_by_the_system_not_the_model(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card = _running_card(pid)
    other = _running_card(pid)
    _install(monkeypatch, _FakeClient([_Block("evaluate_research_output", {
        "task_id": other, "completeness_score": 0.8, "evidence_score": 0.7, "outcome": "COMPLETE", "reason": "x"})]))
    supervisor_service.review_card("P", card)
    assert research_work_items_repo.get(card)["status"] == "COMPLETE"
    assert research_work_items_repo.get(other)["status"] == "RUNNING"
```

(Use the file's existing `_running_card`, `_install`, `_FakeClient`, `_Block`, `app_context` and `drafted` helpers. If `_running_card` reuses a fixed run id, give the second card a distinct run.)

In `tests/test_routes_board.py`, change the draft test's `_Block("propose_tasks", ...)` to `_Block("create_research_task", ...)`.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_review.py tests/test_supervisor_service.py tests/test_tool_registry.py tests/test_routes_board.py -v`
Expected: FAIL (no review tool, no `menu` parameter, and the Supervisor still offers the old tool names).

- [ ] **Step 3: Implement**

In `services/tools/registry.py`, give `run_tool` the parameter `menu=None` and add this check right after the unknown-tool check (before the caller check):

```python
    if menu is not None and name not in menu:
        return finish(False, error=_error("not_allowed", f"{name} is not on this menu."))
```

Make the unknown-tool check apply only when the tool is unknown and **not** already refused by the menu. Check the menu first when it's given, so an invented name logs "not on this menu".

`services/tools/review.py`:

```python
# services/tools/review.py
"""The Supervisor's review of one finished research. Creating a follow-up or asking for the
person's review happen as child tool calls, so they are checked and logged the same way."""
from typing import Literal, Optional

from pydantic import Field

from db.repositories import research_work_items_repo

from .. import research_task_service
from .registry import Tool, ToolError, register, run_tool
from .types import Text, Title, ToolInput, text_list

Outcome = Literal["COMPLETE", "FOLLOW_UP_REQUIRED", "NEEDS_HUMAN", "FAILED"]


class FollowupSuggestion(ToolInput):
    title: Title
    focus: text_list(10, 120) = []
    research_method: Optional[Literal["TARGETED_WEB", "FILE_ANALYSIS"]] = None
    rationale: Text(1000) = ""


class EvaluateInput(ToolInput):
    task_id: int
    completeness_score: float = Field(ge=0, le=1, description="Did it answer the prompt? 0 to 1.")
    evidence_score: float = Field(ge=0, le=1, description="Are its claims sourced? 0 to 1.")
    identified_gaps: text_list(20, 500) = []
    outcome: Outcome = Field(description="COMPLETE if good enough; FOLLOW_UP_REQUIRED if specific gaps need another "
                                         "research (describe it in followup); NEEDS_HUMAN if a person must judge it; "
                                         "FAILED if it produced nothing usable.")
    reason: Text(2000) = ""
    followup: Optional[FollowupSuggestion] = None


def _child(ctx, name, inputs):
    result = run_tool(name, ctx.caller, ctx.project_name, inputs, parent_call_id=ctx.call_id)
    if not result.ok:
        raise ToolError(result.error["code"], result.error["message"], result.error.get("fields"))
    return result.data


def evaluate_research_output(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    if card["status"] != "REVIEWING":
        raise ToolError("conflict", "This research is not being reviewed right now.")
    research_task_service.set_review_scores(
        inputs.task_id, completeness_score=inputs.completeness_score,
        evidence_score=inputs.evidence_score, identified_gaps=inputs.identified_gaps,
    )
    followup_id = None
    if inputs.outcome == "FOLLOW_UP_REQUIRED" and inputs.followup is not None:
        followup_id = _child(ctx, "create_followup_task",
                             {"task_id": inputs.task_id, **inputs.followup.model_dump()})["followup_task_id"]
    if inputs.outcome == "NEEDS_HUMAN" or (inputs.outcome == "COMPLETE" and card["human_review_required"]):
        new_status = _child(ctx, "request_human_review", {"task_id": inputs.task_id, "reason": inputs.reason})["new_status"]
    else:
        research_task_service.transition_task(inputs.task_id, inputs.outcome)
        new_status = inputs.outcome
    return {"task_id": inputs.task_id, "outcome": inputs.outcome, "new_status": new_status,
            "followup_task_id": followup_id}


register(Tool(
    name="evaluate_research_output",
    description="Record your review of the finished research you were shown.",
    input_model=EvaluateInput, handler=evaluate_research_output,
    callers=frozenset({"supervisor"}), id_fields=("task_id",),
))
```

Append to `services/tools/__init__.py`: `from . import review  # noqa: E402,F401  (registers the review tool)`.

In `services/supervisor_service.py`:
- **delete** `PROPOSE_TASKS_TOOL`, `NO_ACTION_TOOL`, `DRAFTING_TOOLS`, `REVIEW_TOOL`, `_batch_has_cycle`, `_validate_proposals`, `handle_propose_tasks`, `handle_no_action`, `DRAFTING_HANDLERS`, `create_followup_card` and `_apply_review`;
- delete `DRAFTABLE_PHASES`, `DRAFTABLE_METHODS` and `REVIEW_OUTCOMES` if nothing else uses them;
- change the import to `from .tools import claude_tools, run_tool`;
- keep `DRAFTING_SYSTEM_PROMPT` and `REVIEW_SYSTEM_PROMPT`;
- add the two menus and replace both functions:

```python
DRAFTING_MENU = ("create_research_task", "no_action")
REVIEW_MENU = ("evaluate_research_output",)


def draft_researches(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    response = _client().messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=4000,
        system=DRAFTING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_context(project_name)}],
        tools=claude_tools(DRAFTING_MENU),
        tool_choice={"type": "any"},
    )
    block = _tool_use(response)
    if block is None:
        raise RuntimeError("The Supervisor did not return a decision.")
    result = run_tool(block.name, "supervisor", project_name, block.input, menu=DRAFTING_MENU)
    execution = ({"success": True, "result": result.data} if result.ok
                 else {"success": False, "error": result.error["message"]})
    agent_decisions_repo.record(
        project_id, decision_type=block.name, detail=json.dumps({"input": block.input, "execution": execution}),
    )
    return {"action": block.name, "input": block.input, "execution": execution}


def review_card(project_name, card_id):
    project_id = projects_repo.get_or_create_id(project_name)
    card = research_work_items_repo.get(card_id)
    if card is None or card["project_id"] != project_id:
        raise ValueError(f"No such research: {card_id}")
    run = research_runs_repo.find_latest_for_work_item(card_id)
    if card["status"] != "RUNNING" or run is None or run["status"] != "completed" or not run["output_text"]:
        return {"reviewed": False, "reason": "This research has no finished report to review."}
    if not research_task_service.claim_transition(card_id, "RUNNING", "REVIEWING"):
        return {"reviewed": False, "reason": "This research is already being reviewed."}

    try:
        task = run_tool("get_task", "system", project_name, {"task_id": card_id})
        if not task.ok:
            raise RuntimeError(task.error["message"])
        response = _client().messages.create(
            model=current_app.config["ANTHROPIC_MODEL"],
            max_tokens=2000,
            system=REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _review_context(project_name, task.data)}],
            tools=claude_tools(REVIEW_MENU),
            tool_choice={"type": "tool", "name": "evaluate_research_output"},
        )
        block = _tool_use(response, "evaluate_research_output")
        if block is None:
            raise RuntimeError("The Supervisor did not return a review.")
        inputs = dict(block.input)
        inputs["task_id"] = card_id  # the system, not the model, decides which card is being reviewed
        result = run_tool("evaluate_research_output", "supervisor", project_name, inputs, menu=REVIEW_MENU)
        if not result.ok:
            raise RuntimeError(result.error["message"])
    except Exception as exc:
        research_task_service.claim_transition(card_id, "REVIEWING", "RUNNING")
        agent_decisions_repo.record(
            project_id, decision_type="review_error", detail=json.dumps({"error": str(exc)}),
            research_work_item_id=card_id,
        )
        return {"reviewed": False, "error": str(exc)}

    agent_decisions_repo.record(
        project_id, decision_type="review_outcome",
        detail=json.dumps({"input": block.input, "execution": {"success": True, "result": result.data}}),
        research_work_item_id=card_id,
    )
    return {"reviewed": True, **result.data}
```

In `services/board_service.py` `_activity_item`, change `elif dtype == "propose_tasks":` to `elif dtype in ("propose_tasks", "create_research_task"):`. Older rows keep reading correctly.

Remove imports that ruff then reports unused in `supervisor_service.py`, such as `create_drafted_card` and `resolve_framework`.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_review.py tests/test_supervisor_service.py tests/test_tool_registry.py tests/test_routes_board.py tests/test_board_service.py -v` (expected: all pass). Then run `python -m ruff check services/tools services/supervisor_service.py services/board_service.py tests/test_tools_review.py tests/test_supervisor_service.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add services/tools services/supervisor_service.py services/board_service.py tests/test_tools_review.py tests/test_supervisor_service.py tests/test_tool_registry.py tests/test_routes_board.py
git commit -m "Add evaluate_research_output and put the Supervisor's drafting and review on fixed tool menus"
```

---

### Task 6: update_task_status, with the board's status buttons going through it

**Files:**
- Create: `services/tools/status.py`, `tests/test_tools_status.py`
- Modify: `services/tools/__init__.py`, `services/board_service.py` (status actions become wrappers)

**Interfaces:**
- Consumes: Tasks 2–5; `research_task_service` (`transition_task`, `claim_transition`, `dependencies_satisfied`, `phase7_readiness`); `research_work_items_repo.claim_status_if_unchanged`; `research_execution_service.RUNNABLE_METHODS` / `PHASE7_LOCKED_MESSAGE`; `prompt_drafting_service.FALLBACK_MARKER`.
- Produces:
  - Tool `update_task_status` (callers: user, system). Inputs `{task_id, action}`.
    - The user's actions are `approve`, `back_to_draft`, `skip`, `restore`, `retry`, `accept`, `mark_failed` and `needs_followup`. The system's only action is `run_failed`.
    - It returns `{"task_id", "new_status"}`.
    - An action the caller may not use returns `not_allowed`.
  - The board_service `approve`, `unapprove`, `skip`, `restore`, `retry`, `back_to_draft`, `accept`, `needs_followup` and `mark_failed` functions keep their signatures and exceptions, but now call the tool. `unapprove` maps to `back_to_draft`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools_status.py`:

```python
import json

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo, tool_calls_repo
from services import llm_service, prompt_drafting_service, research_task_service, tools

GOOD = "Research the fee structures of every online Psychology postgraduate program."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_service, "prompt_completion", lambda s, u, max_tokens=4000: "ROLE: analyst. Drafted follow-up prompt.")
    return projects_repo.get_or_create_id("P")


def _card(pid, status="PROPOSED", prompt=GOOD, method="TARGETED_WEB", **kw):
    card = research_task_service.create_task(pid, "4", "Fees", research_method=method, prompt_text=prompt, **kw)
    if status != "PROPOSED":
        research_work_items_repo.update_fields(card, status=status)
    return card


def _act(card, action, caller="user"):
    return tools.run_tool("update_task_status", caller, "P", {"task_id": card, "action": action})


@pytest.mark.parametrize("action,start,end", [
    ("approve", "PROPOSED", "READY"), ("back_to_draft", "READY", "PROPOSED"), ("back_to_draft", "FAILED", "PROPOSED"),
    ("skip", "PROPOSED", "SKIPPED"), ("restore", "SKIPPED", "PROPOSED"), ("retry", "FAILED", "READY"),
    ("accept", "WAITING_FOR_HUMAN", "COMPLETE"), ("mark_failed", "WAITING_FOR_HUMAN", "FAILED"),
])
def test_user_actions(pid, action, start, end):
    card = _card(pid, status=start)
    result = _act(card, action)
    assert result.data == {"task_id": card, "new_status": end}
    assert research_work_items_repo.get(card)["status"] == end
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"].startswith("user_")


def test_needs_followup_creates_a_logged_child(pid):
    card = _card(pid, status="WAITING_FOR_HUMAN")
    research_work_items_repo.update_fields(card, identified_gaps_json=json.dumps(["No intake dates"]))
    result = _act(card, "needs_followup")
    assert result.data["new_status"] == "FOLLOW_UP_REQUIRED"
    children = [r for r in tool_calls_repo.list_for_project(pid) if r["parent_call_id"] == result.call_id]
    assert [c["tool"] for c in children] == ["create_followup_task"]


def test_system_marks_a_failed_run(pid):
    card = _card(pid, status="RUNNING")
    assert _act(card, "run_failed", caller="system").data["new_status"] == "FAILED"


@pytest.mark.parametrize("caller,action", [("user", "run_failed"), ("system", "approve"), ("supervisor", "approve"),
                                           ("supervisor", "back_to_draft"), ("supervisor", "run_failed")])
def test_callers_only_get_their_actions(pid, caller, action):
    card = _card(pid, status="RUNNING" if action == "run_failed" else "PROPOSED")
    assert _act(card, action, caller=caller).error["code"] == "not_allowed"


@pytest.mark.parametrize("prompt,method", [("Too short", "TARGETED_WEB"), (None, "TARGETED_WEB"),
                                           (prompt_drafting_service.FALLBACK_MARKER + " Research question: Fees and more", "TARGETED_WEB"),
                                           (GOOD, None)])
def test_approve_refuses_cards_not_ready_to_send(pid, prompt, method):
    card = _card(pid, prompt=prompt, method=method)
    assert _act(card, "approve").error["code"] == "invalid_input"
    assert research_work_items_repo.get(card)["status"] == "PROPOSED"


def test_approve_in_the_wrong_state_is_a_conflict(pid):
    card = _card(pid, status="COMPLETE")
    assert _act(card, "approve").error == {
        "code": "conflict", "fields": [],
        "message": "This research has changed since the board was loaded. The board has been refreshed."}


def test_retry_limit_message(pid):
    card = _card(pid, status="FAILED")
    research_work_items_repo.update_fields(card, retry_count=3)
    assert "too many times" in _act(card, "retry").error["message"]


def test_unknown_action_is_invalid(pid):
    assert _act(_card(pid), "launch").error["code"] == "invalid_input"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_status.py -v`
Expected: FAIL (`update_task_status` isn't registered).

- [ ] **Step 3: Implement**

`services/tools/status.py`. Move the action logic **verbatim** from `services/board_service.py`, with exceptions replaced by `ToolError`. The messages must stay identical, because the board shows them.

```python
# services/tools/status.py
"""Status changes the user makes on the board, and the one the system makes when a run fails.
The Supervisor cannot call this tool at all."""
from typing import Literal

from db.repositories import research_work_items_repo

from .. import research_execution_service, research_task_service
from ..prompt_drafting_service import FALLBACK_MARKER
from ..text_utils import as_text_list
from .activity import record_user_action
from .registry import Tool, ToolError, register, run_tool
from .types import ToolInput

MIN_PROMPT_CHARS = 40
USER_ACTIONS = ("approve", "back_to_draft", "skip", "restore", "retry", "accept", "mark_failed", "needs_followup")
SYSTEM_ACTIONS = ("run_failed",)
CHANGED = "This research has changed since the board was loaded. The board has been refreshed."
WAITS = "This research waits for another research to finish first."
_FRIENDLY = (
    ("dependency", WAITS),
    ("Retry limit", "This research has failed too many times to retry. Move it back to draft to change it, or skip it."),
)


class StatusInput(ToolInput):
    task_id: int
    action: Literal["approve", "back_to_draft", "skip", "restore", "retry", "accept", "mark_failed",
                    "needs_followup", "run_failed"]


def _require(card, allowed):
    if card["status"] not in allowed:
        raise ToolError("conflict", CHANGED)


def _transition(card_id, to_status):
    try:
        research_task_service.transition_task(card_id, to_status)
    except ValueError as exc:
        for needle, message in _FRIENDLY:
            if needle in str(exc):
                raise ToolError("conflict", message) from exc
        raise ToolError("conflict", str(exc)) from exc


def _claim(card_id, from_status, to_status):
    if not research_task_service.claim_transition(card_id, from_status, to_status):
        raise ToolError("conflict", CHANGED)


def _approve(ctx, card):
    _require(card, ("PROPOSED",))
    prompt = (card["prompt_text"] or "").strip()
    if not (card["title"] or "").strip():
        raise ToolError("invalid_input", "Add a title before approving.")
    if card["research_method"] not in research_execution_service.RUNNABLE_METHODS:
        raise ToolError("invalid_input", "Choose how it runs before approving.")
    if FALLBACK_MARKER in prompt:
        raise ToolError("invalid_input", "This prompt is a placeholder because drafting failed. Edit it, or re-draft it from the framework, before approving.")
    if len(prompt) < MIN_PROMPT_CHARS:
        raise ToolError("invalid_input", "The research prompt is too short to send. Describe what the research should find out.")
    if card["research_method"] == "SYNTHESIS" and not research_task_service.phase7_readiness(card["project_id"])["unlocked"]:
        raise ToolError("conflict", research_execution_service.PHASE7_LOCKED_MESSAGE)
    if not research_task_service.dependencies_satisfied(card["id"]):
        raise ToolError("conflict", WAITS)
    # Only approve the exact card that was validated above; any edit since then wins.
    if not research_work_items_repo.claim_status_if_unchanged(card["id"], "PROPOSED", "READY", card["updated_at"]):
        raise ToolError("conflict", CHANGED)
    record_user_action(ctx, "user_approve", card["id"])
    return "READY"


def _back_to_draft(ctx, card):
    _require(card, ("READY", "FAILED"))
    if card["status"] == "READY":
        _claim(card["id"], "READY", "PROPOSED")
    else:
        _transition(card["id"], "PROPOSED")
    record_user_action(ctx, "user_back_to_draft", card["id"])
    return "PROPOSED"


def _simple(allowed, to_status, decision):
    def action(ctx, card):
        _require(card, allowed)
        _transition(card["id"], to_status)
        record_user_action(ctx, decision, card["id"])
        return to_status
    return action


def _needs_followup(ctx, card):
    _require(card, ("WAITING_FOR_HUMAN",))
    _transition(card["id"], "REVIEWING")
    _transition(card["id"], "FOLLOW_UP_REQUIRED")
    gaps = as_text_list(_json_or_empty(card["identified_gaps_json"]))
    rationale = "You asked for a follow-up." + (f" Gaps found: {'; '.join(gaps)}" if gaps else "")
    focus = as_text_list(_json_or_empty(card["entities_json"]))
    child = run_tool("create_followup_task", ctx.caller, ctx.project_name,
                     {"task_id": card["id"], "title": f"Follow-up: {card['title']}"[:200], "focus": focus,
                      "rationale": rationale[:1000]}, parent_call_id=ctx.call_id)
    if not child.ok:
        raise ToolError(child.error["code"], child.error["message"])
    record_user_action(ctx, "user_needs_followup", card["id"])
    return "FOLLOW_UP_REQUIRED"


def _json_or_empty(raw):
    import json
    try:
        return json.loads(raw) if raw else []
    except ValueError:
        return []


def _run_failed(ctx, card):
    _require(card, ("RUNNING",))
    _transition(card["id"], "FAILED")
    return "FAILED"


_ACTIONS = {
    "approve": _approve,
    "back_to_draft": _back_to_draft,
    "skip": _simple(("PROPOSED", "READY", "FAILED"), "SKIPPED", "user_skip"),
    "restore": _simple(("SKIPPED",), "PROPOSED", "user_restore"),
    "retry": _simple(("FAILED",), "READY", "user_retry"),
    "accept": _simple(("WAITING_FOR_HUMAN",), "COMPLETE", "user_accept"),
    "mark_failed": _simple(("WAITING_FOR_HUMAN",), "FAILED", "user_mark_failed"),
    "needs_followup": _needs_followup,
    "run_failed": _run_failed,
}


def update_task_status(ctx, inputs):
    allowed = USER_ACTIONS if ctx.caller == "user" else SYSTEM_ACTIONS
    if inputs.action not in allowed:
        raise ToolError("not_allowed", f"{inputs.action} cannot be used by the {ctx.caller}.")
    card = research_work_items_repo.get(inputs.task_id)
    return {"task_id": inputs.task_id, "new_status": _ACTIONS[inputs.action](ctx, card)}


register(Tool(
    name="update_task_status",
    description="Change a research card's status (the person's board actions, or the system marking a failed run).",
    input_model=StatusInput, handler=update_task_status,
    callers=frozenset({"user", "system"}), id_fields=("task_id",),
))
```

Move `import json` to the top of the module, which ruff requires. It's shown inline above only to keep the helper next to its use.

Append to `services/tools/__init__.py`: `from . import status  # noqa: E402,F401  (registers update_task_status)`.

In `services/board_service.py`, replace the bodies of the status actions with wrappers, keeping `ACTIONS` and its keys:

```python
def _status(project_name, card_id, action):
    _user_tool(project_name, "update_task_status", {"task_id": card_id, "action": action})


def approve(project_name, card_id):
    _status(project_name, card_id, "approve")


def unapprove(project_name, card_id):
    _status(project_name, card_id, "back_to_draft")


def skip(project_name, card_id):
    _status(project_name, card_id, "skip")


def restore(project_name, card_id):
    _status(project_name, card_id, "restore")


def retry(project_name, card_id):
    _status(project_name, card_id, "retry")


def back_to_draft(project_name, card_id):
    _status(project_name, card_id, "back_to_draft")


def accept(project_name, card_id):
    _status(project_name, card_id, "accept")


def needs_followup(project_name, card_id):
    _status(project_name, card_id, "needs_followup")


def mark_failed(project_name, card_id):
    _status(project_name, card_id, "mark_failed")
```

Delete helpers in `board_service.py` that nothing uses any more (`_transition`, `_claim`, `_FRIENDLY_TRANSITION_ERRORS` and `MIN_PROMPT_CHARS`, if they're unused). Keep `_card`, `_require_status`, `_CHANGED_MESSAGE` and `_record`, which `edit_card`, `redraft`, `draft_options_report` and `save_objective` still use. If a test in `tests/test_board_service.py` references a removed name, or patches `board_service.<x>` to simulate a race, point it at the new location (`services.tools.status` or `research_task_service`). Keep the test's intent.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_status.py tests/test_board_service.py tests/test_routes_board.py -v` (expected: all pass; the board tests are the regression check for messages and HTTP codes). Then run `python -m ruff check services/tools services/board_service.py tests/test_tools_status.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add services/tools services/board_service.py tests/test_tools_status.py tests/test_board_service.py
git commit -m "Add update_task_status; the board's status buttons go through it"
```

---

### Task 7: launch_deep_research, check_research_run and generate_synthesis, with Run and refresh going through them

**Files:**
- Create: `services/tools/runs.py`, `tests/test_tools_runs.py`
- Modify: `services/tools/__init__.py`, `services/research_execution_service.py` (single-run sync), `services/board_service.py` (`run`, `refresh`, `_settle_card`)

**Interfaces:**
- Consumes: Tasks 2–6; `research_execution_service.start_runs`.
- Produces:
  - `research_execution_service.sync_web_research_run(project_name, run) -> None`. It checks one run with OpenAI and may raise. `needs_web_sync(run) -> bool` is a public alias of `_needs_web_sync`.
  - Tool `launch_deep_research` (caller: user). Inputs `{task_ids: [1..20]}`; returns `{"started","not_ready","failed"}`. SYNTHESIS cards are put in `failed` with a message.
  - Tool `generate_synthesis` (caller: user). Inputs `{task_id}`; same return shape. A non-SYNTHESIS card returns `conflict`.
  - Tool `check_research_run` (caller: system). Inputs `{task_id}`; returns `{"task_id","run_status"}`. A card with no run returns `not_found`.
  - `board_service.run(project_name, card_ids)` keeps its signature and return shape.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools_runs.py`:

```python
from types import SimpleNamespace

import pytest

from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_runs.py -v`
Expected: FAIL (the tools aren't registered).

- [ ] **Step 3: Implement**

In `services/research_execution_service.py`, split the loop body of `sync_web_research_runs` into a single-run function, keeping its behaviour:

```python
def needs_web_sync(run):
    return _needs_web_sync(run)


def sync_web_research_run(project_name, run):
    """Check one web run with OpenAI and store its outcome. Raises if OpenAI can't be reached."""
    response = openai_service.retrieve_deep_research(run["response_id"])
    status = getattr(response, "status", None)
    if status == "completed":
        text, _markdown, citations = extract_deep_research_output(response)
        if text:
            research_run_service.update_run(
                project_name, run["id"], status="completed",
                output_text=_format_web_research_output(text, citations),
                completed_at=datetime.now().isoformat(),
            )
        else:
            research_run_service.fail_run(
                project_name, run["id"], "OpenAI reported the research finished but returned no text"
            )
    elif status in ("failed", "incomplete", "cancelled"):
        research_run_service.fail_run(
            project_name, run["id"], _web_research_error(response) or f"OpenAI reported status: {status}"
        )
    # queued / in_progress: still working; checked again on the next refresh.


def sync_web_research_runs(project_name):
    """Check every unfinished web run of the project (kept for callers that want all at once)."""
    project_id = projects_repo.get_or_create_id(project_name)
    for item in research_work_items_repo.list_for_project(project_id):
        run = research_runs_repo.find_latest_for_work_item(item["id"])
        if not _needs_web_sync(run):
            continue
        try:
            sync_web_research_run(project_name, run)
        except Exception as exc:  # one flaky lookup must not block the whole refresh
            print(f"[board] could not check web research run {run['id']}: {exc}")
```

`services/tools/runs.py`:

```python
# services/tools/runs.py
"""Starting research (the user's Run) and checking web runs (the system's refresh).
The Supervisor cannot call any of these."""
from pydantic import Field

from db.repositories import research_runs_repo, research_work_items_repo

from .. import research_execution_service
from .registry import Tool, ToolError, register
from .types import ToolInput

SYNTHESIS_ELSEWHERE = "The options report is started with its own button."


class LaunchInput(ToolInput):
    task_ids: list[int] = Field(min_length=1, max_length=20)


def launch_deep_research(ctx, inputs):
    synthesis, others = [], []
    for card_id in inputs.task_ids:
        (synthesis if research_work_items_repo.get(card_id)["research_method"] == "SYNTHESIS" else others).append(card_id)
    result = (research_execution_service.start_runs(ctx.project_name, others) if others
              else {"started": [], "not_ready": [], "failed": []})
    result["failed"].extend({"id": card_id, "error": SYNTHESIS_ELSEWHERE} for card_id in synthesis)
    return result


class TaskInput(ToolInput):
    task_id: int


def generate_synthesis(ctx, inputs):
    if research_work_items_repo.get(inputs.task_id)["research_method"] != "SYNTHESIS":
        raise ToolError("conflict", "This research isn't the options report.")
    return research_execution_service.start_runs(ctx.project_name, [inputs.task_id])


def check_research_run(ctx, inputs):
    run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    if run is None:
        raise ToolError("not_found", "This research has not been run.")
    if research_execution_service.needs_web_sync(run):
        research_execution_service.sync_web_research_run(ctx.project_name, run)
        run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    return {"task_id": inputs.task_id, "run_status": run["status"]}


USER_ONLY = frozenset({"user"})
register(Tool(
    name="launch_deep_research",
    description="Start web or 'my files' research on cards the person has approved. Costs money.",
    input_model=LaunchInput, handler=launch_deep_research, callers=USER_ONLY, id_fields=("task_ids",),
))
register(Tool(
    name="generate_synthesis",
    description="Start the Phase 7 options report on the approved options card.",
    input_model=TaskInput, handler=generate_synthesis, callers=USER_ONLY, id_fields=("task_id",),
))
register(Tool(
    name="check_research_run",
    description="Ask OpenAI whether a web research run has finished, and collect its report if so.",
    input_model=TaskInput, handler=check_research_run, callers=frozenset({"system"}), id_fields=("task_id",),
))
```

Append to `services/tools/__init__.py`: `from . import runs  # noqa: E402,F401  (registers the run tools)`.

In `services/board_service.py`:

```python
def run(project_name, card_ids):
    try:
        ids = [int(i) for i in card_ids]
    except (TypeError, ValueError) as exc:
        raise ValueError("card_ids must be a list of research ids") from exc
    synthesis, others = [], []
    for card_id in ids:
        row = research_work_items_repo.get(card_id)
        (synthesis if row is not None and row["research_method"] == "SYNTHESIS" else others).append(card_id)
    result = {"started": [], "not_ready": [], "failed": []}
    parts = ([_user_tool(project_name, "launch_deep_research", {"task_ids": others})] if others else []) + [
        _user_tool(project_name, "generate_synthesis", {"task_id": card_id}) for card_id in synthesis
    ]
    for part in parts:
        for key in result:
            result[key].extend(part[key])
    if result["started"]:
        _record(project_name, "user_run", count=len(result["started"]))
    return result
```

In `refresh`, replace `research_execution_service.sync_web_research_runs(project_name)` with:

```python
            for card in research_work_items_repo.list_for_project(project_id):
                if card["status"] == "RUNNING" and card["research_method"] == "TARGETED_WEB":
                    run_row = research_runs_repo.find_latest_for_work_item(card["id"])
                    if research_execution_service.needs_web_sync(run_row):
                        run_tool("check_research_run", "system", project_name, {"task_id": card["id"]})
```

(A failed check is logged by `run_tool` and retried on the next refresh.)

In `_settle_card`, replace `research_task_service.transition_task(card["id"], "FAILED")` with:

```python
        run_tool("update_task_status", "system", project_name, {"task_id": card["id"], "action": "run_failed"})
```

Remove imports that ruff then reports unused in `board_service.py`.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_runs.py tests/test_board_service.py tests/test_routes_board.py tests/test_research_execution_service.py tests/test_research_execution_sync.py -v` (expected: all pass). Then run `python -m ruff check services/tools services/board_service.py services/research_execution_service.py tests/test_tools_runs.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add services/tools services/board_service.py services/research_execution_service.py tests/test_tools_runs.py
git commit -m "Add launch, synthesis and run-check tools; the Run button and refresh go through them"
```

---

### Task 8: The acceptance tests — what the Supervisor can reach, and the safety rules

**Files:**
- Create: `tests/test_tools_structure.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the full registry (Tasks 2–7), `supervisor_service.DRAFTING_MENU` / `REVIEW_MENU`.

- [ ] **Step 1: Write the tests**

`tests/test_tools_structure.py`:

```python
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
```

- [ ] **Step 2: Run them**

Run `python -m pytest tests/test_tools_structure.py -v`.
Expected: PASS. If something fails, it has found a real gap: fix the tool or registry, not the test. Then run `python -m ruff check tests/test_tools_structure.py` and `python -m pytest tests/ -q`, and `node tests/js/test_board_view.js`.

- [ ] **Step 3: Document**

In `CLAUDE.md`, under **Backend → Services**, add:

```markdown
  - `tools/` — the Supervisor tool layer: every Supervisor action, and the board's status/run actions, go through `tools.run_tool(name, caller, project, inputs)` — pydantic-validated inputs, structured results, a per-tool list of allowed callers (`supervisor` / `user` / `system`) and a log row in `tool_calls`. The Supervisor can only use the tools on its menus (`supervisor_service.DRAFTING_MENU`, `REVIEW_MENU`); anything that spends money or changes what runs is user/system-only. To let the Supervisor use a tool later, add `"supervisor"` to that tool's `callers` (and to a menu).
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_tools_structure.py CLAUDE.md
git commit -m "Add acceptance tests for the Supervisor tool layer and document it"
```

- [ ] **Step 5 (controller): Browser walkthrough**

Start `python scripts/run_board_demo.py` and re-run the stubbed Research tab walkthrough: draft, edit, approval gate, run, automatic review, follow-up, report reader, Insights links, add research, project switch. Any failure goes back through the normal fix loop. Stop the demo server afterwards.

The demo's fake Anthropic client picks its reply by looking for the tool name `propose_tasks`. Update `scripts/run_board_demo.py` so it recognises `create_research_task` (drafting) and answers reviews with the `evaluate_research_output` tool name, then commit that change with the walkthrough.
