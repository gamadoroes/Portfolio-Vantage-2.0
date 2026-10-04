# Phase 3 — Research Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single LLM-driven supervisor that decides the next research action for a project — proposing tasks, dispatching eligible ones, reviewing outcomes, spawning follow-ups, and gating final synthesis — using Anthropic native tool-use for guaranteed structured output, with every decision persisted and inspectable.

**Architecture:** One new service module (`services/supervisor_service.py`) holds nine tool schemas and their handlers, a context builder, and the single orchestration entry point `run_supervisor_cycle`. Every handler mutates state exclusively through the existing validated `research_task_service`/`research_run_service` layer from Phase 2 — never a repository directly. One new route blueprint exposes it; one new additive UI panel surfaces it.

**Tech Stack:** Flask, `anthropic` SDK's native tool-use (`tool_choice: {"type": "any"}`), stdlib `sqlite3` via the existing repo layer, pytest with mocked LLM/OpenAI boundaries, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-04-research-supervisor-design.md`

## Global Constraints

- Do not modify `research_tasks`, `findings`, `evidence`, `project_insight_versions`, `db/migrate.py`, `db/migrate_runner.py`, `routes/ai.py`, or any part of `static/app.js`'s existing Phase 1-7 prompt-building/generation flow. (Spec §11)
- Every handler function has the signature `handle_X(project_name, tool_input) -> dict`. Handlers never catch `ValueError` themselves — raising it IS the "this request is invalid" signal. Only `run_supervisor_cycle` (Task 9) catches `ValueError` and converts it to `{"success": False, "error": str(exc)}`. (Spec §7, resolved during plan-writing for `review_outcome` and `trigger_synthesis`)
- Any handler that mutates `research_work_items` or `research_runs` state must call `research_task_service`/`research_run_service` — never `research_work_items_repo`/`research_runs_repo` directly for a write. Direct repo reads are fine when no validation is needed. (Spec §4's Execution Safety Rule)
- `TOOL_SCHEMAS` must match the JSON schemas in spec §4.1-4.9 exactly: same `name`, same `required` fields, same `enum` values.
- Tests must mock the LLM/OpenAI boundary — `services.supervisor_service.llm_service.prompt_completion`, `services.supervisor_service.openai_service.start_deep_research`, and (for orchestration/route-level tests) `services.supervisor_service.anthropic.Anthropic` — never make a real external API call.
- `research_method` value is always exactly `"FILE_ANALYSIS"` or `"TARGETED_WEB"` (spec §2) — no other value is ever legal input to `dispatch_task`/`create_followup_task`.
- No handler wraps its multiple repository/service calls in an explicit database transaction (consistent with every other service in this codebase — Phase 2's `create_task`/`add_dependency` aren't transactional together either). A `propose_tasks` call whose dependency-wiring step fails partway (e.g. a cycle on the second of several dependency edges) leaves any already-created tasks and already-wired edges in place — this is accepted, not a defect to fix: the orchestrator still records exactly one failed decision for the whole call, and any orphaned `PROPOSED` tasks remain fully visible and actionable (a human or a later supervisor cycle can still see and `skip_task` them).

## Review Focus

- **A `FAILED` outcome in `review_outcome` must transition directly from `RUNNING`, never through `REVIEWING`.** Phase 2's transition table has no `REVIEWING→FAILED` edge — routing through `REVIEWING` first would make the state machine reject the transition. Task 7 pins this with a dedicated test asserting the final status is `FAILED` with no error, distinct from the `COMPLETE`/`FOLLOW_UP_REQUIRED` tests that do pass through `REVIEWING`.
- **`trigger_synthesis`'s prerequisite gate must reject when *any one* of phases 1-6 lacks a `COMPLETE` task, not only when all six lack one.** An implementer could plausibly write an `all()`-style check that only catches the total-absence case. Task 8's test seeds five phases with a `COMPLETE` task and leaves exactly one without, asserting the gate still rejects.
- **`propose_tasks`'s batch-index dependency resolution must map indices to the actual newly-created ids, not treat the LLM-supplied indices as real ids.** Task 5's test creates a two-task batch where task 0 depends on task 1 (via `depends_on_batch_indices: [1]`) and asserts the real foreign key matches whatever id the second task actually received from the database — not the literal integer `1`.
- **A cycle or cross-project dependency violation raised deep inside `propose_tasks`/`create_followup_task` must surface all the way to the orchestrator as a failed, logged decision — never silently swallowed or left unrecorded.** (Any already-created tasks from the same batch are accepted to remain in place per the Global Constraints above — the requirement here is that the *decision itself* is honestly recorded as failed, not that the database rolls back.) Task 9's test exercises this through `run_supervisor_cycle` end-to-end (not just a direct handler call), confirming the decision is recorded with `success: False` and an error message containing "cycle".
- **The context builder must not leak another project's `research_work_items` or `agent_decisions`.** Every repo function here is already scoped by `project_id`, but an unscoped query would be an easy, high-impact mistake to introduce and miss without an explicit multi-project test. Task 3's test seeds two separate projects and asserts each one's context contains only its own data.

---

### Task 1: Migration — `agent_decisions`/`research_runs` columns

**Files:**
- Create: `db/migrations/0004_agent_decisions_work_items.sql`
- Test: `tests/test_db_migrations.py` (modify)

**Interfaces:**
- Produces: columns `agent_decisions.research_work_item_id`, `research_runs.output_text`. All later tasks depend on these existing.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db_migrations.py`:

```python
def test_apply_migrations_is_idempotent(temp_db):
    apply_migrations()
    apply_migrations()
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    assert count == 4


def test_agent_decisions_and_research_runs_schema(temp_db):
    with get_connection() as conn:
        decision_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(agent_decisions)").fetchall()
        }
        assert "research_work_item_id" in decision_columns

        run_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
        }
        assert "output_text" in run_columns
```

This replaces the existing `test_apply_migrations_is_idempotent` (count changes from 3 to 4) and adds one new test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db_migrations.py -v`
Expected: `test_agent_decisions_and_research_runs_schema` FAILS (columns don't exist); `test_apply_migrations_is_idempotent` FAILS (count is 3, not 4).

- [ ] **Step 3: Write the migration**

Create `db/migrations/0004_agent_decisions_work_items.sql`:

```sql
-- Phase 3 (Research Supervisor). Fully additive: two new nullable columns.
-- Does NOT touch research_tasks, findings, evidence, or project_insight_versions.
-- agent_decisions.research_task_id (the legacy Phase-1 FK) is untouched and
-- unused by this phase -- decisions from the supervisor reference
-- research_work_items instead, via this new column.
-- See docs/superpowers/specs/2026-10-04-research-supervisor-design.md section 3.

ALTER TABLE agent_decisions ADD COLUMN research_work_item_id INTEGER REFERENCES research_work_items(id);

-- FILE_ANALYSIS research runs complete synchronously via Claude, not via
-- OpenAI's response_id-based polling, so there's nowhere today to store
-- that kind of run's output text.
ALTER TABLE research_runs ADD COLUMN output_text TEXT;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db_migrations.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0004_agent_decisions_work_items.sql tests/test_db_migrations.py
git commit -m "Add agent_decisions/research_runs columns for Phase 3 supervisor"
```

---

### Task 2: Service and Repository Additions

**Files:**
- Modify: `db/repositories/research_runs_repo.py`
- Modify: `db/repositories/agent_decisions_repo.py`
- Modify: `services/research_task_service.py`
- Test: `tests/test_repositories_research_runs.py` (modify)
- Test: `tests/test_repositories_agent_decisions.py` (create)
- Test: `tests/test_research_task_service.py` (modify)

**Interfaces:**
- Consumes: Task 1's new columns.
- Produces: `research_runs_repo.find_latest_for_work_item(work_item_id) -> Row|None`; `agent_decisions_repo.record(project_id, decision_type, detail, research_task_id=None, research_work_item_id=None) -> int`; `agent_decisions_repo.list_for_project(project_id, limit=50) -> list[Row]`; `research_task_service.set_review_scores(task_id, completeness_score=None, evidence_score=None, identified_gaps=None) -> None`; `research_task_service.flag_for_human_review(task_id) -> None`. Tasks 3-8 consume these exact names/signatures.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repositories_research_runs.py`:

```python
def test_find_latest_for_work_item(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, None, None, "first")
    research_runs_repo.update("run_1", research_work_item_id=5)
    research_runs_repo.create("run_2", pid, None, None, "second")
    research_runs_repo.update("run_2", research_work_item_id=5)

    latest = research_runs_repo.find_latest_for_work_item(5)
    assert latest["id"] == "run_2"


def test_find_latest_for_work_item_none_when_no_runs(temp_db):
    assert research_runs_repo.find_latest_for_work_item(9999) is None


def test_find_latest_for_work_item_scoped_to_correct_item(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_a", pid, None, None, "for item 1")
    research_runs_repo.update("run_a", research_work_item_id=1)
    research_runs_repo.create("run_b", pid, None, None, "for item 2")
    research_runs_repo.update("run_b", research_work_item_id=2)

    assert research_runs_repo.find_latest_for_work_item(1)["id"] == "run_a"
    assert research_runs_repo.find_latest_for_work_item(2)["id"] == "run_b"
```

(`research_runs_repo` and `projects_repo` are already imported at the top of this file by the existing tests — no new import needed.)

Create `tests/test_repositories_agent_decisions.py`:

```python
from db.repositories import agent_decisions_repo, projects_repo


def test_record_without_work_item_id(temp_db):
    pid = projects_repo.get_or_create_id("P")
    decision_id = agent_decisions_repo.record(pid, "NO_ACTION", '{"reason": "nothing to do"}')
    assert isinstance(decision_id, int)


def test_record_with_work_item_id(temp_db):
    pid = projects_repo.get_or_create_id("P")
    decision_id = agent_decisions_repo.record(
        pid, "SKIP_TASK", '{"task_id": 7}', research_work_item_id=7
    )
    assert isinstance(decision_id, int)


def test_list_for_project_ordering_and_limit(temp_db):
    pid = projects_repo.get_or_create_id("P")
    for i in range(5):
        agent_decisions_repo.record(pid, f"ACTION_{i}", "{}")

    all_decisions = agent_decisions_repo.list_for_project(pid)
    assert len(all_decisions) == 5
    assert all_decisions[0]["decision_type"] == "ACTION_4"  # most recent first

    limited = agent_decisions_repo.list_for_project(pid, limit=2)
    assert len(limited) == 2
    assert limited[0]["decision_type"] == "ACTION_4"


def test_list_for_project_scoped_to_project(temp_db):
    pid1 = projects_repo.get_or_create_id("P1")
    pid2 = projects_repo.get_or_create_id("P2")
    agent_decisions_repo.record(pid1, "ACTION_P1", "{}")
    agent_decisions_repo.record(pid2, "ACTION_P2", "{}")

    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid1)] == ["ACTION_P1"]
    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid2)] == ["ACTION_P2"]
```

Append to `tests/test_research_task_service.py`:

```python
def test_set_review_scores_updates_only_provided_fields(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_task_service.set_review_scores(task_id, completeness_score=0.7)
    row = research_work_items_repo.get(task_id)
    assert row["completeness_score"] == 0.7
    assert row["evidence_score"] is None


def test_set_review_scores_serializes_gaps_to_json(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_task_service.set_review_scores(task_id, identified_gaps=["Missing pricing"])
    row = research_work_items_repo.get(task_id)
    assert row["identified_gaps_json"] == '["Missing pricing"]'


def test_flag_for_human_review_sets_flag(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_task_service.flag_for_human_review(task_id)
    row = research_work_items_repo.get(task_id)
    assert row["human_review_required"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_repositories_research_runs.py tests/test_repositories_agent_decisions.py tests/test_research_task_service.py -v`
Expected: the new tests FAIL — `find_latest_for_work_item`/`list_for_project`/`set_review_scores`/`flag_for_human_review` don't exist yet; `test_repositories_agent_decisions.py` fails at import/collection since the module has no `list_for_project`. The existing `record` calls in the new file also fail until `research_work_item_id` is accepted as a kwarg.

- [ ] **Step 3: Write the implementation**

Append to `db/repositories/research_runs_repo.py`:

```python
def find_latest_for_work_item(work_item_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_runs WHERE research_work_item_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (work_item_id,),
        ).fetchone()
```

Replace `db/repositories/agent_decisions_repo.py` in full:

```python
from datetime import datetime

from ..connection import get_connection


def record(project_id, decision_type, detail, research_task_id=None, research_work_item_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO agent_decisions "
            "(project_id, research_task_id, research_work_item_id, decision_type, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, research_task_id, research_work_item_id, decision_type, detail, now),
        )
        return cur.lastrowid


def list_for_project(project_id, limit=50):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM agent_decisions WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
```

Append to `services/research_task_service.py`:

```python
def set_review_scores(task_id, completeness_score=None, evidence_score=None, identified_gaps=None):
    fields = {}
    if completeness_score is not None:
        fields["completeness_score"] = completeness_score
    if evidence_score is not None:
        fields["evidence_score"] = evidence_score
    if identified_gaps is not None:
        fields["identified_gaps_json"] = json.dumps(identified_gaps)
    if fields:
        research_work_items_repo.update_fields(task_id, **fields)


def flag_for_human_review(task_id):
    research_work_items_repo.update_fields(task_id, human_review_required=1)
```

(`json` and `research_work_items_repo` are already imported at the top of `services/research_task_service.py` by the existing Phase 2 code — no new imports needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_repositories_research_runs.py tests/test_repositories_agent_decisions.py tests/test_research_task_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add db/repositories/research_runs_repo.py db/repositories/agent_decisions_repo.py services/research_task_service.py tests/test_repositories_research_runs.py tests/test_repositories_agent_decisions.py tests/test_research_task_service.py
git commit -m "Add service/repo additions for Phase 3: run lookup, decision listing, score/flag setters"
```

---

### Task 3: Context Builder

**Files:**
- Create: `services/supervisor_service.py`
- Test: `tests/test_supervisor_service.py` (create)

**Interfaces:**
- Consumes: `research_work_items_repo.list_for_project`/`.list_dependencies` (Phase 2), `agent_decisions_repo.list_for_project` (Task 2), `insights_service.load_current_insights` (existing), `services.phases.PHASE_DEFINITIONS` (existing), `services.project_service.load_project_prompt` (existing), `db.repositories.projects_repo.get_or_create_id` (existing).
- Produces: `build_context(project_name) -> str`. Takes `project_name`, not `project_id` — matching the convention every other function in this codebase already uses (`load_project_prompt`, `insights_service.load_current_insights`, etc. all take a name; `projects_repo` has no id-to-name lookup, only `get_or_create_id`/`get_id`/`list_all`, and `list_all()`'s rows don't even carry an `id` field — so resolving a name from an id is not a sensible operation to build here). Task 9's orchestration calls this exact function with the `project_name` it already has. This task creates the file `services/supervisor_service.py` — Tasks 4-9 all append to this same file.

- [ ] **Step 1: Write the failing test**

Create `tests/test_supervisor_service.py`:

```python
from db.repositories import projects_repo, research_work_items_repo
from services import insights_service, project_service, supervisor_service


def test_build_context_includes_objective(temp_db):
    projects_repo.get_or_create_id("P")
    project_service.save_project_prompt("P", "project_prompt", "Assess the online MBA market.")
    context = supervisor_service.build_context("P")
    assert "Assess the online MBA market." in context


def test_build_context_includes_phase_definitions(temp_db):
    projects_repo.get_or_create_id("P")
    context = supervisor_service.build_context("P")
    assert "The Landscape" in context  # phase "1"
    assert "Options for OES" in context  # phase "7"


def test_build_context_includes_existing_phase_summary(temp_db):
    projects_repo.get_or_create_id("P")
    insights_service.save_insights("P", {
        "generated_at": "2026-01-01T00:00:00", "competitors": [],
        "competitor_landscape_markdown": "",
        "phases": {str(i): {"title": f"Phase {i}", "summary": "Established fact" if i == 1 else "MISSING",
                             "confidence": "high" if i == 1 else "none", "evidence_sources": [], "gaps": [],
                             "suggested_topics": [], "linked_files": [], "linked_file_ids": []}
                   for i in range(1, 8)},
    })
    context = supervisor_service.build_context("P")
    assert "Established fact" in context


def test_build_context_includes_work_items_and_dependencies(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "Product / La Trobe", priority="high")
    b = research_work_items_repo.create(pid, "4", "Product / Torrens")
    research_work_items_repo.add_dependency(b, a)

    context = supervisor_service.build_context("P")
    assert "Product / La Trobe" in context
    assert "Product / Torrens" in context
    assert str(a) in context  # the dependency should be visible by id


def test_build_context_includes_recent_decisions(temp_db):
    from db.repositories import agent_decisions_repo
    pid = projects_repo.get_or_create_id("P")
    agent_decisions_repo.record(pid, "NO_ACTION", '{"reason": "nothing ready yet"}')

    context = supervisor_service.build_context("P")
    assert "NO_ACTION" in context
    assert "nothing ready yet" in context


def test_build_context_does_not_leak_other_projects(temp_db):
    pid1 = projects_repo.get_or_create_id("P1")
    pid2 = projects_repo.get_or_create_id("P2")
    research_work_items_repo.create(pid1, "4", "P1-only task")
    research_work_items_repo.create(pid2, "4", "P2-only task")
    from db.repositories import agent_decisions_repo
    agent_decisions_repo.record(pid1, "PROPOSE_TASKS", '{"note": "p1 decision"}')
    agent_decisions_repo.record(pid2, "PROPOSE_TASKS", '{"note": "p2 decision"}')

    context1 = supervisor_service.build_context("P1")
    context2 = supervisor_service.build_context("P2")

    assert "P1-only task" in context1
    assert "P2-only task" not in context1
    assert "p1 decision" in context1
    assert "p2 decision" not in context1

    assert "P2-only task" in context2
    assert "P1-only task" not in context2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.supervisor_service'`.

- [ ] **Step 3: Write the implementation**

Create `services/supervisor_service.py`:

```python
# services/supervisor_service.py
from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo

from . import insights_service
from .phases import PHASE_DEFINITIONS
from .project_service import load_project_prompt

MAX_CONTEXT_CHARS = 60000


def _format_work_item(item, dependencies):
    dep_ids = [d["depends_on_work_item_id"] for d in dependencies]
    dep_text = f" | depends on: {dep_ids}" if dep_ids else ""
    return (
        f"- id={item['id']} phase={item['phase_key']} title=\"{item['title']}\" "
        f"status={item['status']} priority={item['priority']} "
        f"completeness={item['completeness_score']} evidence={item['evidence_score']} "
        f"retry={item['retry_count']}/{item['max_retries']} "
        f"human_review_required={bool(item['human_review_required'])}{dep_text}"
    )


def build_context(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    blocks = []

    objective = load_project_prompt(project_name, "project_prompt")
    blocks.append(f"# PROJECT OBJECTIVE\n\n{objective or '(none set)'}")

    blocks.append(
        "# PHASE DEFINITIONS\n\n"
        + "\n".join(f"- {key}: {defn['title']}" for key, defn in PHASE_DEFINITIONS.items())
    )

    current = insights_service.load_current_insights(project_name)
    phase_lines = []
    for key, phase in current["phases"].items():
        summary = phase.get("summary", "MISSING")
        if summary and summary != "MISSING":
            phase_lines.append(f"- Phase {key} ({PHASE_DEFINITIONS[key]['title']}): {summary}")
    blocks.append(
        "# EXISTING PHASE FINDINGS (read-only; you never modify these directly)\n\n"
        + ("\n".join(phase_lines) if phase_lines else "(no phase findings established yet)")
    )

    items = research_work_items_repo.list_for_project(project_id)
    item_lines = [_format_work_item(item, research_work_items_repo.list_dependencies(item["id"])) for item in items]
    blocks.append(
        "# RESEARCH TASKS\n\n" + ("\n".join(item_lines) if item_lines else "(no research tasks yet)")
    )

    decisions = agent_decisions_repo.list_for_project(project_id, limit=10)
    decision_lines = [f"- {d['decision_type']}: {d['detail']}" for d in decisions]
    blocks.append(
        "# RECENT SUPERVISOR DECISIONS (most recent first)\n\n"
        + ("\n".join(decision_lines) if decision_lines else "(no prior decisions)")
    )

    text = "\n\n".join(blocks)
    if len(text) > MAX_CONTEXT_CHARS:
        # Drop the oldest decisions first (least useful context), then hard-clip.
        decisions = agent_decisions_repo.list_for_project(project_id, limit=3)
        decision_lines = [f"- {d['decision_type']}: {d['detail']}" for d in decisions]
        blocks[-1] = (
            "# RECENT SUPERVISOR DECISIONS (most recent first, truncated)\n\n"
            + ("\n".join(decision_lines) if decision_lines else "(no prior decisions)")
        )
        text = "\n\n".join(blocks)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n\n[...context truncated for length...]"

    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/supervisor_service.py tests/test_supervisor_service.py
git commit -m "Add supervisor context builder"
```

---

### Task 4: Tool Schemas + Simple Handlers

**Files:**
- Modify: `services/supervisor_service.py`
- Test: `tests/test_supervisor_service.py` (append)

**Interfaces:**
- Consumes: `research_task_service.transition_task`/`.flag_for_human_review` (Phase 2 / Task 2), `research_work_items_repo.get` (Phase 2, read-only).
- Produces: `TOOL_SCHEMAS` (list of 9 dicts — all nine defined now, even though only four have handlers yet); `TOOL_HANDLERS` (dict, four entries: `mark_ready`, `skip_task`, `no_action`, `request_human_review`); `handle_mark_ready`, `handle_skip_task`, `handle_no_action`, `handle_request_human_review`. Tasks 5-8 each append more entries to this same `TOOL_HANDLERS` dict — do not redeclare it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_supervisor_service.py`:

```python
import pytest

from services import research_task_service


def test_tool_schemas_has_nine_tools_with_correct_names():
    names = {schema["name"] for schema in supervisor_service.TOOL_SCHEMAS}
    assert names == {
        "propose_tasks", "mark_ready", "dispatch_task", "review_outcome",
        "create_followup_task", "request_human_review", "skip_task",
        "trigger_synthesis", "no_action",
    }


def test_handle_mark_ready_transitions_to_ready(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    result = supervisor_service.handle_mark_ready("P", {"task_id": task_id, "reason": "deps satisfied"})
    assert result["new_status"] == "READY"
    assert research_work_items_repo.get(task_id)["status"] == "READY"


def test_handle_mark_ready_raises_on_illegal_transition(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="COMPLETE")
    with pytest.raises(ValueError):
        supervisor_service.handle_mark_ready("P", {"task_id": task_id, "reason": "x"})


def test_handle_skip_task_transitions_to_skipped(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    result = supervisor_service.handle_skip_task("P", {"task_id": task_id, "reason": "no longer useful"})
    assert result["new_status"] == "SKIPPED"
    assert research_work_items_repo.get(task_id)["status"] == "SKIPPED"


def test_handle_no_action_is_a_noop(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    result = supervisor_service.handle_no_action("P", {"reason": "nothing ready"})
    assert "message" in result
    assert research_work_items_repo.get(task_id)["status"] == "PROPOSED"  # untouched


def test_handle_request_human_review_sets_flag_only_when_not_reviewing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")  # status PROPOSED
    result = supervisor_service.handle_request_human_review("P", {"task_id": task_id, "reason": "uncertain"})
    row = research_work_items_repo.get(task_id)
    assert row["human_review_required"] == 1
    assert row["status"] == "PROPOSED"  # unchanged, since it wasn't REVIEWING
    assert result["flagged_only"] is True


def test_handle_request_human_review_transitions_when_reviewing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="REVIEWING")
    result = supervisor_service.handle_request_human_review("P", {"task_id": task_id, "reason": "uncertain"})
    row = research_work_items_repo.get(task_id)
    assert row["human_review_required"] == 1
    assert row["status"] == "WAITING_FOR_HUMAN"
    assert result["new_status"] == "WAITING_FOR_HUMAN"


def test_handle_request_human_review_raises_for_unknown_task(temp_db):
    with pytest.raises(ValueError):
        supervisor_service.handle_request_human_review("P", {"task_id": 9999, "reason": "x"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor_service.py -v -k "tool_schemas or handle_mark_ready or handle_skip_task or handle_no_action or handle_request_human_review"`
Expected: FAIL with `AttributeError` — none of `TOOL_SCHEMAS`/`TOOL_HANDLERS`/the four handler functions exist yet.

- [ ] **Step 3: Write the implementation**

Append to `services/supervisor_service.py`:

```python
from . import research_task_service

TOOL_SCHEMAS = [
    {
        "name": "propose_tasks",
        "description": "Create one or more new research tasks. Use this for the initial research plan, or to fill a gap identified later. Supports dependencies on existing tasks (by their real id) and on other tasks proposed in this same call (by their zero-based index in the tasks array).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "phase_key": {"type": "string", "description": "One of '1' through '7', matching the fixed phase definitions."},
                            "title": {"type": "string"},
                            "objective": {"type": "string"},
                            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                            "entities": {"type": "array", "items": {"type": "string"}},
                            "expected_output": {"type": "string"},
                            "depends_on_existing_ids": {"type": "array", "items": {"type": "integer"}},
                            "depends_on_batch_indices": {"type": "array", "items": {"type": "integer"}, "description": "Zero-based indices into this same tasks array."},
                        },
                        "required": ["phase_key", "title"],
                    },
                },
                "reason": {"type": "string", "description": "Why these tasks are needed now."},
            },
            "required": ["tasks", "reason"],
        },
    },
    {
        "name": "mark_ready",
        "description": "Transition a PROPOSED or FOLLOW_UP_REQUIRED task to READY, making it eligible for dispatch. Only legal if all of its dependencies are COMPLETE or SKIPPED -- the backend re-checks this regardless of what you believe.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
    },
    {
        "name": "dispatch_task",
        "description": "Transition a READY task to RUNNING and start the chosen research method. FILE_ANALYSIS runs synchronously against the project's uploaded source files and completes before this call returns. TARGETED_WEB starts an async web-research job -- its outcome is reviewed on a LATER supervisor call via review_outcome, not this one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "research_method": {"type": "string", "enum": ["FILE_ANALYSIS", "TARGETED_WEB"]},
                "reason": {"type": "string", "description": "Why this method was chosen over the other."},
            },
            "required": ["task_id", "research_method", "reason"],
        },
    },
    {
        "name": "review_outcome",
        "description": "Review a RUNNING task whose research has finished (its linked run has status completed or failed) and record the outcome. Do not call this for a task whose run is still in progress.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "completeness_score": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence_score": {"type": "number", "minimum": 0, "maximum": 1},
                "identified_gaps": {"type": "array", "items": {"type": "string"}},
                "outcome": {"type": "string", "enum": ["COMPLETE", "FOLLOW_UP_REQUIRED", "FAILED"]},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "outcome", "reason"],
        },
    },
    {
        "name": "create_followup_task",
        "description": "Mark a task as needing follow-up research and create a new dependent task to fill the gap. The original task will become eligible for READY again once the new follow-up task completes or is skipped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "The task that needs follow-up."},
                "followup_title": {"type": "string"},
                "followup_objective": {"type": "string"},
                "research_method": {"type": "string", "enum": ["FILE_ANALYSIS", "TARGETED_WEB"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "followup_title", "reason"],
        },
    },
    {
        "name": "request_human_review",
        "description": "Flag a task as requiring human judgment before it can be marked complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
    },
    {
        "name": "skip_task",
        "description": "Abandon a task permanently. Use when it's no longer useful to pursue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
    },
    {
        "name": "trigger_synthesis",
        "description": "Request final Phase 7 (Options for OES) synthesis across all prior phases. Only call this when you believe every phase has sufficient completed research. The backend will verify this independently and reject the request if prerequisites are not actually met.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "no_action",
        "description": "Nothing useful can be done right now (e.g. all eligible tasks are already running, or everything is blocked on an async result). Use this instead of forcing an action that doesn't make sense.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
]


def handle_mark_ready(project_name, tool_input):
    task_id = tool_input["task_id"]
    research_task_service.transition_task(task_id, "READY")
    return {"task_id": task_id, "new_status": "READY"}


def handle_skip_task(project_name, tool_input):
    task_id = tool_input["task_id"]
    research_task_service.transition_task(task_id, "SKIPPED")
    return {"task_id": task_id, "new_status": "SKIPPED"}


def handle_no_action(project_name, tool_input):
    return {"message": "No action taken."}


def handle_request_human_review(project_name, tool_input):
    task_id = tool_input["task_id"]
    item = research_work_items_repo.get(task_id)
    if item is None:
        raise ValueError(f"No such research task: {task_id}")
    research_task_service.flag_for_human_review(task_id)
    if item["status"] == "REVIEWING":
        research_task_service.transition_task(task_id, "WAITING_FOR_HUMAN")
        return {"task_id": task_id, "new_status": "WAITING_FOR_HUMAN"}
    return {"task_id": task_id, "new_status": item["status"], "flagged_only": True}


TOOL_HANDLERS = {
    "mark_ready": handle_mark_ready,
    "skip_task": handle_skip_task,
    "no_action": handle_no_action,
    "request_human_review": handle_request_human_review,
}
```

`research_work_items_repo` is already imported at module scope (Task 3's `from db.repositories import ...` line) — no new production-code import needed for `update_fields`/`get`. `pytest` and `research_task_service` are now used by the test file; add `from services import research_task_service` and `import pytest` to the top of `tests/test_supervisor_service.py` alongside its existing imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor_service.py -v`
Expected: all PASS (Task 3's and Task 4's tests together).

- [ ] **Step 5: Commit**

```bash
git add services/supervisor_service.py tests/test_supervisor_service.py
git commit -m "Add tool schemas and simple supervisor handlers (mark_ready, skip_task, no_action, request_human_review)"
```

---

### Task 5: `propose_tasks` and `create_followup_task` Handlers

**Files:**
- Modify: `services/supervisor_service.py`
- Test: `tests/test_supervisor_service.py` (append)

**Interfaces:**
- Consumes: `research_task_service.create_task`/`.add_dependency`/`.transition_task` (Phase 2), `projects_repo.get_or_create_id` (existing).
- Produces: `handle_propose_tasks`, `handle_create_followup_task`, added to the existing `TOOL_HANDLERS` dict (do not redeclare the dict — add two more key/value pairs to it).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_supervisor_service.py`:

```python
def test_handle_propose_tasks_creates_tasks(temp_db):
    projects_repo.get_or_create_id("P")
    result = supervisor_service.handle_propose_tasks("P", {
        "tasks": [{"phase_key": "4", "title": "Product / La Trobe"}],
        "reason": "initial plan",
    })
    assert len(result["created_task_ids"]) == 1
    row = research_work_items_repo.get(result["created_task_ids"][0])
    assert row["title"] == "Product / La Trobe"
    assert row["status"] == "PROPOSED"


def test_handle_propose_tasks_wires_existing_id_dependency(temp_db):
    pid = projects_repo.get_or_create_id("P")
    existing_id = research_work_items_repo.create(pid, "1", "Landscape task")
    result = supervisor_service.handle_propose_tasks("P", {
        "tasks": [{"phase_key": "4", "title": "Dependent task", "depends_on_existing_ids": [existing_id]}],
        "reason": "needs landscape first",
    })
    new_id = result["created_task_ids"][0]
    deps = [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(new_id)]
    assert deps == [existing_id]


def test_handle_propose_tasks_wires_batch_index_dependency_to_real_id(temp_db):
    projects_repo.get_or_create_id("P")
    result = supervisor_service.handle_propose_tasks("P", {
        "tasks": [
            {"phase_key": "4", "title": "Depends on second task", "depends_on_batch_indices": [1]},
            {"phase_key": "4", "title": "The second task"},
        ],
        "reason": "ordering matters",
    })
    first_id, second_id = result["created_task_ids"]
    deps = [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(first_id)]
    # Must be the SECOND task's real database id -- not the literal index "1".
    assert deps == [second_id]


def test_handle_propose_tasks_cycle_raises_value_error(temp_db):
    # Two tasks in the SAME batch mutually depending on each other via
    # batch indices is a genuine cycle: the handler creates both tasks
    # first, then wires dependencies in array order. Wiring task 0 -> task 1
    # succeeds (no edges exist yet); wiring task 1 -> task 0 immediately
    # after closes a real two-node cycle, since task 0 already depends on
    # task 1 at that point.
    projects_repo.get_or_create_id("P")
    with pytest.raises(ValueError, match="cycle"):
        supervisor_service.handle_propose_tasks("P", {
            "tasks": [
                {"phase_key": "4", "title": "X", "depends_on_batch_indices": [1]},
                {"phase_key": "4", "title": "Y", "depends_on_batch_indices": [0]},
            ],
            "reason": "mutually dependent tasks",
        })


def test_handle_create_followup_task_marks_original_and_creates_dependency(temp_db):
    pid = projects_repo.get_or_create_id("P")
    original_id = research_work_items_repo.create(pid, "4", "Product / Torrens")
    research_work_items_repo.update_fields(original_id, status="REVIEWING")

    result = supervisor_service.handle_create_followup_task("P", {
        "task_id": original_id,
        "followup_title": "Verify Torrens tuition fee from official source",
        "followup_objective": "Find an authoritative source for the current tuition fee.",
        "research_method": "TARGETED_WEB",
        "priority": "high",
        "reason": "Current fee was not from an authoritative source.",
    })

    original_row = research_work_items_repo.get(original_id)
    assert original_row["status"] == "FOLLOW_UP_REQUIRED"

    followup_id = result["followup_task_id"]
    followup_row = research_work_items_repo.get(followup_id)
    assert followup_row["title"] == "Verify Torrens tuition fee from official source"
    assert followup_row["phase_key"] == "4"

    deps = [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(original_id)]
    assert deps == [followup_id]


def test_handle_create_followup_task_raises_for_unknown_task(temp_db):
    with pytest.raises(ValueError):
        supervisor_service.handle_create_followup_task("P", {
            "task_id": 9999, "followup_title": "x", "reason": "x",
        })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor_service.py -v -k "propose_tasks or create_followup_task"`
Expected: FAIL with `AttributeError` — `handle_propose_tasks`/`handle_create_followup_task` don't exist yet.

- [ ] **Step 3: Write the implementation**

Append to `services/supervisor_service.py`:

```python
def handle_propose_tasks(project_name, tool_input):
    project_id = projects_repo.get_or_create_id(project_name)
    tasks_input = tool_input["tasks"]
    new_ids = []
    for t in tasks_input:
        task_id = research_task_service.create_task(
            project_id,
            t["phase_key"],
            t["title"],
            objective=t.get("objective"),
            priority=t.get("priority"),
            entities=t.get("entities"),
            expected_output=t.get("expected_output"),
        )
        new_ids.append(task_id)

    for i, t in enumerate(tasks_input):
        task_id = new_ids[i]
        for dep_id in t.get("depends_on_existing_ids") or []:
            research_task_service.add_dependency(task_id, dep_id)
        for dep_index in t.get("depends_on_batch_indices") or []:
            research_task_service.add_dependency(task_id, new_ids[dep_index])

    return {"created_task_ids": new_ids}


def handle_create_followup_task(project_name, tool_input):
    project_id = projects_repo.get_or_create_id(project_name)
    original_id = tool_input["task_id"]
    original = research_work_items_repo.get(original_id)
    if original is None:
        raise ValueError(f"No such research task: {original_id}")

    research_task_service.transition_task(original_id, "FOLLOW_UP_REQUIRED")
    new_id = research_task_service.create_task(
        project_id,
        original["phase_key"],
        tool_input["followup_title"],
        objective=tool_input.get("followup_objective"),
        priority=tool_input.get("priority"),
        research_method=tool_input.get("research_method"),
    )
    research_task_service.add_dependency(original_id, new_id)

    return {"original_task_id": original_id, "followup_task_id": new_id}


TOOL_HANDLERS["propose_tasks"] = handle_propose_tasks
TOOL_HANDLERS["create_followup_task"] = handle_create_followup_task
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/supervisor_service.py tests/test_supervisor_service.py
git commit -m "Add propose_tasks and create_followup_task supervisor handlers"
```

---

### Task 6: `dispatch_task` Handler

**Files:**
- Modify: `services/supervisor_service.py`
- Test: `tests/test_supervisor_service.py` (append)

**Interfaces:**
- Consumes: `research_task_service.transition_task` (Phase 2), `research_run_service.create_run`/`.update_run` (existing), `llm_service.prompt_completion` (existing, mocked in tests), `openai_service.start_deep_research` (existing, mocked in tests), `services.file_service.load_project_files` (existing), `services.file_index_service.HIDDEN_SOURCE_FILES`/`.reconcile_file_index`/`.reconcile_selected_file_ids` (existing).
- Produces: `handle_dispatch_task`, added to `TOOL_HANDLERS["dispatch_task"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_supervisor_service.py`:

```python
def test_handle_dispatch_task_file_analysis_completes_synchronously(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from services import project_service
    project_service.create_project("P")
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task", objective="Find the tuition fee.")
    research_work_items_repo.update_fields(task_id, status="READY")

    monkeypatch.setattr(
        supervisor_service.llm_service, "prompt_completion",
        lambda system_prompt, user_message, max_tokens=3000: "The fee is $40,000.",
    )

    result = supervisor_service.handle_dispatch_task("P", {
        "task_id": task_id, "research_method": "FILE_ANALYSIS", "reason": "files are available",
    })

    assert result["status"] == "completed"
    task_row = research_work_items_repo.get(task_id)
    assert task_row["status"] == "RUNNING"  # dispatch never reviews -- that's a separate call
    run = research_runs_repo.find_latest_for_work_item(task_id)
    assert run["status"] == "completed"
    assert run["output_text"] == "The fee is $40,000."


def test_handle_dispatch_task_targeted_web_starts_async_job(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from services import project_service
    project_service.create_project("P")
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task", objective="Find the tuition fee.")
    research_work_items_repo.update_fields(task_id, status="READY")

    class FakeResponse:
        id = "resp_123"
        status = "queued"

    monkeypatch.setattr(
        supervisor_service.openai_service, "start_deep_research",
        lambda prompt: FakeResponse(),
    )

    result = supervisor_service.handle_dispatch_task("P", {
        "task_id": task_id, "research_method": "TARGETED_WEB", "reason": "needs live web data",
    })

    assert result["status"] == "running"
    task_row = research_work_items_repo.get(task_id)
    assert task_row["status"] == "RUNNING"
    run = research_runs_repo.find_latest_for_work_item(task_id)
    assert run["status"] == "running"
    assert run["response_id"] == "resp_123"


def test_handle_dispatch_task_raises_for_unknown_task(temp_db):
    with pytest.raises(ValueError):
        supervisor_service.handle_dispatch_task("P", {
            "task_id": 9999, "research_method": "FILE_ANALYSIS", "reason": "x",
        })
```

(`research_runs_repo` needs to be imported at the top of `tests/test_supervisor_service.py`: add `from db.repositories import research_runs_repo` alongside the existing repo imports.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor_service.py -v -k dispatch_task`
Expected: FAIL with `AttributeError: module 'services.supervisor_service' has no attribute 'handle_dispatch_task'`.

- [ ] **Step 3: Write the implementation**

Append to `services/supervisor_service.py`:

```python
import json
from datetime import datetime

from . import llm_service, openai_service, research_run_service
from .file_index_service import HIDDEN_SOURCE_FILES, reconcile_file_index, reconcile_selected_file_ids
from .file_service import load_project_files

MAX_FILE_ANALYSIS_TOTAL_CHARS = 60000
MAX_FILE_ANALYSIS_PER_FILE_CHARS = 15000


def _selected_project_files(project_name):
    all_files = {
        k: v for k, v in load_project_files(project_name).items()
        if k not in HIDDEN_SOURCE_FILES
    }
    index_entries = reconcile_file_index(project_name)
    selection_state = reconcile_selected_file_ids(project_name, index_entries)
    selected_files = selection_state.get("selected_files", [])
    if selected_files:
        return {k: v for k, v in all_files.items() if k in set(selected_files)}
    return all_files


def _build_file_analysis_prompt(task_row, files):
    objective = task_row["objective"] or task_row["title"]
    entities = json.loads(task_row["entities_json"]) if task_row["entities_json"] else []
    expected_output = task_row["expected_output"] or "a clear, evidence-based answer"
    entities_line = f"\nEntities of interest: {', '.join(entities)}" if entities else ""

    system_prompt = (
        "You are a research analyst for an Australian higher-education competitive "
        "analysis project. Use ONLY the provided source files. If the objective "
        "cannot be answered from them, say MISSING -- do not guess or use general "
        "knowledge.\n\n"
        f"OBJECTIVE: {objective}{entities_line}\n"
        f"EXPECTED OUTPUT: {expected_output}"
    )

    blocks = []
    used = 0
    for filename, content in files.items():
        text = content if isinstance(content, str) else str(content or "")
        if len(text) > MAX_FILE_ANALYSIS_PER_FILE_CHARS:
            head = MAX_FILE_ANALYSIS_PER_FILE_CHARS // 2
            text = text[:head] + "\n\n[...truncated...]\n\n" + text[-(MAX_FILE_ANALYSIS_PER_FILE_CHARS - head):]
        block = f"## {filename}\n\n{text}\n\n---\n\n"
        if used + len(block) > MAX_FILE_ANALYSIS_TOTAL_CHARS:
            break
        blocks.append(block)
        used += len(block)

    user_message = "# SOURCE DATA\n\n" + ("".join(blocks) if blocks else "(no source files available)")
    return system_prompt, user_message


def handle_dispatch_task(project_name, tool_input):
    task_id = tool_input["task_id"]
    method = tool_input["research_method"]
    task_row = research_work_items_repo.get(task_id)
    if task_row is None:
        raise ValueError(f"No such research task: {task_id}")

    research_task_service.transition_task(task_id, "RUNNING")
    prompt_preview = task_row["objective"] or task_row["title"]
    run_id = research_run_service.create_run(project_name, None, None, prompt_preview)
    research_run_service.update_run(project_name, run_id, research_work_item_id=task_id)

    if method == "FILE_ANALYSIS":
        files = _selected_project_files(project_name)
        system_prompt, user_message = _build_file_analysis_prompt(task_row, files)
        output_text = llm_service.prompt_completion(system_prompt, user_message, max_tokens=3000)
        research_run_service.update_run(
            project_name, run_id, status="completed", output_text=output_text,
            completed_at=datetime.now().isoformat(),
        )
        return {"task_id": task_id, "run_id": run_id, "research_method": method, "status": "completed"}

    # TARGETED_WEB
    response = openai_service.start_deep_research(prompt_preview)
    research_run_service.update_run(project_name, run_id, response_id=response.id)
    return {"task_id": task_id, "run_id": run_id, "research_method": method, "status": "running"}


TOOL_HANDLERS["dispatch_task"] = handle_dispatch_task
```

Note: this introduces `import json`/`from datetime import datetime` and several new
`from . import ...`/`from .module import ...` lines at a point partway through the
file. Place all import statements at the top of `services/supervisor_service.py`
(standard Python style — consolidate every import added across Tasks 3-9 into the
single import block at the top of the file, not scattered mid-file) rather than
literally appending them where shown above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/supervisor_service.py tests/test_supervisor_service.py
git commit -m "Add dispatch_task supervisor handler (FILE_ANALYSIS + TARGETED_WEB)"
```

---

### Task 7: `review_outcome` Handler

**Files:**
- Modify: `services/supervisor_service.py`
- Test: `tests/test_supervisor_service.py` (append)

**Interfaces:**
- Consumes: `research_runs_repo.find_latest_for_work_item` (Task 2), `research_task_service.set_review_scores`/`.transition_task` (Task 2 / Phase 2).
- Produces: `handle_review_outcome`, added to `TOOL_HANDLERS["review_outcome"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_supervisor_service.py`:

```python
def _dispatched_task_with_run(pid, status="completed"):
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="RUNNING")
    run_id = f"run_{task_id}"
    research_runs_repo.create(run_id, pid, None, None, "preview")
    research_runs_repo.update(run_id, research_work_item_id=task_id, status=status)
    return task_id


def test_handle_review_outcome_raises_when_no_finished_run(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _dispatched_task_with_run(pid, status="running")
    with pytest.raises(ValueError):
        supervisor_service.handle_review_outcome("P", {
            "task_id": task_id, "outcome": "COMPLETE", "reason": "x",
        })


def test_handle_review_outcome_raises_when_no_run_at_all(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="RUNNING")
    with pytest.raises(ValueError):
        supervisor_service.handle_review_outcome("P", {
            "task_id": task_id, "outcome": "COMPLETE", "reason": "x",
        })


def test_handle_review_outcome_complete_goes_through_reviewing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _dispatched_task_with_run(pid, status="completed")
    result = supervisor_service.handle_review_outcome("P", {
        "task_id": task_id, "outcome": "COMPLETE", "completeness_score": 0.9,
        "evidence_score": 0.8, "identified_gaps": [], "reason": "fully answered",
    })
    assert result["outcome"] == "COMPLETE"
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "COMPLETE"
    assert row["completeness_score"] == 0.9


def test_handle_review_outcome_follow_up_required(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _dispatched_task_with_run(pid, status="completed")
    result = supervisor_service.handle_review_outcome("P", {
        "task_id": task_id, "outcome": "FOLLOW_UP_REQUIRED",
        "identified_gaps": ["Missing authoritative source"], "reason": "weak evidence",
    })
    assert result["outcome"] == "FOLLOW_UP_REQUIRED"
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "FOLLOW_UP_REQUIRED"
    assert row["identified_gaps_json"] == '["Missing authoritative source"]'


def test_handle_review_outcome_failed_skips_reviewing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _dispatched_task_with_run(pid, status="failed")
    result = supervisor_service.handle_review_outcome("P", {
        "task_id": task_id, "outcome": "FAILED", "reason": "run errored out",
    })
    assert result["outcome"] == "FAILED"
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "FAILED"
    # No exception was raised getting here -- confirms RUNNING->FAILED was taken
    # directly, since RUNNING->REVIEWING->FAILED is illegal per Phase 2's table
    # (REVIEWING has no FAILED edge) and would have raised ValueError instead.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor_service.py -v -k review_outcome`
Expected: FAIL with `AttributeError: module 'services.supervisor_service' has no attribute 'handle_review_outcome'`.

- [ ] **Step 3: Write the implementation**

Append to `services/supervisor_service.py`:

```python
def handle_review_outcome(project_name, tool_input):
    task_id = tool_input["task_id"]
    outcome = tool_input["outcome"]
    run = research_runs_repo.find_latest_for_work_item(task_id)
    if run is None or run["status"] == "running":
        raise ValueError(f"No finished run to review for task {task_id}")

    research_task_service.set_review_scores(
        task_id,
        completeness_score=tool_input.get("completeness_score"),
        evidence_score=tool_input.get("evidence_score"),
        identified_gaps=tool_input.get("identified_gaps"),
    )

    if outcome == "FAILED":
        # REVIEWING has no FAILED edge in Phase 2's transition table -- must
        # go directly from RUNNING, or this would raise ValueError.
        research_task_service.transition_task(task_id, "FAILED")
    else:
        research_task_service.transition_task(task_id, "REVIEWING")
        research_task_service.transition_task(task_id, outcome)

    return {"task_id": task_id, "outcome": outcome, "run_id": run["id"]}


TOOL_HANDLERS["review_outcome"] = handle_review_outcome
```

Add `from db.repositories import research_runs_repo` to the consolidated
import block at the top of `services/supervisor_service.py` (per Task 6's
note) if not already present from an earlier task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/supervisor_service.py tests/test_supervisor_service.py
git commit -m "Add review_outcome supervisor handler"
```

---

### Task 8: `trigger_synthesis` Handler

**Files:**
- Modify: `services/supervisor_service.py`
- Test: `tests/test_supervisor_service.py` (append)

**Interfaces:**
- Consumes: `research_work_items_repo.list_for_phase` (Phase 2), `insights_service.load_current_insights`/`.save_insights` (existing), `llm_service.prompt_completion` (existing, mocked in tests).
- Produces: `handle_trigger_synthesis`, added to `TOOL_HANDLERS["trigger_synthesis"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_supervisor_service.py`:

```python
def _complete_task_for_phase(pid, phase_key):
    task_id = research_work_items_repo.create(pid, phase_key, f"Task for phase {phase_key}")
    research_work_items_repo.update_fields(task_id, status="COMPLETE")
    return task_id


def test_handle_trigger_synthesis_rejects_when_one_phase_missing(temp_db):
    pid = projects_repo.get_or_create_id("P")
    # Phases 1-5 have a COMPLETE task; phase 6 does not.
    for phase_key in ["1", "2", "3", "4", "5"]:
        _complete_task_for_phase(pid, phase_key)

    with pytest.raises(ValueError, match="6"):
        supervisor_service.handle_trigger_synthesis("P", {"reason": "think we're done"})


def test_handle_trigger_synthesis_succeeds_when_all_six_phases_complete(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from services import project_service
    project_service.create_project("P")
    pid = projects_repo.get_or_create_id("P")
    for phase_key in ["1", "2", "3", "4", "5", "6"]:
        _complete_task_for_phase(pid, phase_key)

    monkeypatch.setattr(
        supervisor_service.llm_service, "prompt_completion",
        lambda system_prompt, user_message, max_tokens=4000: "## Strategic Options\n\nDo X, then Y.",
    )

    result = supervisor_service.handle_trigger_synthesis("P", {"reason": "all phases complete"})
    assert result["synthesis_generated"] is True

    from services import insights_service
    current = insights_service.load_current_insights("P")
    assert current["phases"]["7"]["summary"] == "## Strategic Options\n\nDo X, then Y."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor_service.py -v -k trigger_synthesis`
Expected: FAIL with `AttributeError: module 'services.supervisor_service' has no attribute 'handle_trigger_synthesis'`.

- [ ] **Step 3: Write the implementation**

Append to `services/supervisor_service.py`:

```python
SYNTHESIS_SYSTEM_PROMPT = (
    "You are a senior strategy consultant synthesising a competitive "
    "landscape analysis for Australian higher education. Using ONLY the "
    "phase summaries provided, identify the key strategic options available. "
    "Use the SO WHAT / NOW WHAT framework. Write in Australian English."
)


def handle_trigger_synthesis(project_name, tool_input):
    project_id = projects_repo.get_or_create_id(project_name)
    missing_phases = []
    for phase_key in PHASE_DEFINITIONS:
        if phase_key == "7":
            continue
        items = research_work_items_repo.list_for_phase(project_id, phase_key)
        if not any(item["status"] == "COMPLETE" for item in items):
            missing_phases.append(phase_key)
    if missing_phases:
        raise ValueError(f"Phases missing completed research: {', '.join(missing_phases)}")

    current = insights_service.load_current_insights(project_name)
    phase_summaries = []
    for phase_key in PHASE_DEFINITIONS:
        if phase_key == "7":
            continue
        phase = current["phases"].get(phase_key, {})
        summary = phase.get("summary", "MISSING")
        if summary and summary != "MISSING":
            title = PHASE_DEFINITIONS[phase_key]["title"]
            phase_summaries.append(f"## {title}\n\n{summary}")

    user_message = "\n\n".join(phase_summaries) if phase_summaries else "(no phase summaries available)"
    synthesis_text = llm_service.prompt_completion(SYNTHESIS_SYSTEM_PROMPT, user_message, max_tokens=4000)

    updated_phases = dict(current["phases"])
    phase_7 = dict(updated_phases.get("7", {}))
    phase_7["summary"] = synthesis_text
    phase_7["confidence"] = "medium"
    updated_phases["7"] = phase_7

    insights_service.save_insights(project_name, {
        "generated_at": datetime.now().isoformat(),
        "competitors": current.get("competitors", []),
        "competitor_landscape_markdown": current.get("competitor_landscape_markdown", ""),
        "phases": updated_phases,
    })

    return {"synthesis_generated": True}


TOOL_HANDLERS["trigger_synthesis"] = handle_trigger_synthesis
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/supervisor_service.py tests/test_supervisor_service.py
git commit -m "Add trigger_synthesis supervisor handler with backend-enforced gate"
```

---

### Task 9: Orchestration — `run_supervisor_cycle`

**Files:**
- Modify: `services/supervisor_service.py`
- Test: `tests/test_supervisor_service.py` (append)

**Interfaces:**
- Consumes: `TOOL_SCHEMAS`/`TOOL_HANDLERS`/`build_context` (Tasks 3-8), `agent_decisions_repo.record` (Task 2), Flask's `current_app.config` (existing, requires app context).
- Produces: `run_supervisor_cycle(project_name) -> dict`. Task 10's route calls this exact function.

- [ ] **Step 1: Write the failing test**

This task's tests call `agent_decisions_repo.list_for_project` at module
level (unlike Task 3's two uses of it, which were local imports inside
their own test functions). Add `agent_decisions_repo` to the top-level
import line of `tests/test_supervisor_service.py` now:
`from db.repositories import projects_repo, research_work_items_repo` (Task
3's original line, already extended with `research_runs_repo` by Task 6)
becomes
`from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo`.

Append to `tests/test_supervisor_service.py`:

```python
class _FakeToolUseBlock:
    def __init__(self, name, input):
        self.type = "tool_use"
        self.name = name
        self.input = input


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


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


def _patch_anthropic(monkeypatch, tool_name, tool_input, preceding_text=None):
    content = []
    if preceding_text:
        content.append(_FakeTextBlock(preceding_text))
    content.append(_FakeToolUseBlock(tool_name, tool_input))
    response = _FakeAnthropicResponse(content)
    monkeypatch.setattr(
        supervisor_service.anthropic, "Anthropic",
        lambda api_key: _FakeAnthropicClient(response),
    )


@pytest.fixture
def app_context():
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        yield


def test_run_supervisor_cycle_executes_and_records_decision(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    _patch_anthropic(monkeypatch, "mark_ready", {"task_id": task_id, "reason": "ready to go"})

    result = supervisor_service.run_supervisor_cycle("P")

    assert result["decision"]["action"] == "mark_ready"
    assert result["execution"]["success"] is True
    assert research_work_items_repo.get(task_id)["status"] == "READY"

    decisions = agent_decisions_repo.list_for_project(pid)
    assert len(decisions) == 1
    assert decisions[0]["decision_type"] == "mark_ready"
    assert decisions[0]["research_work_item_id"] == task_id


def test_run_supervisor_cycle_records_failed_decision_without_crashing(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(task_id, status="COMPLETE")
    _patch_anthropic(monkeypatch, "mark_ready", {"task_id": task_id, "reason": "trying anyway"})

    result = supervisor_service.run_supervisor_cycle("P")

    assert result["execution"]["success"] is False
    assert "error" in result["execution"]
    decisions = agent_decisions_repo.list_for_project(pid)
    assert len(decisions) == 1  # still recorded, even though execution failed


def test_run_supervisor_cycle_propagates_cycle_rejection_as_failed_decision(temp_db, app_context, monkeypatch):
    # Same mutual-dependency construction as Task 5's handler-level cycle
    # test, but driven end-to-end through run_supervisor_cycle: the (faked)
    # model proposes two tasks in one batch that depend on each other via
    # batch indices, which is a genuine cycle once both edges are wired.
    projects_repo.get_or_create_id("P")
    _patch_anthropic(monkeypatch, "propose_tasks", {
        "tasks": [
            {"phase_key": "4", "title": "X", "depends_on_batch_indices": [1]},
            {"phase_key": "4", "title": "Y", "depends_on_batch_indices": [0]},
        ],
        "reason": "mutually dependent tasks",
    })

    result = supervisor_service.run_supervisor_cycle("P")

    assert result["execution"]["success"] is False
    assert "cycle" in result["execution"]["error"].lower()
    decisions = agent_decisions_repo.list_for_project(projects_repo.get_or_create_id("P"))
    assert len(decisions) == 1
    assert decisions[0]["decision_type"] == "propose_tasks"


def test_run_supervisor_cycle_no_tool_use_block_raises(temp_db, app_context, monkeypatch):
    projects_repo.get_or_create_id("P")
    response = _FakeAnthropicResponse([_FakeTextBlock("I don't know what to do.")])
    monkeypatch.setattr(
        supervisor_service.anthropic, "Anthropic",
        lambda api_key: _FakeAnthropicClient(response),
    )
    with pytest.raises(RuntimeError):
        supervisor_service.run_supervisor_cycle("P")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor_service.py -v -k run_supervisor_cycle`
Expected: FAIL with `AttributeError: module 'services.supervisor_service' has no attribute 'run_supervisor_cycle'`.

- [ ] **Step 3: Write the implementation**

Append to `services/supervisor_service.py`:

```python
import anthropic
from flask import current_app

SUPERVISOR_SYSTEM_PROMPT = (
    "You are the research supervisor for a competitive-intelligence project "
    "in Australian higher education. You coordinate research across 7 fixed "
    "phases by managing a set of research tasks, each with its own status "
    "lifecycle. On every call, you must choose exactly ONE of the provided "
    "tools based on the current project state below. Prefer making forward "
    "progress: propose tasks for phases with no work yet, mark proposed "
    "tasks ready once their dependencies are satisfied, dispatch tasks that "
    "are READY, review tasks whose research has finished, and request human "
    "review or create follow-up tasks when evidence is weak. Only trigger "
    "final synthesis once every phase has real completed research."
)


def run_supervisor_cycle(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    context_text = build_context(project_name)

    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=2000,
        system=SUPERVISOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": context_text}],
        tools=TOOL_SCHEMAS,
        tool_choice={"type": "any"},
    )

    tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use_block is None:
        raise RuntimeError("Supervisor model did not return a tool call.")

    tool_name = tool_use_block.name
    tool_input = tool_use_block.input
    handler = TOOL_HANDLERS[tool_name]

    try:
        result = handler(project_name, tool_input)
        execution_result = {"success": True, "result": result}
    except ValueError as exc:
        execution_result = {"success": False, "error": str(exc)}

    agent_decisions_repo.record(
        project_id,
        decision_type=tool_name,
        detail=json.dumps({"input": tool_input, "execution": execution_result}),
        research_work_item_id=tool_input.get("task_id"),
    )

    return {
        "decision": {"action": tool_name, "input": tool_input},
        "execution": execution_result,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/supervisor_service.py tests/test_supervisor_service.py
git commit -m "Add run_supervisor_cycle orchestration entry point"
```

---

### Task 10: Routes — `routes/supervisor.py`

**Files:**
- Create: `routes/supervisor.py`
- Modify: `app.py:5-30` (add import and blueprint registration)
- Test: `tests/test_routes_supervisor.py` (create)

**Interfaces:**
- Consumes: `services.supervisor_service.run_supervisor_cycle` (Task 9), `db.repositories.agent_decisions_repo.list_for_project` (Task 2), `services.project_service.normalize_project_name`/`.project_exists` (existing), `db.repositories.projects_repo.get_or_create_id` (existing).
- Produces: blueprint `supervisor_bp` registered at `/api/supervisor`.

Routes:
- `POST /api/supervisor/run` — body `{project}` → `{decision, execution}` (whatever `run_supervisor_cycle` returned) or 400.
- `GET /api/supervisor/decisions?project=` → `{decisions: [...]}`, each with `id`, `decision_type`, `detail` (parsed JSON), `research_work_item_id`, `created_at`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_routes_supervisor.py`:

```python
import json

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_routes_supervisor.py -v`
Expected: FAIL with 404s (blueprint not registered / doesn't exist).

- [ ] **Step 3: Write the implementation**

Create `routes/supervisor.py`:

```python
import json

from flask import Blueprint, jsonify, request, session

from db.repositories import agent_decisions_repo, projects_repo
from services.project_service import normalize_project_name, project_exists
from services.supervisor_service import run_supervisor_cycle

supervisor_bp = Blueprint("supervisor", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


@supervisor_bp.route("/api/supervisor/run", methods=["POST"])
def run_supervisor_route():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400

    try:
        result = run_supervisor_cycle(project)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify(result)


@supervisor_bp.route("/api/supervisor/decisions", methods=["GET"])
def list_decisions_route():
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400

    project_id = projects_repo.get_or_create_id(project)
    rows = agent_decisions_repo.list_for_project(project_id)
    decisions = [
        {
            "id": row["id"],
            "decision_type": row["decision_type"],
            "detail": json.loads(row["detail"]) if row["detail"] else {},
            "research_work_item_id": row["research_work_item_id"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return jsonify({"decisions": decisions})
```

Modify `app.py` — add the import at the top (alongside the other route imports) and register the blueprint:

```python
from routes.supervisor import supervisor_bp
```

and, in `create_app()`, alongside the other `app.register_blueprint(...)` calls:

```python
    app.register_blueprint(supervisor_bp)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_routes_supervisor.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/supervisor.py app.py tests/test_routes_supervisor.py
git commit -m "Add routes/supervisor.py: run and decisions endpoints"
```

---

### Task 11: UI — Supervisor Panel

**Files:**
- Modify: `static/app.js` (append near the end of the Insights-rendering section, after `renderPhaseLinkedFiles`/Phase 2's task-panel functions)
- Modify: `static/style.css` (append after Phase 2's task-panel CSS rules)
- No automated tests (this codebase has zero JS test coverage, consistent with Phase 2's Task 7) — manual verification steps below.

**Interfaces:**
- Consumes: `POST /api/supervisor/run`, `GET /api/supervisor/decisions?project=` (Task 10).
- Produces: a "Supervisor" section rendered once per `renderInsights` call (reusing the Phase 2 precedent of an additive section appended via the same `showTasks`-gated render path — the supervisor section should ALSO be suppressed when `showTasks` is false, i.e. when viewing a historical insights version, for the identical reason Phase 2's task panel is: supervisor decisions are live/current state with no version concept, and showing them under a "(historical)" label would misrepresent frozen data as current).

- [ ] **Step 1: Add the Supervisor section container to the insights render**

In `static/app.js`, find the end of `renderInsights`'s body (the same function Phase 2 modified — look for the line `if (showTasks) refreshPhaseTaskPanels();` that Phase 2 added just before the function's closing brace). Add one more line immediately after it:

```javascript
    if (showTasks) refreshPhaseTaskPanels();
    if (showTasks) ensureSupervisorPanel();
}
```

Then find the phase cards container in the DOM (`document.getElementById('insight-phases')`, referenced earlier in `renderInsights`). The Supervisor panel is a sibling section, not inside any individual phase card — add a one-time container `insertAdjacentHTML` call inside `ensureSupervisorPanel` (defined in Step 2) that appends it after the phases container's parent, only if it doesn't already exist (so repeated `renderInsights` calls don't duplicate it).

- [ ] **Step 2: Add the rendering and data-fetching functions**

Add these new functions immediately after Phase 2's `transitionResearchTask` function (the last function Phase 2 added to this file):

```javascript
function ensureSupervisorPanel() {
    if (document.getElementById('supervisor-panel')) {
        refreshSupervisorDecisions();
        return;
    }
    const phasesContainer = document.getElementById('insight-phases');
    if (!phasesContainer || !phasesContainer.parentNode) return;
    const panel = document.createElement('div');
    panel.id = 'supervisor-panel';
    panel.innerHTML = `
        <div class="supervisor-header">
            <h3>Research Supervisor</h3>
            <button id="run-supervisor-btn" class="btn-run-supervisor" onclick="runSupervisor()">Run Supervisor</button>
        </div>
        <div id="supervisor-status" class="supervisor-status"></div>
        <ul id="supervisor-decision-log" class="supervisor-decision-log"></ul>
    `;
    phasesContainer.parentNode.insertBefore(panel, phasesContainer.nextSibling);
    refreshSupervisorDecisions();
}

function renderSupervisorDecisionRow(decision) {
    const input = decision.detail && decision.detail.input ? decision.detail.input : {};
    const execution = decision.detail && decision.detail.execution ? decision.detail.execution : {};
    const taskRef = decision.research_work_item_id ? ` (task #${decision.research_work_item_id})` : '';
    const outcomeClass = execution.success ? 'supervisor-outcome-success' : 'supervisor-outcome-error';
    const outcomeText = execution.success
        ? 'succeeded'
        : `failed: ${escapeHtml(execution.error || 'unknown error')}`;
    const reason = input.reason ? escapeHtml(input.reason) : '';
    return `<li class="supervisor-decision-row">
        <span class="supervisor-action">${escapeHtml(decision.decision_type)}${taskRef}</span>
        <span class="${outcomeClass}">${outcomeText}</span>
        ${reason ? `<div class="supervisor-reason">${reason}</div>` : ''}
    </li>`;
}

async function refreshSupervisorDecisions() {
    const log = document.getElementById('supervisor-decision-log');
    if (!log || !currentProject) return;
    try {
        const res = await fetch(`/api/supervisor/decisions?project=${encodeURIComponent(currentProject)}`);
        const json = await res.json();
        const decisions = Array.isArray(json.decisions) ? json.decisions : [];
        log.innerHTML = decisions.length
            ? decisions.map(renderSupervisorDecisionRow).join('')
            : '<li class="supervisor-decision-empty">No decisions yet.</li>';
    } catch (e) {
        console.error('[supervisor] failed to load decisions:', e);
    }
}

async function runSupervisor() {
    const btn = document.getElementById('run-supervisor-btn');
    const statusEl = document.getElementById('supervisor-status');
    if (btn) { btn.disabled = true; btn.innerText = 'Running...'; }
    if (statusEl) statusEl.innerText = '';
    try {
        const res = await fetch('/api/supervisor/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project: currentProject })
        });
        const json = await res.json();
        if (json.success === false) {
            if (statusEl) statusEl.innerHTML = `<span class="supervisor-outcome-error">${escapeHtml(json.error || 'Supervisor run failed')}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = `<span class="supervisor-outcome-error">${escapeHtml(e.message)}</span>`;
    } finally {
        if (btn) { btn.disabled = false; btn.innerText = 'Run Supervisor'; }
        refreshSupervisorDecisions();
        refreshPhaseTaskPanels();
    }
}
```

- [ ] **Step 3: Add CSS**

Append to `static/style.css` (after Phase 2's `.btn-add-task:hover` rule):

```css
#supervisor-panel {
    margin-top: 1.5rem;
    padding: 1rem;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    background: #fafbfc;
}
.supervisor-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.6rem;
}
.supervisor-header h3 {
    margin: 0;
    font-size: 1rem;
    color: var(--oes-ink);
}
.btn-run-supervisor {
    font-size: 0.8rem;
    padding: 6px 14px;
    border: none;
    border-radius: 4px;
    background: var(--oes-valencia, #FF8A00);
    color: white;
    cursor: pointer;
    font-weight: 600;
}
.btn-run-supervisor:hover {
    opacity: 0.9;
}
.btn-run-supervisor:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
.supervisor-status {
    font-size: 0.75rem;
    margin-bottom: 0.4rem;
}
.supervisor-decision-log {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 280px;
    overflow-y: auto;
}
.supervisor-decision-row {
    font-size: 0.78rem;
    padding: 6px 8px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    background: white;
}
.supervisor-action {
    font-weight: 700;
    color: var(--oes-ink);
    margin-right: 6px;
}
.supervisor-outcome-success {
    color: #28a745;
}
.supervisor-outcome-error {
    color: #dc3545;
}
.supervisor-reason {
    margin-top: 3px;
    color: #666;
    font-style: italic;
}
.supervisor-decision-empty {
    font-size: 0.75rem;
    color: #999;
    list-style: none;
}
```

- [ ] **Step 4: Manual verification**

Run: `python app.py`, then in a browser:
1. Open a project with at least one `research_work_items` row (create one via the Phase 2 task panel's "+ Add task" if none exist), go to the Insights tab.
2. Confirm a "Research Supervisor" section appears below the phase cards, with a "Run Supervisor" button and an (initially empty) decision log.
3. Click "Run Supervisor" — confirm the button disables, shows "Running...", then re-enables, and a new row appears in the decision log showing the action taken and its outcome.
4. Click "Run Supervisor" again on a task that's now `READY` or similar — confirm further decisions accumulate in the log, most recent first.
5. Open the History view and step to a previous version (if any exist) — confirm the Supervisor panel does NOT appear under the "(historical)" label (same suppression mechanism as Phase 2's task panel, via `showTasks`).
6. Confirm the existing phase cards, task panels (Phase 2), and all other Insights tab behavior are completely unaffected.

- [ ] **Step 5: Commit**

```bash
git add static/app.js static/style.css
git commit -m "Add Supervisor panel to the Insights tab"
```

---

### Final Steps (after all 11 tasks)

- [ ] Run the full test suite: `python -m pytest tests/ -v` — expect all tests from before this plan (242 as of Phase 2's merge) plus every new test added across Tasks 1-10 to pass, zero regressions.
- [ ] Run `python -m ruff check .` — fix anything newly introduced (pre-existing findings from before this plan are not this plan's concern).
- [ ] Use `superpowers:finishing-a-development-branch` to decide how this work gets merged/pushed.
