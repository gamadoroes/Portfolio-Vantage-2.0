# Phase 2 — Structured Research-Task State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a first-class, trackable "research task" (work item) entity with a status lifecycle, dependencies, and scoring/retry fields, surfaced additively in the Insights UI, without touching the existing versioned findings/history mechanism.

**Architecture:** A new, fully additive migration adds two tables (`research_work_items`, `research_work_item_dependencies`) and two nullable FK columns on existing tables. A new repo module does plain storage; a new service module owns the status state machine, dependency-cycle guard, and a deterministic phase roll-up; a new route blueprint exposes CRUD + transitions; `insights_service.py` gains one new additive JSON key; `app.js`/`style.css` gain an additive panel per phase tab.

**Tech Stack:** Flask, stdlib `sqlite3`, pytest, vanilla JS (no framework), existing OES design tokens in `static/style.css`.

**Spec:** `docs/superpowers/specs/2026-10-04-research-task-state-design.md`

## Global Constraints

- Do not modify `research_tasks`, `findings`, `evidence`, `project_insight_versions`, `agent_decisions`, `db/migrate.py`, or `db/migrate_runner.py` in any task. (Spec §2, §11)
- No automatic orchestration: every status transition, dependency edge, and task creation is triggered by an explicit human action (UI click) or explicit API call — nothing auto-transitions status or auto-creates a `research_runs` row. (Spec §4, §11)
- No DB `CHECK` constraints for status/priority enums — validation lives in the service layer only, matching this schema's existing style. (Spec §3)
- JSON array columns (`entities_json`, `source_requirements_json`, `identified_gaps_json`) follow the existing `competitors_json` convention: a JSON-encoded TEXT column, decoded to a Python list / JS array at the service/route boundary, never exposed as raw JSON text to callers. (Spec §3)
- No hard delete of work items — abandoning work means transitioning to `SKIPPED`, not removing the row. (Spec §8, §11)
- `COMPLETE` and `SKIPPED` are terminal: no code path may transition out of them. (Spec §4)

## Review Focus

- **Viewing a historical insights version must not show live (non-versioned) task state as if it belonged to that version.** `renderInsights(data)` is called both for the *current* insights payload and for a frozen historical entry (`viewPreviousInsightsVersion`/`viewNextInsightsVersion` in `static/app.js`, which label the view "(historical)"). Since `research_work_items` have no version concept, rendering the live task panel under a historical label would misrepresent frozen data as current. Task 7 adds a `showTasks` flag to `renderInsights` and suppresses the task panel on the two historical-view call sites. There is no existing automated test coverage for `static/app.js` in this codebase (zero JS tests exist today), so this is pinned by Task 7's manual verification step 5, not an automated test — consistent with how the rest of the frontend is (not) tested here.
- **A retried task must not retry forever.** `FAILED → READY` is blocked once `retry_count >= max_retries`. Task 3's tests exercise exhausting retries and confirm the transition is rejected with a clear error, not silently allowed or silently dropped.
- **A `human_review_required` task must not reach `COMPLETE` without passing through `WAITING_FOR_HUMAN`.** Both `RUNNING → COMPLETE` and `REVIEWING → COMPLETE` must be blocked when the flag is set — this is the inconsistency caught and fixed during the spec's self-review, and Task 3 pins it with a test for each of the two blocked edges, not just one.
- **A dependency cycle must be rejected, including an indirect (transitive) cycle, not just a direct A-depends-on-A or A-depends-on-B-depends-on-A case.** Task 4's tests build a 3-node chain (A depends on B, B depends on C) and confirm adding "C depends on A" is rejected.
- **Creating a work item for a phase that doesn't yet have any `research_tasks`/findings row must not error.** Work items are fully decoupled from the findings mechanism (spec §2) — Task 6's test confirms a phase with zero findings still returns a valid (all-empty) `task_summary` once a work item exists for it.

---

### Task 1: Migration — `research_work_items` schema

**Files:**
- Create: `db/migrations/0003_research_work_items.sql`
- Test: `tests/test_db_migrations.py` (modify)

**Interfaces:**
- Produces: tables `research_work_items`, `research_work_item_dependencies`; columns `research_runs.research_work_item_id`, `artefacts.research_work_item_id`. All later tasks' repo code depends on this exact schema.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db_migrations.py`:

```python
def test_apply_migrations_is_idempotent(temp_db):
    apply_migrations()
    apply_migrations()
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    assert count == 3


def test_research_work_items_schema(temp_db):
    with get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"research_work_items", "research_work_item_dependencies"}.issubset(tables)

        work_item_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(research_work_items)").fetchall()
        }
        expected_columns = {
            "id", "project_id", "phase_key", "title", "objective", "status", "priority",
            "research_method", "entities_json", "expected_output", "source_requirements_json",
            "completeness_score", "evidence_score", "identified_gaps_json", "retry_count",
            "max_retries", "human_review_required", "created_at", "updated_at",
        }
        assert expected_columns.issubset(work_item_columns)

        dep_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(research_work_item_dependencies)").fetchall()
        }
        assert {"work_item_id", "depends_on_work_item_id", "created_at"}.issubset(dep_columns)

        run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()}
        assert "research_work_item_id" in run_columns

        artefact_columns = {row["name"] for row in conn.execute("PRAGMA table_info(artefacts)").fetchall()}
        assert "research_work_item_id" in artefact_columns


def test_research_work_items_default_status_is_proposed(temp_db):
    from db.repositories import projects_repo
    pid = projects_repo.get_or_create_id("P")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO research_work_items (project_id, phase_key, title, created_at, updated_at) "
            "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')",
            (pid, "4", "Product / La Trobe"),
        )
        row = conn.execute("SELECT status FROM research_work_items WHERE project_id = ?", (pid,)).fetchone()
        assert row["status"] == "PROPOSED"
```

This replaces the existing `test_apply_migrations_is_idempotent` (its expected count changes from 2 to 3) and adds two new tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db_migrations.py -v`
Expected: `test_research_work_items_schema` and `test_research_work_items_default_status_is_proposed` FAIL (table/columns don't exist yet); `test_apply_migrations_is_idempotent` FAILS (count is 2, not 3).

- [ ] **Step 3: Write the migration**

Create `db/migrations/0003_research_work_items.sql`:

```sql
-- Structured research-task state (Phase 2). Fully additive: two new tables,
-- two new nullable columns on existing tables. Does NOT touch research_tasks,
-- findings, evidence, or project_insight_versions -- those keep anchoring the
-- versioned insights-history mechanism exactly as today. See
-- docs/superpowers/specs/2026-10-04-research-task-state-design.md section 2
-- for why this is a new table rather than a rebuild of research_tasks.

CREATE TABLE research_work_items (
    id                       INTEGER PRIMARY KEY,
    project_id               INTEGER NOT NULL REFERENCES projects(id),
    phase_key                TEXT,
    title                    TEXT NOT NULL,
    objective                TEXT,
    status                   TEXT NOT NULL DEFAULT 'PROPOSED',
    priority                 TEXT,
    research_method          TEXT,
    entities_json            TEXT,
    expected_output          TEXT,
    source_requirements_json TEXT,
    completeness_score       REAL,
    evidence_score           REAL,
    identified_gaps_json     TEXT,
    retry_count              INTEGER NOT NULL DEFAULT 0,
    max_retries              INTEGER NOT NULL DEFAULT 3,
    human_review_required    INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE INDEX idx_research_work_items_project_phase
    ON research_work_items(project_id, phase_key);

CREATE TABLE research_work_item_dependencies (
    work_item_id            INTEGER NOT NULL REFERENCES research_work_items(id),
    depends_on_work_item_id INTEGER NOT NULL REFERENCES research_work_items(id),
    created_at              TEXT NOT NULL,
    PRIMARY KEY (work_item_id, depends_on_work_item_id)
);

ALTER TABLE research_runs ADD COLUMN research_work_item_id INTEGER REFERENCES research_work_items(id);
ALTER TABLE artefacts ADD COLUMN research_work_item_id INTEGER REFERENCES research_work_items(id);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db_migrations.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0003_research_work_items.sql tests/test_db_migrations.py
git commit -m "Add research_work_items schema (Phase 2 data model)"
```

---

### Task 2: Repository — `research_work_items_repo.py`

**Files:**
- Create: `db/repositories/research_work_items_repo.py`
- Test: `tests/test_repositories_research_work_items.py`

**Interfaces:**
- Consumes: `db.connection.get_connection` (same pattern as every other repo module).
- Produces: `create(project_id, phase_key, title, objective=None, priority=None, research_method=None, entities_json=None, expected_output=None, source_requirements_json=None, human_review_required=0, max_retries=3) -> int`; `get(id) -> Row|None`; `list_for_project(project_id) -> list[Row]`; `list_for_phase(project_id, phase_key) -> list[Row]`; `update_fields(id, **fields) -> bool`; `add_dependency(work_item_id, depends_on_work_item_id)`; `remove_dependency(work_item_id, depends_on_work_item_id)`; `list_dependencies(work_item_id) -> list[Row]` (each row has `depends_on_work_item_id`); `list_dependents(work_item_id) -> list[Row]` (each row has `work_item_id`). These exact names/signatures are consumed by Tasks 3-6.

- [ ] **Step 1: Write the failing test**

Create `tests/test_repositories_research_work_items.py`:

```python
from db.repositories import projects_repo, research_work_items_repo


def test_create_and_get_round_trip(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(
        pid, "4", "Product / La Trobe",
        objective="Scope La Trobe's product offering",
        priority="high",
        research_method="deep_research",
        entities_json='["La Trobe"]',
        expected_output="A product comparison table",
        source_requirements_json='["public website"]',
        human_review_required=1,
        max_retries=5,
    )
    row = research_work_items_repo.get(task_id)
    assert row["project_id"] == pid
    assert row["phase_key"] == "4"
    assert row["title"] == "Product / La Trobe"
    assert row["objective"] == "Scope La Trobe's product offering"
    assert row["status"] == "PROPOSED"
    assert row["priority"] == "high"
    assert row["entities_json"] == '["La Trobe"]'
    assert row["human_review_required"] == 1
    assert row["max_retries"] == 5
    assert row["retry_count"] == 0


def test_get_missing_returns_none(temp_db):
    assert research_work_items_repo.get(9999) is None


def test_list_for_project_and_phase(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_work_items_repo.create(pid, "4", "Product / La Trobe")
    research_work_items_repo.create(pid, "4", "Product / Torrens")
    research_work_items_repo.create(pid, "1", "Landscape overview")

    assert len(research_work_items_repo.list_for_project(pid)) == 3
    phase_4_items = research_work_items_repo.list_for_phase(pid, "4")
    assert len(phase_4_items) == 2
    assert {r["title"] for r in phase_4_items} == {"Product / La Trobe", "Product / Torrens"}


def test_multiple_tasks_allowed_under_one_phase(temp_db):
    # The core Phase-2 relaxation: no UNIQUE(project_id, phase_key) here,
    # unlike the legacy research_tasks table.
    pid = projects_repo.get_or_create_id("P")
    id1 = research_work_items_repo.create(pid, "4", "Product / La Trobe")
    id2 = research_work_items_repo.create(pid, "4", "Product / Torrens")
    assert id1 != id2


def test_update_fields(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Product / La Trobe")
    assert research_work_items_repo.update_fields(task_id, status="READY", completeness_score=0.5) is True
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "READY"
    assert row["completeness_score"] == 0.5


def test_update_fields_unknown_id_returns_false(temp_db):
    assert research_work_items_repo.update_fields(9999, status="READY") is False


def test_dependency_round_trip(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")

    research_work_items_repo.add_dependency(a, b)
    deps = research_work_items_repo.list_dependencies(a)
    assert [r["depends_on_work_item_id"] for r in deps] == [b]

    dependents = research_work_items_repo.list_dependents(b)
    assert [r["work_item_id"] for r in dependents] == [a]

    research_work_items_repo.remove_dependency(a, b)
    assert research_work_items_repo.list_dependencies(a) == []


def test_add_dependency_is_idempotent(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")
    research_work_items_repo.add_dependency(a, b)
    research_work_items_repo.add_dependency(a, b)  # must not raise (duplicate PK)
    assert len(research_work_items_repo.list_dependencies(a)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_repositories_research_work_items.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db.repositories.research_work_items_repo'`.

- [ ] **Step 3: Write the implementation**

Create `db/repositories/research_work_items_repo.py`:

```python
from datetime import datetime

from ..connection import get_connection


def create(
    project_id, phase_key, title, objective=None, priority=None, research_method=None,
    entities_json=None, expected_output=None, source_requirements_json=None,
    human_review_required=0, max_retries=3,
):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO research_work_items "
            "(project_id, phase_key, title, objective, priority, research_method, "
            " entities_json, expected_output, source_requirements_json, "
            " human_review_required, max_retries, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id, phase_key, title, objective, priority, research_method,
                entities_json, expected_output, source_requirements_json,
                human_review_required, max_retries, now, now,
            ),
        )
        return cur.lastrowid


def get(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM research_work_items WHERE id = ?", (id,)).fetchone()


def list_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_work_items WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()


def list_for_phase(project_id, phase_key):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_work_items WHERE project_id = ? AND phase_key = ? ORDER BY created_at",
            (project_id, phase_key),
        ).fetchall()


def update_fields(id, **fields):
    if not fields:
        return get(id) is not None
    fields["updated_at"] = datetime.now().isoformat()
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [id]
    with get_connection() as conn:
        cur = conn.execute(f"UPDATE research_work_items SET {columns} WHERE id = ?", values)
        return cur.rowcount > 0


def add_dependency(work_item_id, depends_on_work_item_id):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO research_work_item_dependencies "
            "(work_item_id, depends_on_work_item_id, created_at) VALUES (?, ?, ?)",
            (work_item_id, depends_on_work_item_id, now),
        )


def remove_dependency(work_item_id, depends_on_work_item_id):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM research_work_item_dependencies "
            "WHERE work_item_id = ? AND depends_on_work_item_id = ?",
            (work_item_id, depends_on_work_item_id),
        )


def list_dependencies(work_item_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT depends_on_work_item_id FROM research_work_item_dependencies WHERE work_item_id = ?",
            (work_item_id,),
        ).fetchall()


def list_dependents(work_item_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT work_item_id FROM research_work_item_dependencies WHERE depends_on_work_item_id = ?",
            (work_item_id,),
        ).fetchall()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_repositories_research_work_items.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add db/repositories/research_work_items_repo.py tests/test_repositories_research_work_items.py
git commit -m "Add research_work_items_repo: CRUD and dependency storage"
```

---

### Task 3: Service — status transition state machine

**Files:**
- Create: `services/research_task_service.py`
- Test: `tests/test_research_task_service.py`

**Interfaces:**
- Consumes: `db.repositories.research_work_items_repo` (Task 2's exact functions).
- Produces: `ALLOWED_TRANSITIONS: dict[str, set[str]]`; `create_task(project_id, phase_key, title, objective=None, priority=None, research_method=None, entities=None, expected_output=None, source_requirements=None, human_review_required=False, max_retries=3) -> int`; `transition_task(work_item_id, new_status) -> None` (raises `ValueError` on any illegal transition). Task 4 adds `add_dependency`/`compute_phase_rollup` to this same module/file. Task 5's routes call `create_task` and `transition_task` directly.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_task_service.py`:

```python
import pytest

from db.repositories import projects_repo, research_work_items_repo
from services import research_task_service


def _make_task(pid, status="PROPOSED", **kwargs):
    task_id = research_work_items_repo.create(pid, "4", "Task", **kwargs)
    if status != "PROPOSED":
        research_work_items_repo.update_fields(task_id, status=status)
    return task_id


def test_create_task_defaults_to_proposed(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_task_service.create_task(pid, "4", "Product / La Trobe")
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "PROPOSED"


def test_create_task_serializes_list_fields_to_json(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_task_service.create_task(
        pid, "4", "Product / La Trobe", entities=["La Trobe"], source_requirements=["public website"]
    )
    row = research_work_items_repo.get(task_id)
    assert row["entities_json"] == '["La Trobe"]'
    assert row["source_requirements_json"] == '["public website"]'


def test_create_task_rejects_invalid_priority(temp_db):
    pid = projects_repo.get_or_create_id("P")
    with pytest.raises(ValueError):
        research_task_service.create_task(pid, "4", "Task", priority="urgent")


@pytest.mark.parametrize("from_status,to_status", [
    ("PROPOSED", "READY"),
    ("PROPOSED", "SKIPPED"),
    ("READY", "RUNNING"),
    ("READY", "SKIPPED"),
    ("RUNNING", "REVIEWING"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "COMPLETE"),
    ("REVIEWING", "COMPLETE"),
    ("REVIEWING", "FOLLOW_UP_REQUIRED"),
    ("WAITING_FOR_HUMAN", "REVIEWING"),
    ("WAITING_FOR_HUMAN", "COMPLETE"),
    ("WAITING_FOR_HUMAN", "FAILED"),
    ("FOLLOW_UP_REQUIRED", "READY"),
    ("FAILED", "READY"),
    ("FAILED", "SKIPPED"),
])
def test_legal_transitions_succeed(temp_db, from_status, to_status):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status=from_status)
    research_task_service.transition_task(task_id, to_status)
    assert research_work_items_repo.get(task_id)["status"] == to_status


@pytest.mark.parametrize("from_status,to_status", [
    ("PROPOSED", "RUNNING"),
    ("PROPOSED", "COMPLETE"),
    ("READY", "REVIEWING"),
    ("RUNNING", "READY"),
    ("COMPLETE", "READY"),
    ("COMPLETE", "RUNNING"),
    ("SKIPPED", "READY"),
    ("SKIPPED", "RUNNING"),
])
def test_illegal_transitions_rejected(temp_db, from_status, to_status):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status=from_status)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, to_status)
    assert research_work_items_repo.get(task_id)["status"] == from_status


def test_running_to_complete_blocked_when_human_review_required(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="RUNNING", human_review_required=1)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "COMPLETE")


def test_reviewing_to_complete_blocked_when_human_review_required(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="REVIEWING", human_review_required=1)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "COMPLETE")


def test_reviewing_to_waiting_for_human_requires_flag(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="REVIEWING", human_review_required=0)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "WAITING_FOR_HUMAN")


def test_running_to_failed_increments_retry_count(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="RUNNING")
    research_task_service.transition_task(task_id, "FAILED")
    assert research_work_items_repo.get(task_id)["retry_count"] == 1


def test_failed_to_ready_blocked_once_retries_exhausted(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="FAILED", max_retries=1)
    research_work_items_repo.update_fields(task_id, retry_count=1)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "READY")


def test_failed_to_ready_allowed_when_retries_remain(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="FAILED", max_retries=3)
    research_work_items_repo.update_fields(task_id, retry_count=1)
    research_task_service.transition_task(task_id, "READY")
    assert research_work_items_repo.get(task_id)["status"] == "READY"


def test_proposed_to_ready_blocked_by_incomplete_dependency(temp_db):
    pid = projects_repo.get_or_create_id("P")
    dep_id = _make_task(pid, status="RUNNING")
    task_id = research_work_items_repo.create(pid, "4", "Dependent task")
    research_work_items_repo.add_dependency(task_id, dep_id)
    with pytest.raises(ValueError):
        research_task_service.transition_task(task_id, "READY")


def test_proposed_to_ready_allowed_once_dependency_complete(temp_db):
    pid = projects_repo.get_or_create_id("P")
    dep_id = _make_task(pid, status="COMPLETE")
    task_id = research_work_items_repo.create(pid, "4", "Dependent task")
    research_work_items_repo.add_dependency(task_id, dep_id)
    research_task_service.transition_task(task_id, "READY")
    assert research_work_items_repo.get(task_id)["status"] == "READY"


def test_transition_unknown_task_raises(temp_db):
    with pytest.raises(ValueError):
        research_task_service.transition_task(9999, "READY")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_research_task_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.research_task_service'`.

- [ ] **Step 3: Write the implementation**

Create `services/research_task_service.py`:

```python
import json

from db.repositories import research_work_items_repo

ALLOWED_TRANSITIONS = {
    "PROPOSED": {"READY", "SKIPPED"},
    "READY": {"RUNNING", "SKIPPED"},
    "RUNNING": {"REVIEWING", "FAILED", "COMPLETE"},
    "REVIEWING": {"COMPLETE", "FOLLOW_UP_REQUIRED", "WAITING_FOR_HUMAN"},
    "WAITING_FOR_HUMAN": {"REVIEWING", "COMPLETE", "FAILED"},
    "FOLLOW_UP_REQUIRED": {"READY"},
    "FAILED": {"READY", "SKIPPED"},
    "COMPLETE": set(),
    "SKIPPED": set(),
}

VALID_PRIORITIES = {"low", "medium", "high"}


def create_task(
    project_id, phase_key, title, objective=None, priority=None, research_method=None,
    entities=None, expected_output=None, source_requirements=None,
    human_review_required=False, max_retries=3,
):
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority!r}. Must be one of {sorted(VALID_PRIORITIES)}")
    return research_work_items_repo.create(
        project_id, phase_key, title,
        objective=objective,
        priority=priority,
        research_method=research_method,
        entities_json=json.dumps(entities) if entities else None,
        expected_output=expected_output,
        source_requirements_json=json.dumps(source_requirements) if source_requirements else None,
        human_review_required=1 if human_review_required else 0,
        max_retries=max_retries,
    )


def transition_task(work_item_id, new_status):
    item = research_work_items_repo.get(work_item_id)
    if item is None:
        raise ValueError(f"No such research task: {work_item_id}")

    current = item["status"]
    if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"Cannot transition from {current} to {new_status}")

    if new_status == "READY":
        _assert_dependencies_satisfied(work_item_id)
        if current == "FAILED" and item["retry_count"] >= item["max_retries"]:
            raise ValueError("Retry limit reached -- transition to SKIPPED instead")

    if new_status == "COMPLETE" and current in ("RUNNING", "REVIEWING") and item["human_review_required"]:
        raise ValueError("This task requires human review before it can be marked COMPLETE")

    if new_status == "WAITING_FOR_HUMAN" and not item["human_review_required"]:
        raise ValueError("This task does not require human review")

    fields = {"status": new_status}
    if new_status == "FAILED":
        fields["retry_count"] = item["retry_count"] + 1
    research_work_items_repo.update_fields(work_item_id, **fields)


def _assert_dependencies_satisfied(work_item_id):
    dep_ids = [r["depends_on_work_item_id"] for r in research_work_items_repo.list_dependencies(work_item_id)]
    for dep_id in dep_ids:
        dep = research_work_items_repo.get(dep_id)
        if dep is None or dep["status"] not in ("COMPLETE", "SKIPPED"):
            raise ValueError(f"Cannot move to READY: dependency {dep_id} is not complete")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_research_task_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/research_task_service.py tests/test_research_task_service.py
git commit -m "Add research_task_service: status transition state machine"
```

---

### Task 4: Service — dependency cycle guard and phase roll-up

**Files:**
- Modify: `services/research_task_service.py` (append; do not touch the code from Task 3)
- Test: `tests/test_research_task_service.py` (append)

**Interfaces:**
- Consumes: `research_work_items_repo.list_dependencies`, `.get`, `.add_dependency`, `.list_for_phase` (Task 2).
- Produces: `add_dependency(work_item_id, depends_on_work_item_id) -> None` (raises `ValueError` on self-reference, cross-project, or cycle); `compute_phase_rollup(project_id, phase_key) -> dict` with keys `status_counts` (dict[str,int]), `weakest_completeness` (float|None), `merged_gaps` (list[str]). Task 5's routes call `add_dependency`; Task 6 calls `compute_phase_rollup`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_research_task_service.py`:

```python
def test_add_dependency_rejects_self_reference(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "A")
    with pytest.raises(ValueError):
        research_task_service.add_dependency(task_id, task_id)


def test_add_dependency_rejects_cross_project(temp_db):
    pid1 = projects_repo.get_or_create_id("P1")
    pid2 = projects_repo.get_or_create_id("P2")
    a = research_work_items_repo.create(pid1, "4", "A")
    b = research_work_items_repo.create(pid2, "4", "B")
    with pytest.raises(ValueError):
        research_task_service.add_dependency(a, b)


def test_add_dependency_rejects_direct_cycle(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")
    research_task_service.add_dependency(a, b)  # A depends on B
    with pytest.raises(ValueError):
        research_task_service.add_dependency(b, a)  # B depends on A -- direct cycle


def test_add_dependency_rejects_transitive_cycle(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")
    c = research_work_items_repo.create(pid, "4", "C")
    research_task_service.add_dependency(a, b)  # A depends on B
    research_task_service.add_dependency(b, c)  # B depends on C
    with pytest.raises(ValueError):
        research_task_service.add_dependency(c, a)  # C depends on A -- closes the loop


def test_add_dependency_allows_diamond_shape(temp_db):
    # A depends on B and C; both B and C depend on D. Not a cycle.
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")
    c = research_work_items_repo.create(pid, "4", "C")
    d = research_work_items_repo.create(pid, "4", "D")
    research_task_service.add_dependency(a, b)
    research_task_service.add_dependency(a, c)
    research_task_service.add_dependency(b, d)
    research_task_service.add_dependency(c, d)  # must not raise
    assert len(research_work_items_repo.list_dependencies(a)) == 2


def test_compute_phase_rollup_empty_phase(temp_db):
    pid = projects_repo.get_or_create_id("P")
    rollup = research_task_service.compute_phase_rollup(pid, "4")
    assert rollup == {"status_counts": {}, "weakest_completeness": None, "merged_gaps": []}


def test_compute_phase_rollup_counts_and_scores(temp_db):
    pid = projects_repo.get_or_create_id("P")
    t1 = research_work_items_repo.create(pid, "4", "A")
    research_work_items_repo.update_fields(t1, status="COMPLETE", completeness_score=0.9,
                                            identified_gaps_json='["Missing pricing"]')
    t2 = research_work_items_repo.create(pid, "4", "B")
    research_work_items_repo.update_fields(t2, status="COMPLETE", completeness_score=0.4,
                                            identified_gaps_json='["Missing pricing", "No faculty data"]')
    t3 = research_work_items_repo.create(pid, "4", "C")
    research_work_items_repo.update_fields(t3, status="RUNNING")

    rollup = research_task_service.compute_phase_rollup(pid, "4")
    assert rollup["status_counts"] == {"COMPLETE": 2, "RUNNING": 1}
    assert rollup["weakest_completeness"] == 0.4
    assert rollup["merged_gaps"] == ["Missing pricing", "No faculty data"]


def test_compute_phase_rollup_ignores_other_phases(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_work_items_repo.create(pid, "1", "Other phase task")
    rollup = research_task_service.compute_phase_rollup(pid, "4")
    assert rollup["status_counts"] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_research_task_service.py -v`
Expected: the new tests FAIL with `AttributeError: module 'services.research_task_service' has no attribute 'add_dependency'` (and `compute_phase_rollup`); Task 3's tests still PASS.

- [ ] **Step 3: Write the implementation**

Append to `services/research_task_service.py` (after `_assert_dependencies_satisfied`):

```python
def add_dependency(work_item_id, depends_on_work_item_id):
    if work_item_id == depends_on_work_item_id:
        raise ValueError("A task cannot depend on itself")
    item = research_work_items_repo.get(work_item_id)
    dep = research_work_items_repo.get(depends_on_work_item_id)
    if item is None or dep is None:
        raise ValueError("Task not found")
    if item["project_id"] != dep["project_id"]:
        raise ValueError("Dependencies must be within the same project")
    if _creates_cycle(depends_on_work_item_id, work_item_id):
        raise ValueError("This dependency would create a cycle")
    research_work_items_repo.add_dependency(work_item_id, depends_on_work_item_id)


def _creates_cycle(start_id, target_id):
    """True if target_id is reachable from start_id by following existing
    depends_on edges -- i.e. adding (target_id depends_on start_id) would
    close a loop."""
    visited = set()
    stack = [start_id]
    while stack:
        current = stack.pop()
        if current == target_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        for row in research_work_items_repo.list_dependencies(current):
            stack.append(row["depends_on_work_item_id"])
    return False


def compute_phase_rollup(project_id, phase_key):
    items = research_work_items_repo.list_for_phase(project_id, phase_key)

    status_counts = {}
    for item in items:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1

    completed_scores = [
        item["completeness_score"] for item in items
        if item["status"] == "COMPLETE" and item["completeness_score"] is not None
    ]
    weakest_completeness = min(completed_scores) if completed_scores else None

    merged_gaps = []
    seen_gaps = set()
    for item in items:
        gaps = json.loads(item["identified_gaps_json"]) if item["identified_gaps_json"] else []
        for gap in gaps:
            if gap not in seen_gaps:
                seen_gaps.add(gap)
                merged_gaps.append(gap)

    return {
        "status_counts": status_counts,
        "weakest_completeness": weakest_completeness,
        "merged_gaps": merged_gaps,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_research_task_service.py -v`
Expected: all PASS (Task 3's and Task 4's tests together).

- [ ] **Step 5: Commit**

```bash
git add services/research_task_service.py tests/test_research_task_service.py
git commit -m "Add dependency-cycle guard and phase roll-up to research_task_service"
```

---

### Task 5: Routes — `routes/research_tasks.py`

**Files:**
- Create: `routes/research_tasks.py`
- Modify: `app.py:5-30` (add import and blueprint registration)
- Test: `tests/test_routes_research_tasks.py`

**Interfaces:**
- Consumes: `services.research_task_service.create_task`/`.transition_task`/`.add_dependency` (Tasks 3-4), `db.repositories.research_work_items_repo.list_for_project`/`.list_for_phase`/`.remove_dependency` (Task 2), `services.project_service.normalize_project_name`/`.project_exists`, `db.repositories.projects_repo.get_or_create_id` (existing).
- Produces: blueprint `research_tasks_bp` registered at `/api/research-tasks`. No other task depends on this blueprint's internals, only on it existing and responding per the routes below (Task 7's UI calls these exact paths/bodies).

Routes:
- `POST /api/research-tasks` — body `{project, phase_key, title, objective?, priority?, research_method?, entities?, expected_output?, source_requirements?, human_review_required?, max_retries?}` → `{success: true, id}` or 400.
- `GET /api/research-tasks?project=&phase_key=` → `{tasks: [...]}` (phase_key optional; omitted means all tasks in the project).
- `POST /api/research-tasks/<int:task_id>/transition` — body `{status}` → `{success: true}` or 400 with `{success: false, error}`.
- `POST /api/research-tasks/<int:task_id>/dependencies` — body `{depends_on_task_id}` → `{success: true}` or 400.
- `DELETE /api/research-tasks/<int:task_id>/dependencies/<int:depends_on_id>` → `{success: true}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_routes_research_tasks.py`:

```python
import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations


@pytest.fixture
def client(tmp_path):
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


def test_transition_task_route(client):
    _create_project(client)
    create_resp = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"})
    task_id = create_resp.get_json()["id"]

    resp = client.post(f"/api/research-tasks/{task_id}/transition", json={"status": "READY"})
    assert resp.get_json()["success"] is True

    list_resp = client.get("/api/research-tasks", query_string={"project": "P"})
    assert list_resp.get_json()["tasks"][0]["status"] == "READY"


def test_transition_task_illegal_returns_400_with_error(client):
    _create_project(client)
    create_resp = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"})
    task_id = create_resp.get_json()["id"]

    resp = client.post(f"/api/research-tasks/{task_id}/transition", json={"status": "COMPLETE"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_add_and_remove_dependency_routes(client):
    _create_project(client)
    a_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"}).get_json()["id"]
    b_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "B"}).get_json()["id"]

    resp = client.post(f"/api/research-tasks/{a_id}/dependencies", json={"depends_on_task_id": b_id})
    assert resp.get_json()["success"] is True

    # A depends on B, which is still PROPOSED -- A cannot go READY yet.
    blocked = client.post(f"/api/research-tasks/{a_id}/transition", json={"status": "READY"})
    assert blocked.status_code == 400

    del_resp = client.delete(f"/api/research-tasks/{a_id}/dependencies/{b_id}")
    assert del_resp.get_json()["success"] is True

    # Dependency removed -- A can go READY now.
    allowed = client.post(f"/api/research-tasks/{a_id}/transition", json={"status": "READY"})
    assert allowed.get_json()["success"] is True


def test_add_dependency_cycle_returns_400(client):
    _create_project(client)
    a_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "A"}).get_json()["id"]
    b_id = client.post("/api/research-tasks", json={"project": "P", "phase_key": "4", "title": "B"}).get_json()["id"]
    client.post(f"/api/research-tasks/{a_id}/dependencies", json={"depends_on_task_id": b_id})

    resp = client.post(f"/api/research-tasks/{b_id}/dependencies", json={"depends_on_task_id": a_id})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_routes_research_tasks.py -v`
Expected: FAIL with 404s (blueprint not registered / doesn't exist).

- [ ] **Step 3: Write the implementation**

Create `routes/research_tasks.py`:

```python
import json

from flask import Blueprint, jsonify, request, session

from db.repositories import projects_repo, research_work_items_repo
from services import research_task_service
from services.project_service import normalize_project_name, project_exists

research_tasks_bp = Blueprint("research_tasks", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


def _serialize_task(row):
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "phase_key": row["phase_key"],
        "title": row["title"],
        "objective": row["objective"],
        "status": row["status"],
        "priority": row["priority"],
        "research_method": row["research_method"],
        "entities": json.loads(row["entities_json"]) if row["entities_json"] else [],
        "expected_output": row["expected_output"],
        "source_requirements": json.loads(row["source_requirements_json"]) if row["source_requirements_json"] else [],
        "completeness_score": row["completeness_score"],
        "evidence_score": row["evidence_score"],
        "identified_gaps": json.loads(row["identified_gaps_json"]) if row["identified_gaps_json"] else [],
        "retry_count": row["retry_count"],
        "max_retries": row["max_retries"],
        "human_review_required": bool(row["human_review_required"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@research_tasks_bp.route("/api/research-tasks", methods=["POST"])
def create_task_route():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "error": "Title is required"}), 400

    project_id = projects_repo.get_or_create_id(project)
    try:
        task_id = research_task_service.create_task(
            project_id,
            data.get("phase_key"),
            title,
            objective=data.get("objective"),
            priority=data.get("priority"),
            research_method=data.get("research_method"),
            entities=data.get("entities"),
            expected_output=data.get("expected_output"),
            source_requirements=data.get("source_requirements"),
            human_review_required=bool(data.get("human_review_required", False)),
            max_retries=data.get("max_retries", 3),
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "id": task_id})


@research_tasks_bp.route("/api/research-tasks", methods=["GET"])
def list_tasks_route():
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400
    project_id = projects_repo.get_or_create_id(project)
    phase_key = request.args.get("phase_key")
    if phase_key:
        rows = research_work_items_repo.list_for_phase(project_id, phase_key)
    else:
        rows = research_work_items_repo.list_for_project(project_id)
    return jsonify({"tasks": [_serialize_task(r) for r in rows]})


@research_tasks_bp.route("/api/research-tasks/<int:task_id>/transition", methods=["POST"])
def transition_task_route(task_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"success": False, "error": "Missing status"}), 400
    try:
        research_task_service.transition_task(task_id, new_status)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True})


@research_tasks_bp.route("/api/research-tasks/<int:task_id>/dependencies", methods=["POST"])
def add_dependency_route(task_id):
    data = request.get_json(silent=True) or {}
    depends_on_id = data.get("depends_on_task_id")
    if not depends_on_id:
        return jsonify({"success": False, "error": "Missing depends_on_task_id"}), 400
    try:
        research_task_service.add_dependency(task_id, depends_on_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True})


@research_tasks_bp.route(
    "/api/research-tasks/<int:task_id>/dependencies/<int:depends_on_id>", methods=["DELETE"]
)
def remove_dependency_route(task_id, depends_on_id):
    research_work_items_repo.remove_dependency(task_id, depends_on_id)
    return jsonify({"success": True})
```

Modify `app.py` — add the import at the top (alongside the other route imports) and register the blueprint:

```python
from routes.research_tasks import research_tasks_bp
```

and, in `create_app()`, alongside the other `app.register_blueprint(...)` calls:

```python
    app.register_blueprint(research_tasks_bp)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_routes_research_tasks.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/research_tasks.py app.py tests/test_routes_research_tasks.py
git commit -m "Add routes/research_tasks.py: CRUD, transition, and dependency endpoints"
```

---

### Task 6: Integrate phase roll-up into `insights_service.py`

**Files:**
- Modify: `services/insights_service.py`
- Test: `tests/test_insights_service.py` (append)

**Interfaces:**
- Consumes: `services.research_task_service.compute_phase_rollup(project_id, phase_key)` (Task 4).
- Produces: `_build_insights_payload`'s returned `phases[phase_key]` dict gains one new key, `task_summary`, on every phase (whether or not that phase has any findings). This is the only change in this task — no other key changes.

- [ ] **Step 1: Write the failing test**

In `tests/test_insights_service.py`, change the existing top-of-file import line

```python
from db.repositories import projects_repo, sources_repo
```

to

```python
from db.repositories import projects_repo, research_work_items_repo, sources_repo
```

Then append these two test functions at the end of the file:

```python
def test_task_summary_present_even_with_no_findings(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_work_items_repo.create(pid, "4", "Product / La Trobe")

    loaded = insights_service.load_current_insights("P")
    assert loaded["phases"]["4"]["task_summary"]["status_counts"] == {"PROPOSED": 1}
    # Untouched phase: task_summary present and empty, existing keys unaffected
    assert loaded["phases"]["1"]["task_summary"]["status_counts"] == {}
    assert loaded["phases"]["1"]["summary"] == "MISSING"


def test_task_summary_does_not_disturb_existing_phase_keys(temp_db):
    insights_service.save_insights("P", _sample_data(summary="Landscape summary"))
    loaded = insights_service.load_current_insights("P")
    phase = loaded["phases"]["1"]
    assert phase["summary"] == "Landscape summary"
    assert "task_summary" in phase
    assert set(phase.keys()) >= {
        "title", "summary", "confidence", "evidence_sources", "gaps",
        "suggested_topics", "linked_files", "linked_file_ids", "task_summary",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_insights_service.py -v -k task_summary`
Expected: FAIL with `KeyError: 'task_summary'`.

- [ ] **Step 3: Write the implementation**

In `services/insights_service.py`, add the import at the top:

```python
from . import research_task_service
```

Then modify `_build_insights_payload` — the loop that builds `phases`:

```python
    phases = {}
    for phase_key in PHASE_DEFINITIONS:
        task_id = tasks_by_phase.get(phase_key)
        if task_id is None:
            phases[phase_key] = _empty_phase_payload(phase_key)
        else:
            phases[phase_key] = _build_phase_data(version_row["id"], phase_key, task_id)
        phases[phase_key]["task_summary"] = research_task_service.compute_phase_rollup(project_id, phase_key)
```

(The added line is the only change — everything else in `_build_insights_payload` stays as-is.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_insights_service.py -v`
Expected: all PASS (including every pre-existing test in this file — this confirms the I1 fix's mechanism is untouched).

- [ ] **Step 5: Commit**

```bash
git add services/insights_service.py tests/test_insights_service.py
git commit -m "Add additive task_summary roll-up to insights phase payload"
```

---

### Task 7: UI — additive task panel per phase tab

**Files:**
- Modify: `static/app.js` (around line 2014's `renderInsights` function, and the two historical-view call sites at lines 1256 and 1270)
- Modify: `static/style.css` (append after the `.linked-file-chip` rules, around line 1060)
- Test: manual verification steps below (this is a frontend-only task; existing test suite has no browser-level tests for `app.js`, matching the codebase's current convention — `static/app.js` has zero existing automated tests)

**Interfaces:**
- Consumes: `GET /api/research-tasks?project=`, `POST /api/research-tasks`, `POST /api/research-tasks/<id>/transition` (Task 5); `phase.task_summary` (Task 6, already present in every `data.phases[key]` the existing `renderInsights(data)` receives).
- Produces: a `showTasks` parameter on `renderInsights`, defaulting to `true`.

- [ ] **Step 1: Add the `showTasks` parameter and suppress it on historical views**

In `static/app.js`, change the `renderInsights` declaration (currently at line 2014):

```javascript
function renderInsights(data, { showTasks = true } = {}) {
```

Find the two historical-version call sites and change them to pass `{ showTasks: false }`:

```javascript
function viewPreviousInsightsVersion() {
    if (currentInsightsVersion <= 1) return;
    currentInsightsVersion--;
    const entry = insightsHistory[currentInsightsVersion - 1];
    currentInsightsData = entry.data;
    renderInsights(entry.data, { showTasks: false });
    ...
```

```javascript
function viewNextInsightsVersion() {
    if (currentInsightsVersion >= insightsHistory.length) return;
    currentInsightsVersion++;
    const entry = insightsHistory[currentInsightsVersion - 1];
    currentInsightsData = entry.data;
    renderInsights(entry.data, { showTasks: false });
    ...
```

All other existing call sites (`renderInsights(currentInsightsData)`) are unchanged — they keep the `showTasks = true` default.

- [ ] **Step 2: Add the roll-up line and panel placeholder to the phase card markup**

In `renderInsights`, inside the `Object.entries(data.phases).forEach(([key, phase]) => { ... })` loop, the existing card markup is:

```javascript
            phasesContainer.innerHTML += `
                <div class="insight-phase-card" id="phase-card-${key}" ${clickToEdit} ${cursorStyle}>
                    <div class="phase-header">
                        <span class="phase-num">Phase ${key}</span>
                        <h3>${renderMarkdownInline(phase.title || '')}</h3>
                        ${confidenceBadge}
                        ${generateBtn}
                    </div>
                    <div class="phase-content markdown-body">
                        ${summaryHtml}
                        ${evidenceSources}
                        ${gapsHtml}
                        ${topicsHtml}
                    </div>
                    ${renderPhaseLinkedFiles(key, phase)}
                    ${hasContent ? '<div class="phase-edit-hint">Click to edit</div>' : ''}
                </div>`;
```

Change it to (adds two lines; everything else identical):

```javascript
            phasesContainer.innerHTML += `
                <div class="insight-phase-card" id="phase-card-${key}" ${clickToEdit} ${cursorStyle}>
                    <div class="phase-header">
                        <span class="phase-num">Phase ${key}</span>
                        <h3>${renderMarkdownInline(phase.title || '')}</h3>
                        ${confidenceBadge}
                        ${generateBtn}
                    </div>
                    <div class="phase-content markdown-body">
                        ${summaryHtml}
                        ${evidenceSources}
                        ${gapsHtml}
                        ${topicsHtml}
                    </div>
                    ${renderPhaseLinkedFiles(key, phase)}
                    ${showTasks ? renderTaskSummaryLine(phase.task_summary) : ''}
                    ${showTasks ? `<div class="phase-task-panel" id="phase-task-panel-${key}" onclick="event.stopPropagation()"></div>` : ''}
                    ${hasContent ? '<div class="phase-edit-hint">Click to edit</div>' : ''}
                </div>`;
```

At the end of `renderInsights`, the existing tail is:

```javascript
    renderExcludedCompetitors();
    renderPhaseNavBar(data);
    setupPhaseNavObserver();
}
```

Change it to:

```javascript
    renderExcludedCompetitors();
    renderPhaseNavBar(data);
    setupPhaseNavObserver();
    if (showTasks) refreshPhaseTaskPanels();
}
```

- [ ] **Step 3: Add the new rendering and data-fetching functions**

Add these new functions immediately after `renderPhaseLinkedFiles` (after its closing `}` at line 2516):

```javascript
const TASK_TRANSITIONS = {
    PROPOSED: ['READY', 'SKIPPED'],
    READY: ['RUNNING', 'SKIPPED'],
    RUNNING: ['REVIEWING', 'FAILED', 'COMPLETE'],
    REVIEWING: ['COMPLETE', 'FOLLOW_UP_REQUIRED', 'WAITING_FOR_HUMAN'],
    WAITING_FOR_HUMAN: ['REVIEWING', 'COMPLETE', 'FAILED'],
    FOLLOW_UP_REQUIRED: ['READY'],
    FAILED: ['READY', 'SKIPPED'],
    COMPLETE: [],
    SKIPPED: [],
};

function renderTaskSummaryLine(taskSummary) {
    if (!taskSummary || !taskSummary.status_counts) return '';
    const counts = taskSummary.status_counts;
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    if (total === 0) return '';
    const completed = counts.COMPLETE || 0;
    const parts = [`${completed} of ${total} task${total > 1 ? 's' : ''} complete`];
    if (taskSummary.weakest_completeness !== null && taskSummary.weakest_completeness !== undefined) {
        parts.push(`weakest completeness ${taskSummary.weakest_completeness}`);
    }
    if (taskSummary.merged_gaps && taskSummary.merged_gaps.length) {
        parts.push(`${taskSummary.merged_gaps.length} open gap${taskSummary.merged_gaps.length > 1 ? 's' : ''}`);
    }
    return `<div class="task-summary-line" onclick="event.stopPropagation()">${parts.join(' &middot; ')}</div>`;
}

function renderTaskList(phaseKey, tasks) {
    const addBtn = `<button class="btn-add-task" onclick="event.stopPropagation(); promptCreateResearchTask('${phaseKey}')">+ Add task</button>`;
    if (!tasks.length) {
        return `<div class="task-list-empty">No research tasks yet.</div>${addBtn}`;
    }
    const rows = tasks.map(t => {
        const entities = (t.entities && t.entities.length)
            ? ` <span class="task-entities">(${t.entities.map(escapeHtml).join(', ')})</span>`
            : '';
        const options = (TASK_TRANSITIONS[t.status] || [])
            .map(s => `<option value="${s}">${s}</option>`).join('');
        return `<li class="task-row" data-task-id="${t.id}">
            <span class="task-status-badge task-status-${t.status}">${t.status}</span>
            <span class="task-title">${escapeHtml(t.title)}</span>${entities}
            <select class="task-transition-select" onchange="event.stopPropagation(); if(this.value){transitionResearchTask(${t.id}, this.value);} this.value='';">
                <option value="">Change status&hellip;</option>
                ${options}
            </select>
        </li>`;
    }).join('');
    return `<ul class="task-list">${rows}</ul>${addBtn}`;
}

async function refreshPhaseTaskPanels() {
    if (!currentProject) return;
    let tasks = [];
    try {
        const res = await fetch(`/api/research-tasks?project=${encodeURIComponent(currentProject)}`);
        const json = await res.json();
        tasks = Array.isArray(json.tasks) ? json.tasks : [];
    } catch (e) {
        console.error('[research-tasks] failed to load:', e);
        return;
    }
    const byPhase = {};
    tasks.forEach(t => {
        const key = t.phase_key || '';
        if (!byPhase[key]) byPhase[key] = [];
        byPhase[key].push(t);
    });
    document.querySelectorAll('.phase-task-panel').forEach(panel => {
        const key = panel.id.replace('phase-task-panel-', '');
        panel.innerHTML = renderTaskList(key, byPhase[key] || []);
    });
}

function promptCreateResearchTask(phaseKey) {
    const title = (prompt('Task title (e.g. "Product / La Trobe"):') || '').trim();
    if (!title) return;
    createResearchTask(phaseKey, title);
}

async function createResearchTask(phaseKey, title) {
    try {
        const res = await fetch('/api/research-tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project: currentProject, phase_key: phaseKey, title })
        });
        const json = await res.json();
        if (!json.success) alert('Could not create task: ' + (json.error || 'unknown error'));
    } catch (e) {
        alert('Could not create task: ' + e.message);
    } finally {
        refreshPhaseTaskPanels();
    }
}

async function transitionResearchTask(taskId, newStatus) {
    try {
        const res = await fetch(`/api/research-tasks/${taskId}/transition`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus })
        });
        const json = await res.json();
        if (!json.success) alert('Could not change status: ' + (json.error || 'unknown error'));
    } catch (e) {
        alert('Could not change status: ' + e.message);
    } finally {
        refreshPhaseTaskPanels();
    }
}
```

- [ ] **Step 4: Add CSS**

Append to `static/style.css` (after the `.linked-file-chip button:hover` rule, around line 1060):

```css
.task-summary-line {
    padding: 0.4rem 1rem 0;
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--oes-ink);
}
.phase-task-panel {
    padding: 0.6rem 1rem;
    border-top: 1px solid var(--border-color);
}
.task-list-empty {
    font-size: 0.75rem;
    color: #999;
    margin-bottom: 0.4rem;
}
.task-list {
    list-style: none;
    margin: 0 0 0.4rem;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.task-row {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    font-size: 0.75rem;
}
.task-status-badge {
    font-size: 0.65rem;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 10px;
    background: #eee;
    color: var(--oes-ink);
    white-space: nowrap;
}
.task-status-COMPLETE { background: #d9f2e3; }
.task-status-FAILED { background: #fbdada; }
.task-status-RUNNING { background: #fde6cc; }
.task-entities {
    color: #777;
    font-size: 0.7rem;
}
.task-transition-select {
    font-size: 0.7rem;
    padding: 2px 4px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
}
.btn-add-task {
    font-size: 0.72rem;
    padding: 3px 8px;
    border: 1px solid var(--oes-sky);
    border-radius: 4px;
    background: white;
    color: var(--oes-ink);
    cursor: pointer;
}
.btn-add-task:hover {
    background: #e6f4f6;
}
```

- [ ] **Step 5: Manual verification**

Run: `python app.py` (or use the project's existing dev-server workflow), then in a browser:
1. Open a project, go to the Insights tab.
2. Confirm every phase card now shows an (initially empty) task panel with a "+ Add task" button below the linked-files section, and no roll-up line yet (no tasks exist).
3. Click "+ Add task" on phase 4, enter "Product / La Trobe" — confirm it appears in the list with a `PROPOSED` badge, and the panel's "Change status…" dropdown offers `READY`/`SKIPPED`.
4. Change its status to `READY` — confirm the badge updates and a roll-up line appears above the panel ("0 of 1 tasks complete").
5. Open the History view and step to a previous version (if any exist) — confirm the task panel and roll-up line do NOT appear under the "(historical)" label (this is the Review Focus item from this plan's header).
6. Confirm the existing phase summary text, confidence badge, linked-files chips, and "Click to edit" behavior are all unchanged from before this task.

- [ ] **Step 6: Commit**

```bash
git add static/app.js static/style.css
git commit -m "Add additive research-task panel to the Insights phase cards"
```

---

### Final Steps (after all 7 tasks)

- [ ] Run the full test suite: `python -m pytest tests/ -v` — expect all tests from before this plan (177) plus every new test added across Tasks 1-6 to pass, zero regressions.
- [ ] Run `python -m ruff check .` — fix anything newly introduced (the two pre-existing findings from before this plan, in `db/migrate.py` and `db/repositories/chat_repo.py`, are not this plan's concern).
- [ ] Use `superpowers:finishing-a-development-branch` to decide how this work gets merged/pushed.
