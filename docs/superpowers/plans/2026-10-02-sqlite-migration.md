# Phase 1 — SQLite Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON-file-per-concern storage model under `projects/{name}/` with a single SQLite database (`instance/app.db`), accessed through a new repository layer, with zero changes to any existing route's JSON request/response shape and zero changes to `static/app.js`.

**Architecture:** New `db/` package (connection helper, numbered SQL migrations, one repository module per table group). Every existing `services/*.py` function keeps its name/signature/return shape; only its internals swap from `services/storage.py`'s JSON read/write to repository calls. One new `services/insights_service.py` holds genuinely new logic (versioned insights read/write). `routes/files.py` and `routes/projects.py` gain a narrow special-case for three reserved filenames (`insights.json`, `insights_history.json`, `excluded_competitors.json`) that redirects to `insights_service` instead of plain file I/O.

**Tech Stack:** Python stdlib `sqlite3` (no ORM, no new dependencies), Flask 3.0, pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-sqlite-migration-design.md`

## Global Constraints

- Zero new pip dependencies (stdlib `sqlite3` only).
- Every `services/*.py` public function signature and return shape stays byte-identical to what it is today — callers (`routes/*.py`) are never touched except the two named exceptions below.
- `routes/files.py` (`POST /api/files`), `routes/projects.py` (`GET /api/projects/<name>`), and `routes/chats.py` are the *only three* route files touched. The first two special-case three reserved filenames; `routes/chats.py` is touched because it bypasses `chat_service.py` entirely today (discovered during this plan's self-review — see Task 18b) and must stop doing so before Task 20 removes the JSON primitives it currently calls directly.
- `projects/{name}/files/` and `projects/{name}/outputs/` stay on disk — only the JSON metadata files move to SQLite.
- `system_prompt.txt`/`project_prompt.txt` stay as plain text files (out of scope) — `project_service.load_project_prompt`/`save_project_prompt` and `services/storage.py`'s `read_text`/`write_text` are not touched.
- `db/connection.py` must not depend on Flask's `current_app` — it exposes `set_database_path(path)` so tests and the CLI migration script can point it at a temp file without an app context, matching this repo's existing test style (`tests/test_file_index_service.py` patches path-resolution functions directly, no Flask app context).
- `instance/` is added to `.gitignore`; `instance/app.db` is never committed.
- Original `projects/*/*.json` files are never modified or deleted by any code in this plan.

## Review Focus

- A project whose `insights.json`/`insights_history.json` has never been touched (true of all 7 real sample projects today) must migrate and load cleanly with zero `research_tasks`/`findings` rows — not raise an error.
- Renaming an uploaded file must not break its phase links or selected-file state, since `sources`/`project_selected_sources`/`finding_evidence` all key off the stable `source_id`, not filename — a reasonable person expects a rename to be a non-event for these links (this actually gets *more* reliable than today's name-based matching, see Task 18).
- Deleting a file that's linked to a phase must unlink it from the *current* insights version only, exactly like today's behavior — a past (historical) version must keep showing the content as it was, not have its evidence silently vanish.
- Re-running the migration script after a partial failure (e.g. disk full mid-run) must not duplicate rows or crash on the second run — a reasonable person re-runs a failed migration without first hand-cleaning the database.
- Saving insights data for a project that has never had a `projects` row migrated yet (e.g. a project created fresh, post-cutover, that never existed as JSON) must still work — `insights_service.save_insights` must not assume `project_service.create_project` already ran a migration-era step.

---

## File Structure

New:
- `db/__init__.py`
- `db/connection.py` — connection factory + path override
- `db/migrate_runner.py` — applies numbered `.sql` files, tracks `schema_migrations`
- `db/migrations/0001_initial.sql` — full schema
- `db/migrate.py` — one-shot CLI: JSON → SQLite
- `db/repositories/__init__.py`
- `db/repositories/projects_repo.py`
- `db/repositories/sources_repo.py` (covers `sources` + `project_selected_sources`)
- `db/repositories/artefacts_repo.py`
- `db/repositories/chat_repo.py` (covers `chat_sessions` + `project_messages`)
- `db/repositories/research_runs_repo.py`
- `db/repositories/research_tasks_repo.py` (covers `research_tasks`, `project_insight_versions`, `findings`, `evidence`, `finding_evidence`)
- `db/repositories/excluded_competitors_repo.py`
- `db/repositories/agent_decisions_repo.py`
- `services/phases.py` — `PHASE_DEFINITIONS` ported from `static/app.js`
- `services/insights_service.py`
- `tests/conftest.py` — `temp_db` fixture
- `tests/test_db_migrations.py`
- `tests/test_repositories_projects.py`
- `tests/test_repositories_sources.py`
- `tests/test_repositories_artefacts.py`
- `tests/test_repositories_chat.py`
- `tests/test_repositories_research_runs.py`
- `tests/test_repositories_research_tasks.py`
- `tests/test_repositories_excluded_competitors.py`
- `tests/test_insights_service.py`
- `tests/test_migration_script.py`
- `tests/test_project_service.py`
- `tests/test_artifact_service.py`
- `tests/test_chat_service.py`
- `tests/test_research_run_service.py`
- `tests/test_routes_insights_wiring.py`
- `tests/test_routes_chats.py`

Modified:
- `config.py` — add `DATABASE_PATH`
- `.gitignore` — add `instance/`
- `app.py` — wire `config.DATABASE_PATH` into `db.connection` at startup
- `services/project_service.py` — cut over `config.json`/`metadata.json` to `projects_repo`
- `services/file_index_service.py` — cut over `file_index.json` + selected-files to `sources_repo`
- `services/artifact_service.py` — cut over `artifacts.json` to `artefacts_repo`
- `services/chat_service.py` — cut over `chat_history.json` to `chat_repo`
- `services/research_run_service.py` — cut over `research_runs.json` to `research_runs_repo`
- `services/reference_integrity_service.py` — cut over pruning logic to repos
- `services/storage.py` — remove the now-unused JSON functions, keep `read_text`/`write_text`
- `routes/files.py`, `routes/projects.py` — special-case the three reserved filenames
- `routes/chats.py` — stop bypassing `chat_service.py` (Task 18b)
- `tests/test_storage.py`, `tests/test_file_index_service.py`, `tests/test_reference_integrity_service.py` — updated for the new backing store

---

## Task 1: DB foundation — connection, migration runner, initial schema

**Files:**
- Create: `db/__init__.py`, `db/connection.py`, `db/migrate_runner.py`, `db/migrations/0001_initial.sql`
- Modify: `config.py`, `.gitignore`, `app.py`
- Test: `tests/conftest.py`, `tests/test_db_migrations.py`

**Interfaces:**
- Produces: `db.connection.get_connection()` (context manager yielding a `sqlite3.Connection` with `row_factory=sqlite3.Row`), `db.connection.set_database_path(path)`, `db.connection.get_database_path()`, `db.migrate_runner.apply_migrations()` (idempotent).

- [ ] **Step 1: Write the failing tests**

```python
# tests/conftest.py
import pytest

from db.connection import set_database_path
from db.migrate_runner import apply_migrations


@pytest.fixture
def temp_db(tmp_path):
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    yield
    set_database_path(None)
```

```python
# tests/test_db_migrations.py
from db.connection import get_connection
from db.migrate_runner import apply_migrations


def test_apply_migrations_creates_all_tables(temp_db):
    expected = {
        "projects", "sources", "project_selected_sources", "artefacts",
        "chat_sessions", "project_messages", "research_runs", "research_tasks",
        "project_insight_versions", "findings", "evidence", "finding_evidence",
        "agent_decisions", "excluded_competitors", "schema_migrations",
    }
    with get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert expected.issubset(tables)


def test_apply_migrations_is_idempotent(temp_db):
    apply_migrations()
    apply_migrations()
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    assert count == 1


def test_foreign_keys_enforced(temp_db):
    with get_connection() as conn:
        with_fk_error = False
        try:
            conn.execute(
                "INSERT INTO sources (project_id, stable_file_id, filename, created_at, updated_at) "
                "VALUES (9999, 'f_doesnotexist', 'x.txt', '2026-01-01', '2026-01-01')"
            )
        except Exception:
            with_fk_error = True
        assert with_fk_error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db_migrations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Implement**

```python
# db/__init__.py
```

```python
# db/connection.py
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_DEFAULT_DB_PATH = "instance/app.db"
_db_path_override = None


def set_database_path(path):
    """Override the DB path. Pass None to clear the override (tests only)."""
    global _db_path_override
    _db_path_override = path


def get_database_path():
    if _db_path_override:
        return Path(_db_path_override)
    return Path(os.environ.get("DATABASE_PATH", _DEFAULT_DB_PATH))


@contextmanager
def get_connection():
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
    finally:
        conn.close()
```

```python
# db/migrate_runner.py
from datetime import datetime
from pathlib import Path

from .connection import get_connection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _ensure_migrations_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn):
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def _migration_files():
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_migrations():
    with get_connection() as conn:
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)
        for path in _migration_files():
            version = path.stem
            if version in applied:
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now().isoformat()),
            )
```

```sql
-- db/migrations/0001_initial.sql
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE sources (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    stable_file_id  TEXT NOT NULL,
    filename        TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'upload',
    retrieved_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(project_id, stable_file_id)
);

CREATE TABLE project_selected_sources (
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, source_id)
);

CREATE TABLE artefacts (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    source_id   INTEGER REFERENCES sources(id),
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE chat_sessions (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE project_messages (
    id              INTEGER PRIMARY KEY,
    chat_session_id TEXT NOT NULL REFERENCES chat_sessions(id),
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    artefact_id     TEXT REFERENCES artefacts(id),
    created_at      TEXT NOT NULL,
    UNIQUE(chat_session_id, seq)
);

CREATE TABLE research_runs (
    id              TEXT PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    response_id     TEXT,
    chat_session_id TEXT REFERENCES chat_sessions(id),
    status          TEXT NOT NULL,
    artefact_id     TEXT REFERENCES artefacts(id),
    prompt_preview  TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE research_tasks (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    phase_key   TEXT,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'not_started',
    confidence  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(project_id, phase_key)
);

CREATE TABLE project_insight_versions (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    version         INTEGER NOT NULL,
    generated_at    TEXT NOT NULL,
    competitors_json TEXT,
    competitor_landscape_markdown TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE TABLE findings (
    id                  INTEGER PRIMARY KEY,
    research_task_id    INTEGER NOT NULL REFERENCES research_tasks(id),
    insight_version_id  INTEGER NOT NULL REFERENCES project_insight_versions(id),
    content             TEXT NOT NULL,
    gaps_notes          TEXT,
    confidence          TEXT,
    effective_date      TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(research_task_id, insight_version_id)
);

CREATE TABLE evidence (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    source_id       INTEGER REFERENCES sources(id),
    raw_text        TEXT,
    retrieved_at    TEXT,
    effective_date  TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE finding_evidence (
    finding_id  INTEGER NOT NULL REFERENCES findings(id),
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (finding_id, evidence_id)
);

CREATE TABLE agent_decisions (
    id               INTEGER PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES projects(id),
    research_task_id INTEGER REFERENCES research_tasks(id),
    decision_type    TEXT NOT NULL,
    detail           TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE excluded_competitors (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    competitor_name TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(project_id, competitor_name)
);
```

```python
# config.py — add alongside existing settings
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "instance/app.db")
```

```python
# app.py — inside create_app(), after app.config.from_object(Config)
    from db.connection import set_database_path
    set_database_path(app.config["DATABASE_PATH"])
```

```gitignore
# .gitignore — add
instance/
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db_migrations.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/ config.py .gitignore app.py tests/conftest.py tests/test_db_migrations.py
git commit -m "feat(db): add SQLite connection, migration runner, and initial schema"
```

---

## Task 2: `projects_repo`

**Files:**
- Create: `db/repositories/__init__.py`, `db/repositories/projects_repo.py`
- Test: `tests/test_repositories_projects.py`

**Interfaces:**
- Consumes: `db.connection.get_connection` (Task 1).
- Produces: `get_or_create_id(name) -> int`, `get_id(name) -> int | None`, `get_metadata(name) -> {"description": str, "archived": bool}`, `save_metadata(name, description, archived) -> None`, `list_all() -> list[{"name", "description", "archived"}]`, `exists(name) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories_projects.py
from db.repositories import projects_repo


def test_get_or_create_id_creates_then_reuses(temp_db):
    id1 = projects_repo.get_or_create_id("Proj A")
    id2 = projects_repo.get_or_create_id("Proj A")
    assert id1 == id2
    assert projects_repo.get_id("Proj A") == id1


def test_get_id_returns_none_for_unknown_project(temp_db):
    assert projects_repo.get_id("Nope") is None


def test_metadata_round_trip(temp_db):
    projects_repo.get_or_create_id("Proj B")
    projects_repo.save_metadata("Proj B", "a description", True)
    assert projects_repo.get_metadata("Proj B") == {"description": "a description", "archived": True}


def test_metadata_defaults_for_unknown_project(temp_db):
    assert projects_repo.get_metadata("Nope") == {"description": "", "archived": False}


def test_list_all(temp_db):
    projects_repo.get_or_create_id("Z Project")
    projects_repo.get_or_create_id("A Project")
    names = [p["name"] for p in projects_repo.list_all()]
    assert names == ["A Project", "Z Project"]


def test_exists(temp_db):
    assert projects_repo.exists("Ghost") is False
    projects_repo.get_or_create_id("Ghost")
    assert projects_repo.exists("Ghost") is True


def test_get_created_at(temp_db):
    projects_repo.get_or_create_id("P")
    created = projects_repo.get_created_at("P")
    assert created is not None
    assert projects_repo.get_created_at("Nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_projects.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.repositories'`

- [ ] **Step 3: Implement**

```python
# db/repositories/__init__.py
```

```python
# db/repositories/projects_repo.py
from datetime import datetime

from ..connection import get_connection


def get_or_create_id(name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO projects (name, description, archived, created_at, updated_at) "
            "VALUES (?, '', 0, ?, ?)",
            (name, now, now),
        )
        return cur.lastrowid


def get_id(name):
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None


def exists(name):
    return get_id(name) is not None


def get_created_at(name):
    with get_connection() as conn:
        row = conn.execute("SELECT created_at FROM projects WHERE name = ?", (name,)).fetchone()
        return row["created_at"] if row else None


def get_metadata(name):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT description, archived FROM projects WHERE name = ?", (name,)
        ).fetchone()
    if not row:
        return {"description": "", "archived": False}
    return {"description": row["description"] or "", "archived": bool(row["archived"])}


def save_metadata(name, description, archived):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE projects SET description = ?, archived = ?, updated_at = ? WHERE name = ?",
            (description, 1 if archived else 0, now, name),
        )


def list_all():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, description, archived FROM projects ORDER BY name"
        ).fetchall()
    return [
        {"name": r["name"], "description": r["description"] or "", "archived": bool(r["archived"])}
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_projects.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/repositories/__init__.py db/repositories/projects_repo.py tests/test_repositories_projects.py
git commit -m "feat(db): add projects repository"
```

---

## Task 3: `sources_repo` (sources + project_selected_sources)

**Files:**
- Create: `db/repositories/sources_repo.py`
- Test: `tests/test_repositories_sources.py`

**Interfaces:**
- Consumes: `projects_repo.get_or_create_id` (Task 2).
- Produces: `upsert(project_id, stable_file_id, filename, kind='upload') -> int` (source id), `get_by_stable_id(project_id, stable_file_id) -> row|None`, `get_by_filename(project_id, filename) -> row|None`, `list_for_project(project_id) -> list[row]`, `rename(source_id, new_filename) -> None`, `delete(source_id) -> None`, `set_selected(project_id, source_id, selected: bool) -> None`, `list_selected_ids(project_id) -> list[int]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories_sources.py
from db.repositories import projects_repo, sources_repo


def test_upsert_creates_then_reuses(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid1 = sources_repo.upsert(pid, "f_abc", "doc.txt")
    sid2 = sources_repo.upsert(pid, "f_abc", "doc.txt")
    assert sid1 == sid2


def test_get_by_stable_id_and_filename(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "doc.txt")
    assert sources_repo.get_by_stable_id(pid, "f_abc")["id"] == sid
    assert sources_repo.get_by_filename(pid, "doc.txt")["id"] == sid
    assert sources_repo.get_by_filename(pid, "nope.txt") is None


def test_rename_preserves_id(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "old.txt")
    sources_repo.rename(sid, "new.txt")
    assert sources_repo.get_by_stable_id(pid, "f_abc")["filename"] == "new.txt"


def test_delete_removes_source(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "doc.txt")
    sources_repo.delete(sid)
    assert sources_repo.get_by_stable_id(pid, "f_abc") is None


def test_selected_sources_round_trip(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid1 = sources_repo.upsert(pid, "f_1", "a.txt")
    sid2 = sources_repo.upsert(pid, "f_2", "b.txt")
    sources_repo.set_selected(pid, sid1, True)
    assert sources_repo.list_selected_ids(pid) == [sid1]
    sources_repo.set_selected(pid, sid2, True)
    assert set(sources_repo.list_selected_ids(pid)) == {sid1, sid2}
    sources_repo.set_selected(pid, sid1, False)
    assert sources_repo.list_selected_ids(pid) == [sid2]


def test_delete_source_also_unselects_it(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_1", "a.txt")
    sources_repo.set_selected(pid, sid, True)
    sources_repo.delete(sid)
    assert sources_repo.list_selected_ids(pid) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.repositories.sources_repo'`

- [ ] **Step 3: Implement**

```python
# db/repositories/sources_repo.py
from datetime import datetime

from ..connection import get_connection


def upsert(project_id, stable_file_id, filename, kind="upload"):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM sources WHERE project_id = ? AND stable_file_id = ?",
            (project_id, stable_file_id),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO sources (project_id, stable_file_id, filename, kind, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, stable_file_id, filename, kind, now, now),
        )
        return cur.lastrowid


def get_by_stable_id(project_id, stable_file_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE project_id = ? AND stable_file_id = ?",
            (project_id, stable_file_id),
        ).fetchone()


def get_by_filename(project_id, filename):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE project_id = ? AND filename = ?",
            (project_id, filename),
        ).fetchone()


def list_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE project_id = ? ORDER BY filename", (project_id,)
        ).fetchall()


def rename(source_id, new_filename):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE sources SET filename = ?, updated_at = ? WHERE id = ?",
            (new_filename, now, source_id),
        )


def delete(source_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM project_selected_sources WHERE source_id = ?", (source_id,))
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))


def set_selected(project_id, source_id, selected):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        if selected:
            conn.execute(
                "INSERT OR IGNORE INTO project_selected_sources (project_id, source_id, created_at) "
                "VALUES (?, ?, ?)",
                (project_id, source_id, now),
            )
        else:
            conn.execute(
                "DELETE FROM project_selected_sources WHERE project_id = ? AND source_id = ?",
                (project_id, source_id),
            )


def list_selected_ids(project_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT source_id FROM project_selected_sources WHERE project_id = ? "
            "ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return [r["source_id"] for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_sources.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/repositories/sources_repo.py tests/test_repositories_sources.py
git commit -m "feat(db): add sources repository with selected-sources tracking"
```

---

## Task 4: `artefacts_repo`

**Files:**
- Create: `db/repositories/artefacts_repo.py`
- Test: `tests/test_repositories_artefacts.py`

**Interfaces:**
- Consumes: `projects_repo`, `sources_repo` (Tasks 2-3).
- Produces: `upsert(id, project_id, name, source_id=None) -> None`, `get(id) -> row|None`, `list_for_project(project_id) -> list[row]`, `rename(id, new_name) -> None`, `delete(id) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories_artefacts.py
from db.repositories import artefacts_repo, projects_repo


def test_upsert_then_get(temp_db):
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "My Artefact")
    row = artefacts_repo.get("art_1")
    assert row["name"] == "My Artefact"
    assert row["project_id"] == pid


def test_upsert_is_idempotent_update(temp_db):
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "First Name")
    artefacts_repo.upsert("art_1", pid, "Renamed")
    assert artefacts_repo.get("art_1")["name"] == "Renamed"


def test_list_for_project(temp_db):
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "A")
    artefacts_repo.upsert("art_2", pid, "B")
    names = {row["name"] for row in artefacts_repo.list_for_project(pid)}
    assert names == {"A", "B"}


def test_delete(temp_db):
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "A")
    artefacts_repo.delete("art_1")
    assert artefacts_repo.get("art_1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_artefacts.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# db/repositories/artefacts_repo.py
from datetime import datetime

from ..connection import get_connection


def upsert(id, project_id, name, source_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM artefacts WHERE id = ?", (id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE artefacts SET name = ?, source_id = ?, updated_at = ? WHERE id = ?",
                (name, source_id, now, id),
            )
        else:
            conn.execute(
                "INSERT INTO artefacts (id, project_id, source_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (id, project_id, source_id, name, now, now),
            )


def get(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM artefacts WHERE id = ?", (id,)).fetchone()


def list_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM artefacts WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()


def rename(id, new_name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE artefacts SET name = ?, updated_at = ? WHERE id = ?", (new_name, now, id))


def delete(id):
    with get_connection() as conn:
        conn.execute("DELETE FROM artefacts WHERE id = ?", (id,))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_artefacts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/repositories/artefacts_repo.py tests/test_repositories_artefacts.py
git commit -m "feat(db): add artefacts repository"
```

---

## Task 5: `chat_repo` (chat_sessions + project_messages)

**Files:**
- Create: `db/repositories/chat_repo.py`
- Test: `tests/test_repositories_chat.py`

**Interfaces:**
- Consumes: `projects_repo`, `artefacts_repo` (Tasks 2, 4).
- Produces: `create_session(id, project_id, name) -> None`, `rename_session(id, new_name) -> None`, `delete_session(id) -> None`, `get_session(id) -> row|None`, `list_sessions_for_project(project_id) -> list[row]`, `append_message(chat_session_id, role, content) -> int` (returns `seq`), `list_messages(chat_session_id) -> list[row]`, `link_artefact_to_message(chat_session_id, seq, artefact_id) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories_chat.py
from db.repositories import chat_repo, projects_repo


def test_create_and_get_session(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Research May 01")
    row = chat_repo.get_session("chat_1")
    assert row["name"] == "Research May 01"


def test_rename_session(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Old")
    chat_repo.rename_session("chat_1", "New")
    assert chat_repo.get_session("chat_1")["name"] == "New"


def test_append_message_increments_seq(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    seq1 = chat_repo.append_message("chat_1", "user", "hello")
    seq2 = chat_repo.append_message("chat_1", "assistant", "hi there")
    assert seq1 == 1
    assert seq2 == 2
    messages = chat_repo.list_messages("chat_1")
    assert [m["content"] for m in messages] == ["hello", "hi there"]


def test_link_artefact_to_message(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    seq = chat_repo.append_message("chat_1", "assistant", "output")
    from db.repositories import artefacts_repo
    artefacts_repo.upsert("art_1", pid, "Artefact")
    assert chat_repo.link_artefact_to_message("chat_1", seq, "art_1") is True
    messages = chat_repo.list_messages("chat_1")
    assert messages[0]["artefact_id"] == "art_1"


def test_link_artefact_to_missing_message_returns_false(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    assert chat_repo.link_artefact_to_message("chat_1", 99, "art_1") is False


def test_delete_session_removes_messages(temp_db):
    pid = projects_repo.get_or_create_id("P")
    chat_repo.create_session("chat_1", pid, "Chat")
    chat_repo.append_message("chat_1", "user", "hi")
    chat_repo.delete_session("chat_1")
    assert chat_repo.get_session("chat_1") is None
    assert chat_repo.list_messages("chat_1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_chat.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# db/repositories/chat_repo.py
from datetime import datetime

from ..connection import get_connection


def create_session(id, project_id, name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (id, project_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (id, project_id, name, now, now),
        )


def get_session(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (id,)).fetchone()


def rename_session(id, new_name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE chat_sessions SET name = ?, updated_at = ? WHERE id = ?", (new_name, now, id)
        )


def list_sessions_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM chat_sessions WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()


def delete_session(id):
    with get_connection() as conn:
        conn.execute("DELETE FROM project_messages WHERE chat_session_id = ?", (id,))
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (id,))


def append_message(chat_session_id, role, content, artefact_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM project_messages WHERE chat_session_id = ?",
            (chat_session_id,),
        ).fetchone()
        next_seq = row["max_seq"] + 1
        conn.execute(
            "INSERT INTO project_messages (chat_session_id, seq, role, content, artefact_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_session_id, next_seq, role, content, artefact_id, now),
        )
        return next_seq


def list_messages(chat_session_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM project_messages WHERE chat_session_id = ? ORDER BY seq",
            (chat_session_id,),
        ).fetchall()


def link_artefact_to_message(chat_session_id, seq, artefact_id):
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE project_messages SET artefact_id = ? WHERE chat_session_id = ? AND seq = ?",
            (artefact_id, chat_session_id, seq),
        )
        return cur.rowcount > 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_chat.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/repositories/chat_repo.py tests/test_repositories_chat.py
git commit -m "feat(db): add chat repository"
```

---

## Task 6: `research_runs_repo`

**Files:**
- Create: `db/repositories/research_runs_repo.py`
- Test: `tests/test_repositories_research_runs.py`

**Interfaces:**
- Consumes: `projects_repo`, `chat_repo` (Tasks 2, 5).
- Produces: `create(id, project_id, response_id, chat_session_id, prompt_preview, status='running') -> None`, `get(id) -> row|None`, `update(id, **fields) -> bool`, `list_for_project(project_id) -> list[row]`, `find_recent_running_or_queued_with_preview(project_id, prompt_preview, cutoff_iso) -> row|None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories_research_runs.py
from datetime import datetime, timedelta

from db.repositories import projects_repo, research_runs_repo


def test_create_and_get(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "prompt preview")
    row = research_runs_repo.get("run_1")
    assert row["status"] == "running"
    assert row["response_id"] == "resp_1"


def test_update_fields(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "preview")
    assert research_runs_repo.update("run_1", status="completed", artefact_id="art_1") is True
    row = research_runs_repo.get("run_1")
    assert row["status"] == "completed"
    assert row["artefact_id"] == "art_1"


def test_update_unknown_run_returns_false(temp_db):
    assert research_runs_repo.update("nope", status="failed") is False


def test_list_for_project(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "preview")
    research_runs_repo.create("run_2", pid, "resp_2", None, "preview")
    assert {r["id"] for r in research_runs_repo.list_for_project(pid)} == {"run_1", "run_2"}


def test_find_recent_duplicate_by_preview(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "same preview")
    cutoff = (datetime.now() - timedelta(seconds=30)).isoformat()
    found = research_runs_repo.find_recent_running_or_queued_with_preview(pid, "same preview", cutoff)
    assert found is not None
    not_found = research_runs_repo.find_recent_running_or_queued_with_preview(pid, "different", cutoff)
    assert not_found is None


def test_find_recent_duplicate_ignores_completed_runs(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, "resp_1", None, "same preview")
    research_runs_repo.update("run_1", status="completed")
    cutoff = (datetime.now() - timedelta(seconds=30)).isoformat()
    found = research_runs_repo.find_recent_running_or_queued_with_preview(pid, "same preview", cutoff)
    assert found is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_research_runs.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# db/repositories/research_runs_repo.py
from datetime import datetime

from ..connection import get_connection


def create(id, project_id, response_id, chat_session_id, prompt_preview, status="running"):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO research_runs "
            "(id, project_id, response_id, chat_session_id, status, prompt_preview, "
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id, project_id, response_id, chat_session_id, status, prompt_preview, now, now),
        )


def get(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM research_runs WHERE id = ?", (id,)).fetchone()


def update(id, **fields):
    if not fields:
        return get(id) is not None
    fields["updated_at"] = datetime.now().isoformat()
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [id]
    with get_connection() as conn:
        cur = conn.execute(f"UPDATE research_runs SET {columns} WHERE id = ?", values)
        return cur.rowcount > 0


def list_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_runs WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()


def find_recent_running_or_queued_with_preview(project_id, prompt_preview, cutoff_iso):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_runs WHERE project_id = ? AND prompt_preview = ? "
            "AND status IN ('queued', 'running') AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id, prompt_preview, cutoff_iso),
        ).fetchone()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_research_runs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/repositories/research_runs_repo.py tests/test_repositories_research_runs.py
git commit -m "feat(db): add research runs repository"
```

---

## Task 7: `research_tasks_repo` (tasks, insight versions, findings, evidence, finding_evidence)

**Files:**
- Create: `db/repositories/research_tasks_repo.py`
- Test: `tests/test_repositories_research_tasks.py`

**Interfaces:**
- Consumes: `projects_repo`, `sources_repo` (Tasks 2-3).
- Produces: `get_or_create_task(project_id, phase_key, title) -> int`, `create_ad_hoc_task(project_id, title) -> int`, `list_tasks_for_project(project_id) -> list[row]`, `next_version_number(project_id) -> int`, `create_version(project_id, version, generated_at, competitors_json, competitor_landscape_markdown) -> int`, `list_versions_for_project(project_id) -> list[row]`, `get_latest_version(project_id) -> row|None`, `create_finding(research_task_id, insight_version_id, content, gaps_notes, confidence) -> int`, `list_findings_for_version(insight_version_id) -> list[row]`, `create_evidence(project_id, raw_text, source_id=None) -> int`, `link_finding_evidence(finding_id, evidence_id) -> None`, `list_evidence_for_finding(finding_id) -> list[row]`, `unlink_evidence_by_source_in_version(insight_version_id, source_id) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories_research_tasks.py
from db.repositories import projects_repo, research_tasks_repo, sources_repo


def test_get_or_create_task_is_idempotent_per_phase(temp_db):
    pid = projects_repo.get_or_create_id("P")
    tid1 = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    tid2 = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    assert tid1 == tid2


def test_create_ad_hoc_task_allows_multiple_per_project(temp_db):
    pid = projects_repo.get_or_create_id("P")
    tid1 = research_tasks_repo.create_ad_hoc_task(pid, "Look into X")
    tid2 = research_tasks_repo.create_ad_hoc_task(pid, "Look into Y")
    assert tid1 != tid2


def test_version_numbering_increments(temp_db):
    pid = projects_repo.get_or_create_id("P")
    assert research_tasks_repo.next_version_number(pid) == 1
    research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    assert research_tasks_repo.next_version_number(pid) == 2


def test_get_latest_version(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    research_tasks_repo.create_version(pid, 2, "2026-01-02T00:00:00", "[]", "")
    assert research_tasks_repo.get_latest_version(pid)["version"] == 2


def test_get_latest_version_none_when_no_versions(temp_db):
    pid = projects_repo.get_or_create_id("P")
    assert research_tasks_repo.get_latest_version(pid) is None


def test_finding_and_evidence_round_trip(temp_db):
    pid = projects_repo.get_or_create_id("P")
    tid = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    vid = research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    fid = research_tasks_repo.create_finding(tid, vid, "Summary text", "Some gap", "medium")
    sid = sources_repo.upsert(pid, "f_1", "report.pdf")
    eid1 = research_tasks_repo.create_evidence(pid, None, source_id=sid)
    eid2 = research_tasks_repo.create_evidence(pid, "freeform citation text")
    research_tasks_repo.link_finding_evidence(fid, eid1)
    research_tasks_repo.link_finding_evidence(fid, eid2)

    findings = research_tasks_repo.list_findings_for_version(vid)
    assert len(findings) == 1
    assert findings[0]["content"] == "Summary text"

    evidence = research_tasks_repo.list_evidence_for_finding(fid)
    assert len(evidence) == 2


def test_get_finding_for_task_in_version_is_scoped_correctly(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task1 = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    task2 = research_tasks_repo.get_or_create_task(pid, "2", "The Student")
    vid = research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    research_tasks_repo.create_finding(task1, vid, "Phase 1 summary", None, "medium")
    research_tasks_repo.create_finding(task2, vid, "Phase 2 summary", None, "medium")

    # The bug this guards against: list_findings_for_version returns BOTH rows,
    # so callers must look up by (task, version), not just take the first result.
    all_in_version = research_tasks_repo.list_findings_for_version(vid)
    assert len(all_in_version) == 2

    phase1_finding = research_tasks_repo.get_finding_for_task_in_version(task1, vid)
    phase2_finding = research_tasks_repo.get_finding_for_task_in_version(task2, vid)
    assert phase1_finding["content"] == "Phase 1 summary"
    assert phase2_finding["content"] == "Phase 2 summary"


def test_unlink_evidence_by_source_in_version(temp_db):
    pid = projects_repo.get_or_create_id("P")
    tid = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    vid = research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    fid = research_tasks_repo.create_finding(tid, vid, "Summary", None, "medium")
    sid = sources_repo.upsert(pid, "f_1", "report.pdf")
    eid = research_tasks_repo.create_evidence(pid, None, source_id=sid)
    research_tasks_repo.link_finding_evidence(fid, eid)

    research_tasks_repo.unlink_evidence_by_source_in_version(vid, sid)
    assert research_tasks_repo.list_evidence_for_finding(fid) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_research_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# db/repositories/research_tasks_repo.py
from datetime import datetime

from ..connection import get_connection


def get_or_create_task(project_id, phase_key, title):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM research_tasks WHERE project_id = ? AND phase_key = ?",
            (project_id, phase_key),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO research_tasks (project_id, phase_key, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, phase_key, title, now, now),
        )
        return cur.lastrowid


def create_ad_hoc_task(project_id, title):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO research_tasks (project_id, phase_key, title, created_at, updated_at) "
            "VALUES (?, NULL, ?, ?, ?)",
            (project_id, title, now, now),
        )
        return cur.lastrowid


def list_tasks_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_tasks WHERE project_id = ? ORDER BY phase_key", (project_id,)
        ).fetchall()


def next_version_number(project_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS max_v FROM project_insight_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return row["max_v"] + 1


def create_version(project_id, version, generated_at, competitors_json, competitor_landscape_markdown):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO project_insight_versions "
            "(project_id, version, generated_at, competitors_json, competitor_landscape_markdown, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, version, generated_at, competitors_json, competitor_landscape_markdown, now),
        )
        return cur.lastrowid


def list_versions_for_project(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM project_insight_versions WHERE project_id = ? ORDER BY version",
            (project_id,),
        ).fetchall()


def get_latest_version(project_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM project_insight_versions WHERE project_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()


def get_version_by_number(project_id, version):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM project_insight_versions WHERE project_id = ? AND version = ?",
            (project_id, version),
        ).fetchone()


def create_finding(research_task_id, insight_version_id, content, gaps_notes, confidence):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO findings "
            "(research_task_id, insight_version_id, content, gaps_notes, confidence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (research_task_id, insight_version_id, content, gaps_notes, confidence, now, now),
        )
        return cur.lastrowid


def list_findings_for_version(insight_version_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM findings WHERE insight_version_id = ?", (insight_version_id,)
        ).fetchall()


def get_finding_for_task_in_version(research_task_id, insight_version_id):
    """Scoped to one phase's finding within one version.

    NOTE: list_findings_for_version() alone is NOT enough to reconstruct a
    single phase — it returns all 7 phases' findings for that version. This
    function is what insights_service._build_phase_data actually needs.
    """
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM findings WHERE research_task_id = ? AND insight_version_id = ?",
            (research_task_id, insight_version_id),
        ).fetchone()


def create_evidence(project_id, raw_text, source_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO evidence (project_id, source_id, raw_text, created_at) VALUES (?, ?, ?, ?)",
            (project_id, source_id, raw_text, now),
        )
        return cur.lastrowid


def link_finding_evidence(finding_id, evidence_id):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO finding_evidence (finding_id, evidence_id) VALUES (?, ?)",
            (finding_id, evidence_id),
        )


def list_evidence_for_finding(finding_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT e.* FROM evidence e "
            "JOIN finding_evidence fe ON fe.evidence_id = e.id "
            "WHERE fe.finding_id = ?",
            (finding_id,),
        ).fetchall()


def unlink_evidence_by_source_in_version(insight_version_id, source_id):
    """Remove finding<->evidence links for a given source, scoped to one version only.

    Used when a file is deleted: only the current/latest version's links are
    pruned (matching today's behavior of only editing the live insights.json),
    historical versions keep their evidence untouched.
    """
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM finding_evidence WHERE evidence_id IN ("
            "  SELECT e.id FROM evidence e WHERE e.source_id = ?"
            ") AND finding_id IN ("
            "  SELECT id FROM findings WHERE insight_version_id = ?"
            ")",
            (source_id, insight_version_id),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_research_tasks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/repositories/research_tasks_repo.py tests/test_repositories_research_tasks.py
git commit -m "feat(db): add research tasks/insight versions/findings/evidence repository"
```

---

## Task 8: `excluded_competitors_repo` and `agent_decisions_repo`

**Files:**
- Create: `db/repositories/excluded_competitors_repo.py`, `db/repositories/agent_decisions_repo.py`
- Test: `tests/test_repositories_excluded_competitors.py`

**Interfaces:**
- Produces: `excluded_competitors_repo.add(project_id, name) -> None`, `remove(project_id, name) -> None`, `list_for_project(project_id) -> list[str]`; `agent_decisions_repo.record(project_id, decision_type, detail, research_task_id=None) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories_excluded_competitors.py
from db.repositories import excluded_competitors_repo, projects_repo


def test_add_and_list(temp_db):
    pid = projects_repo.get_or_create_id("P")
    excluded_competitors_repo.add(pid, "Competitor A")
    excluded_competitors_repo.add(pid, "Competitor B")
    assert set(excluded_competitors_repo.list_for_project(pid)) == {"Competitor A", "Competitor B"}


def test_add_is_idempotent(temp_db):
    pid = projects_repo.get_or_create_id("P")
    excluded_competitors_repo.add(pid, "Competitor A")
    excluded_competitors_repo.add(pid, "Competitor A")
    assert excluded_competitors_repo.list_for_project(pid) == ["Competitor A"]


def test_remove(temp_db):
    pid = projects_repo.get_or_create_id("P")
    excluded_competitors_repo.add(pid, "Competitor A")
    excluded_competitors_repo.remove(pid, "Competitor A")
    assert excluded_competitors_repo.list_for_project(pid) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_excluded_competitors.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# db/repositories/excluded_competitors_repo.py
from datetime import datetime

from ..connection import get_connection


def add(project_id, competitor_name):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO excluded_competitors (project_id, competitor_name, created_at) "
            "VALUES (?, ?, ?)",
            (project_id, competitor_name, now),
        )


def remove(project_id, competitor_name):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM excluded_competitors WHERE project_id = ? AND competitor_name = ?",
            (project_id, competitor_name),
        )


def list_for_project(project_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT competitor_name FROM excluded_competitors WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return [r["competitor_name"] for r in rows]
```

```python
# db/repositories/agent_decisions_repo.py
from datetime import datetime

from ..connection import get_connection


def record(project_id, decision_type, detail, research_task_id=None):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO agent_decisions (project_id, research_task_id, decision_type, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, research_task_id, decision_type, detail, now),
        )
        return cur.lastrowid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_excluded_competitors.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/repositories/excluded_competitors_repo.py db/repositories/agent_decisions_repo.py tests/test_repositories_excluded_competitors.py
git commit -m "feat(db): add excluded competitors and agent decisions repositories"
```

---

## Task 9: `services/phases.py` — port `PHASE_DEFINITIONS`

**Files:**
- Create: `services/phases.py`
- Test: part of `tests/test_insights_service.py` (Task 10) — this constant has no behavior to unit-test on its own beyond import.

**Interfaces:**
- Produces: `PHASE_DEFINITIONS: dict[str, {"title": str, "description": str}]`, `PHASE_KEYS: list[str]` (`["1".."7"]`).

- [ ] **Step 1: Implement directly (pure data, no test-first needed)**

```python
# services/phases.py
"""Phase 1-7 registry, ported from static/app.js's PHASE_DEFINITIONS (app.js:66-95).

This is the first time this registry exists on the backend — previously it
was defined only in browser JS. Kept byte-identical to the JS version so
reconstructed insights.json content matches exactly.
"""

PHASE_DEFINITIONS = {
    "1": {
        "title": "The Landscape",
        "description": (
            "Comprehensive market review using HEIMS and publicly available data — "
            "program types, top-ranked offerings, enrolment trends, provider landscape, "
            "and general sentiment analysis around program value. Focused on current "
            "data (last 2 years)."
        ),
    },
    "2": {
        "title": "The Student",
        "description": (
            "Target student demographics, motivations, pain points, decision drivers, "
            "career stage profiles, and enrolment pathway analysis across competitor "
            "programs."
        ),
    },
    "3": {
        "title": "Review of Marketing",
        "description": (
            "Competitor scoping, website UX/messaging review, sentiment analysis via "
            "social listening, paid and organic channel strategy, and brand "
            "positioning analysis."
        ),
    },
    "4": {
        "title": "Product Features",
        "description": (
            "Granular course-level scraping — delivery modes, unit structures, "
            "specialisations, pricing, duration, flexibility options, technology "
            "platforms, and student experience features."
        ),
    },
    "5": {
        "title": "Academic Content",
        "description": (
            "Deep curriculum analysis for priority competitors — learning outcomes, "
            "assessment design, accreditation, faculty profiles, academic "
            "partnerships, and pedagogical approach."
        ),
    },
    "6": {
        "title": "Industry Engagement",
        "description": (
            "Industry partnerships, employer connections, placement programs, "
            "advisory boards, professional body affiliations, and work-integrated "
            "learning arrangements."
        ),
    },
    "7": {
        "title": "Options for OES",
        "description": (
            "White space analysis and strategic options using the SO WHAT / NOW WHAT "
            "framework — interrogating each key finding across all phases to identify "
            "actionable opportunities for OES."
        ),
    },
}

PHASE_KEYS = list(PHASE_DEFINITIONS.keys())
```

- [ ] **Step 2: Commit**

```bash
git add services/phases.py
git commit -m "feat: port PHASE_DEFINITIONS from app.js to a shared backend constant"
```

---

## Task 10: `services/insights_service.py`

**Files:**
- Create: `services/insights_service.py`
- Test: `tests/test_insights_service.py`

**Interfaces:**
- Consumes: `projects_repo`, `sources_repo`, `research_tasks_repo`, `excluded_competitors_repo` (Tasks 2-3, 7-8), `services.phases.PHASE_DEFINITIONS` (Task 9).
- Produces: `save_insights(project_name, data) -> None`, `load_current_insights(project_name) -> dict`, `load_insights_history(project_name) -> list[dict]`, `save_excluded_competitors(project_name, names: list[str]) -> None`, `load_excluded_competitors(project_name) -> list[str]`.

This is the exact JSON shape being reconstructed (from `createEmptyInsightsData()`/`normalizeInsightsData()` in `static/app.js`):
```json
{
  "generated_at": "...",
  "competitors": [...],
  "competitor_landscape_markdown": "...",
  "phases": {
    "1": {"title": "...", "summary": "...", "confidence": "...", "evidence_sources": [...],
          "gaps": [...], "suggested_topics": [...], "linked_files": [...], "linked_file_ids": [...]},
    ... "2".."7"
  }
}
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_insights_service.py
import json

from db.repositories import projects_repo, sources_repo
from services import insights_service


def _sample_data(summary="A landscape summary", evidence=None, linked_file_id=None):
    data = {
        "generated_at": "2026-01-01T00:00:00",
        "competitors": [{"name": "Comp A"}],
        "competitor_landscape_markdown": "# Landscape",
        "phases": {},
    }
    for key in [str(i) for i in range(1, 8)]:
        data["phases"][key] = {
            "title": f"Phase {key}",
            "summary": summary if key == "1" else "MISSING",
            "confidence": "medium" if key == "1" else "none",
            "evidence_sources": evidence or [] if key == "1" else [],
            "gaps": ["Some gap"] if key == "1" else [],
            "suggested_topics": ["Look into X"] if key == "1" else [],
            "linked_files": [],
            "linked_file_ids": [linked_file_id] if (key == "1" and linked_file_id) else [],
        }
    return data


def test_save_then_load_current_round_trips_phase_1(temp_db):
    insights_service.save_insights("P", _sample_data())
    loaded = insights_service.load_current_insights("P")
    assert loaded["phases"]["1"]["summary"] == "A landscape summary"
    assert loaded["phases"]["1"]["gaps"] == ["Some gap"]
    assert loaded["phases"]["1"]["suggested_topics"] == ["Look into X"]
    assert loaded["competitors"] == [{"name": "Comp A"}]
    assert loaded["competitor_landscape_markdown"] == "# Landscape"
    # Untouched phases still round-trip to their empty defaults
    assert loaded["phases"]["2"]["summary"] == "MISSING"


def test_save_twice_creates_two_versions_and_history_preserves_first(temp_db):
    insights_service.save_insights("P", _sample_data(summary="Version 1 summary"))
    insights_service.save_insights("P", _sample_data(summary="Version 2 summary"))

    current = insights_service.load_current_insights("P")
    assert current["phases"]["1"]["summary"] == "Version 2 summary"

    history = insights_service.load_insights_history("P")
    assert [h["version"] for h in history] == [1, 2]
    assert history[0]["data"]["phases"]["1"]["summary"] == "Version 1 summary"
    assert history[1]["data"]["phases"]["1"]["summary"] == "Version 2 summary"


def test_evidence_sources_with_linked_file_round_trips(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc123", "report.pdf")
    insights_service.save_insights(
        "P", _sample_data(evidence=["freeform citation"], linked_file_id="f_abc123")
    )
    current = insights_service.load_current_insights("P")
    assert current["phases"]["1"]["linked_file_ids"] == ["f_abc123"]
    assert current["phases"]["1"]["linked_files"] == ["report.pdf"]


def test_load_current_insights_for_project_with_no_saves_yet_returns_empty_shape(temp_db):
    loaded = insights_service.load_current_insights("Untouched Project")
    assert loaded["phases"]["1"]["summary"] == "MISSING"
    assert loaded["competitors"] == []


def test_load_insights_history_for_project_with_no_saves_is_empty_list(temp_db):
    assert insights_service.load_insights_history("Untouched Project") == []


def test_excluded_competitors_round_trip(temp_db):
    insights_service.save_excluded_competitors("P", ["Comp A", "Comp B"])
    assert set(insights_service.load_excluded_competitors("P")) == {"Comp A", "Comp B"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_insights_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.insights_service'`

- [ ] **Step 3: Implement**

```python
# services/insights_service.py
import json

from db.repositories import excluded_competitors_repo, projects_repo, research_tasks_repo, sources_repo
from .phases import PHASE_DEFINITIONS


def _empty_phase_payload(phase_key):
    return {
        "title": PHASE_DEFINITIONS[phase_key]["title"],
        "summary": "MISSING",
        "confidence": "none",
        "evidence_sources": [],
        "gaps": [],
        "suggested_topics": [],
        "linked_files": [],
        "linked_file_ids": [],
    }


def save_insights(project_name, data):
    project_id = projects_repo.get_or_create_id(project_name)
    version_number = research_tasks_repo.next_version_number(project_id)
    version_id = research_tasks_repo.create_version(
        project_id,
        version_number,
        data.get("generated_at", ""),
        json.dumps(data.get("competitors", [])),
        data.get("competitor_landscape_markdown", ""),
    )

    phases = data.get("phases", {})
    for phase_key in PHASE_DEFINITIONS:
        phase = phases.get(phase_key, _empty_phase_payload(phase_key))
        task_id = research_tasks_repo.get_or_create_task(
            project_id, phase_key, phase.get("title") or PHASE_DEFINITIONS[phase_key]["title"]
        )
        gaps_notes = "\n".join(phase.get("gaps") or []) or None
        finding_id = research_tasks_repo.create_finding(
            task_id, version_id, phase.get("summary", "MISSING"), gaps_notes, phase.get("confidence")
        )

        linked_ids = set(phase.get("linked_file_ids") or [])
        for raw_text in phase.get("evidence_sources") or []:
            evidence_id = research_tasks_repo.create_evidence(project_id, raw_text)
            research_tasks_repo.link_finding_evidence(finding_id, evidence_id)
        for stable_file_id in linked_ids:
            source_row = sources_repo.get_by_stable_id(project_id, stable_file_id)
            if source_row is None:
                continue
            evidence_id = research_tasks_repo.create_evidence(project_id, None, source_id=source_row["id"])
            research_tasks_repo.link_finding_evidence(finding_id, evidence_id)

        for topic in phase.get("suggested_topics") or []:
            research_tasks_repo.create_ad_hoc_task(project_id, topic)


def _build_phase_data(version_id, phase_key, task_id, project_id):
    # Scoped to (task_id, version_id) — NOT list_findings_for_version() + [0], which
    # would return an arbitrary one of all 7 phases' findings for this version.
    finding = research_tasks_repo.get_finding_for_task_in_version(task_id, version_id)
    phase = _empty_phase_payload(phase_key)
    if finding is None:
        return phase

    phase["summary"] = finding["content"]
    phase["confidence"] = finding["confidence"] or "none"
    phase["gaps"] = finding["gaps_notes"].split("\n") if finding["gaps_notes"] else []

    evidence_rows = research_tasks_repo.list_evidence_for_finding(finding["id"])
    evidence_sources = []
    linked_files = []
    linked_file_ids = []
    for row in evidence_rows:
        if row["source_id"] is not None:
            source = sources_repo.get_by_stable_id(project_id, None) if False else None
        if row["raw_text"]:
            evidence_sources.append(row["raw_text"])
        if row["source_id"] is not None:
            sources = sources_repo.list_for_project(project_id)
            match = next((s for s in sources if s["id"] == row["source_id"]), None)
            if match is not None:
                linked_files.append(match["filename"])
                linked_file_ids.append(match["stable_file_id"])
    phase["evidence_sources"] = evidence_sources
    phase["linked_files"] = linked_files
    phase["linked_file_ids"] = linked_file_ids
    return phase


def _build_insights_payload(project_id, version_row):
    if version_row is None:
        return {
            "generated_at": "",
            "competitors": [],
            "competitor_landscape_markdown": "",
            "phases": {key: _empty_phase_payload(key) for key in PHASE_DEFINITIONS},
        }

    tasks_by_phase = {
        t["phase_key"]: t["id"]
        for t in research_tasks_repo.list_tasks_for_project(project_id)
        if t["phase_key"] is not None
    }
    phases = {}
    for phase_key in PHASE_DEFINITIONS:
        task_id = tasks_by_phase.get(phase_key)
        if task_id is None:
            phases[phase_key] = _empty_phase_payload(phase_key)
        else:
            phases[phase_key] = _build_phase_data(version_row["id"], phase_key, task_id, project_id)

    return {
        "generated_at": version_row["generated_at"],
        "competitors": json.loads(version_row["competitors_json"] or "[]"),
        "competitor_landscape_markdown": version_row["competitor_landscape_markdown"] or "",
        "phases": phases,
    }


def load_current_insights(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    latest = research_tasks_repo.get_latest_version(project_id)
    return _build_insights_payload(project_id, latest)


def load_insights_history(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    versions = research_tasks_repo.list_versions_for_project(project_id)
    return [
        {
            "version": v["version"],
            "generated_at": v["generated_at"],
            "data": _build_insights_payload(project_id, v),
        }
        for v in versions
    ]


def save_excluded_competitors(project_name, names):
    project_id = projects_repo.get_or_create_id(project_name)
    existing = set(excluded_competitors_repo.list_for_project(project_id))
    target = set(names or [])
    for name in target - existing:
        excluded_competitors_repo.add(project_id, name)
    for name in existing - target:
        excluded_competitors_repo.remove(project_id, name)


def load_excluded_competitors(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    return excluded_competitors_repo.list_for_project(project_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_insights_service.py -v`
Expected: PASS

- [ ] **Step 5: Clean up a dead branch found during implementation**

The line `source = sources_repo.get_by_stable_id(project_id, None) if False else None` in `_build_phase_data` is leftover dead code from drafting — remove it:

```python
# services/insights_service.py — in _build_phase_data, delete this line:
        if row["source_id"] is not None:
            source = sources_repo.get_by_stable_id(project_id, None) if False else None
```

Run: `python -m pytest tests/test_insights_service.py -v`
Expected: PASS (unchanged — confirms the removed line was dead)

- [ ] **Step 6: Commit**

```bash
git add services/insights_service.py tests/test_insights_service.py
git commit -m "feat: add insights_service for versioned phase/evidence storage"
```

---

## Task 11: Migration script (`db/migrate.py`)

**Files:**
- Create: `db/migrate.py`
- Test: `tests/test_migration_script.py`

**Interfaces:**
- Consumes: all repositories (Tasks 2-8), `services.insights_service` module-level helpers are not reused here (the migration writes rows directly via repos, since it needs upsert/idempotency semantics `insights_service.save_insights` doesn't provide — `save_insights` always creates a *new* version, which would be wrong on a re-run).
- Produces: `migrate_all_projects(projects_root="projects") -> dict` (per-project row-count report), a `if __name__ == "__main__":` CLI entry point.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_script.py
import json
import shutil
from pathlib import Path

from db.migrate import migrate_all_projects
from db.repositories import (
    artefacts_repo,
    excluded_competitors_repo,
    projects_repo,
    research_runs_repo,
    research_tasks_repo,
    sources_repo,
)


REAL_SAMPLE_PROJECT = Path(__file__).parent.parent / "projects" / "M Proj Mgmt"


def test_migrates_real_sample_project_artefacts_and_runs(temp_db, tmp_path):
    if not REAL_SAMPLE_PROJECT.exists():
        import pytest
        pytest.skip("Real sample project not present in this environment")

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    shutil.copytree(REAL_SAMPLE_PROJECT, projects_root / "M Proj Mgmt")

    with open(projects_root / "M Proj Mgmt" / "artifacts.json", encoding="utf-8") as f:
        original_artifacts = json.load(f)
    with open(projects_root / "M Proj Mgmt" / "research_runs.json", encoding="utf-8") as f:
        original_runs = json.load(f)

    report = migrate_all_projects(str(projects_root))

    pid = projects_repo.get_id("M Proj Mgmt")
    assert pid is not None

    migrated_artefacts = artefacts_repo.list_for_project(pid)
    assert len(migrated_artefacts) == len(original_artifacts)
    for art_id, original in original_artifacts.items():
        migrated = artefacts_repo.get(art_id)
        assert migrated is not None
        assert migrated["name"] == original["name"]

    migrated_runs = research_runs_repo.list_for_project(pid)
    assert len(migrated_runs) == len(original_runs)
    for run_id, original in original_runs.items():
        migrated = research_runs_repo.get(run_id)
        assert migrated is not None
        assert migrated["status"] == original["status"]
        assert migrated["artefact_id"] == original.get("artifact_id")

    assert report["M Proj Mgmt"]["artefacts"] == len(original_artifacts)
    assert report["M Proj Mgmt"]["research_runs"] == len(original_runs)


def test_migrates_project_with_no_insights_json_cleanly(temp_db, tmp_path):
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Bare Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Bare Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")
    (project_dir / "file_index.json").write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")

    report = migrate_all_projects(str(projects_root))

    pid = projects_repo.get_id("Bare Project")
    assert pid is not None
    assert research_tasks_repo.get_latest_version(pid) is None
    assert report["Bare Project"]["research_tasks"] == 0


def test_migration_is_idempotent(temp_db, tmp_path):
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Bare Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Bare Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text(
        json.dumps({
            "chat_1": {"name": "Chat 1", "created": "2026-01-01T00:00:00",
                        "messages": [{"role": "user", "content": "hello"},
                                     {"role": "assistant", "content": "hi there"}]}
        }),
        encoding="utf-8",
    )
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")
    (project_dir / "file_index.json").write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")

    migrate_all_projects(str(projects_root))
    migrate_all_projects(str(projects_root))  # must not raise, duplicate projects, or duplicate messages

    assert len(projects_repo.list_all()) == 1
    from db.repositories import chat_repo
    assert len(chat_repo.list_messages("chat_1")) == 2  # not 4 -- this is what the re-run bug would produce


def test_migrates_excluded_competitors_and_selected_files(temp_db, tmp_path):
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "P"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "files" / "doc.txt").write_text("content", encoding="utf-8")
    (project_dir / "outputs").mkdir()
    (project_dir / "file_index.json").write_text(
        json.dumps({"version": 1, "files": {"f_abc": {"filename": "doc.txt",
                     "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"}}}),
        encoding="utf-8",
    )
    (project_dir / "config.json").write_text(
        json.dumps({"name": "P", "created": "2026-01-01T00:00:00",
                     "selected_files": ["doc.txt"], "selected_file_ids": ["f_abc"]}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")
    (project_dir / "excluded_competitors.json").write_text(
        json.dumps(["Competitor X"]), encoding="utf-8"
    )

    migrate_all_projects(str(projects_root))

    pid = projects_repo.get_id("P")
    assert set(excluded_competitors_repo.list_for_project(pid)) == {"Competitor X"}
    source = sources_repo.get_by_stable_id(pid, "f_abc")
    assert sources_repo.list_selected_ids(pid) == [source["id"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_migration_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.migrate'`

- [ ] **Step 3: Implement**

```python
# db/migrate.py
import json
import sys
from pathlib import Path

from db.repositories import (
    artefacts_repo,
    chat_repo,
    excluded_competitors_repo,
    projects_repo,
    research_runs_repo,
    research_tasks_repo,
    sources_repo,
)
from services.phases import PHASE_DEFINITIONS


def _read_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _migrate_project_core(project_dir, project_name):
    project_id = projects_repo.get_or_create_id(project_name)

    metadata = _read_json(project_dir / "metadata.json", {"description": "", "archived": False})
    projects_repo.save_metadata(
        project_name, metadata.get("description", ""), bool(metadata.get("archived", False))
    )

    file_index = _read_json(project_dir / "file_index.json", {"files": {}})
    stable_id_to_source_id = {}
    for stable_file_id, rec in (file_index.get("files") or {}).items():
        filename = rec.get("filename") if isinstance(rec, dict) else rec
        if not filename:
            continue
        source_id = sources_repo.upsert(project_id, stable_file_id, filename)
        stable_id_to_source_id[stable_file_id] = source_id

    config = _read_json(project_dir / "config.json", {})
    for stable_file_id in config.get("selected_file_ids", []):
        source_id = stable_id_to_source_id.get(stable_file_id)
        if source_id is not None:
            sources_repo.set_selected(project_id, source_id, True)

    artifacts = _read_json(project_dir / "artifacts.json", {})
    for art_id, art in artifacts.items():
        source_id = None
        backing_filename = art.get("filename")
        if backing_filename:
            source_row = sources_repo.get_by_filename(project_id, backing_filename)
            if source_row is not None:
                source_id = source_row["id"]
        artefacts_repo.upsert(art_id, project_id, art.get("name", art_id), source_id=source_id)

    chat_history = _read_json(project_dir / "chat_history.json", {})
    for chat_id, session in chat_history.items():
        if chat_repo.get_session(chat_id) is not None:
            continue  # already migrated on a prior run -- skip, don't re-append messages
        chat_repo.create_session(chat_id, project_id, session.get("name", chat_id))
        for message in session.get("messages", []):
            chat_repo.append_message(
                chat_id,
                message.get("role", "user"),
                message.get("content", ""),
                artefact_id=message.get("artifact_id"),
            )

    research_runs = _read_json(project_dir / "research_runs.json", {})
    for run_id, run in research_runs.items():
        if research_runs_repo.get(run_id) is not None:
            continue
        research_runs_repo.create(
            run_id,
            project_id,
            run.get("response_id"),
            run.get("chat_id"),
            run.get("prompt_preview", ""),
            status=run.get("status", "completed"),
        )
        research_runs_repo.update(
            run_id,
            artefact_id=run.get("artifact_id"),
            error=run.get("error"),
            completed_at=run.get("completed_at"),
        )

    excluded = _read_json(project_dir / "excluded_competitors.json", [])
    for name in excluded:
        excluded_competitors_repo.add(project_id, name)

    return project_id, stable_id_to_source_id


def _migrate_insights_version(project_id, stable_id_to_source_id, version_number, version_data):
    generated_at = version_data.get("generated_at", "")
    competitors_json = json.dumps(version_data.get("competitors", []))
    competitor_md = version_data.get("competitor_landscape_markdown", "")
    version_id = research_tasks_repo.create_version(
        project_id, version_number, generated_at, competitors_json, competitor_md
    )

    for phase_key, phase in (version_data.get("phases") or {}).items():
        if phase_key not in PHASE_DEFINITIONS:
            continue
        summary = phase.get("summary", "MISSING")
        if summary == "MISSING":
            continue
        task_id = research_tasks_repo.get_or_create_task(
            project_id, phase_key, phase.get("title") or PHASE_DEFINITIONS[phase_key]["title"]
        )
        gaps_notes = "\n".join(phase.get("gaps") or []) or None
        finding_id = research_tasks_repo.create_finding(
            task_id, version_id, summary, gaps_notes, phase.get("confidence")
        )
        for raw_text in phase.get("evidence_sources") or []:
            evidence_id = research_tasks_repo.create_evidence(project_id, raw_text)
            research_tasks_repo.link_finding_evidence(finding_id, evidence_id)
        for stable_file_id in phase.get("linked_file_ids") or []:
            source_id = stable_id_to_source_id.get(stable_file_id)
            if source_id is None:
                continue
            evidence_id = research_tasks_repo.create_evidence(project_id, None, source_id=source_id)
            research_tasks_repo.link_finding_evidence(finding_id, evidence_id)

    for phase_key, phase in (version_data.get("phases") or {}).items():
        for topic in phase.get("suggested_topics") or []:
            research_tasks_repo.create_ad_hoc_task(project_id, topic)

    return 1 if any(
        phase.get("summary", "MISSING") != "MISSING"
        for phase in (version_data.get("phases") or {}).values()
    ) else 0


def _migrate_insights(project_dir, project_id, stable_id_to_source_id):
    if research_tasks_repo.get_latest_version(project_id) is not None:
        return 0  # already migrated on a prior run

    history = _read_json(project_dir / "insights_history.json", [])
    task_count = 0
    seen_versions = set()
    for entry in history:
        version_number = entry.get("version")
        if version_number in seen_versions:
            continue
        seen_versions.add(version_number)
        task_count += _migrate_insights_version(
            project_id, stable_id_to_source_id, version_number, entry.get("data", {})
        )

    current = _read_json(project_dir / "insights.json", None)
    if current is not None:
        last_entry_data = history[-1]["data"] if history else None
        if current != last_entry_data:
            next_version = research_tasks_repo.next_version_number(project_id)
            task_count += _migrate_insights_version(
                project_id, stable_id_to_source_id, next_version, current
            )

    return task_count


def migrate_all_projects(projects_root="projects"):
    root = Path(projects_root)
    report = {}
    if not root.exists():
        return report

    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        project_name = project_dir.name
        project_id, stable_id_to_source_id = _migrate_project_core(project_dir, project_name)
        research_tasks_migrated = _migrate_insights(project_dir, project_id, stable_id_to_source_id)

        report[project_name] = {
            "artefacts": len(artefacts_repo.list_for_project(project_id)),
            "research_runs": len(research_runs_repo.list_for_project(project_id)),
            "research_tasks": research_tasks_migrated,
        }

    return report


if __name__ == "__main__":
    from db.migrate_runner import apply_migrations

    apply_migrations()
    result = migrate_all_projects()
    for project_name, counts in result.items():
        print(f"{project_name}: {counts}")
    print(f"Migrated {len(result)} project(s).")
    sys.exit(0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_migration_script.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/migrate.py tests/test_migration_script.py
git commit -m "feat(db): add one-shot JSON-to-SQLite migration script"
```

---

## Task 12: Cut over `project_service.py`

**Files:**
- Modify: `services/project_service.py`
- Test: existing test suite has no dedicated `test_project_service.py` today — add one covering the behavior change.

**Interfaces:**
- Consumes: `projects_repo`, `sources_repo` (Tasks 2-3).
- Produces: identical public signatures — `list_projects()`, `get_project_dir(name)`, `get_project_path(name, *parts)`, `project_exists(name)`, `create_project(name)`, `load_project_config(name)`, `save_project_config(name, config)`, `get_project_metadata(name)`, `save_project_metadata(name, metadata)`, `list_projects_with_metadata()`, `load_project_prompt`/`save_project_prompt` (unchanged, still use `storage.read_text`/`write_text`).

**Behavior change (intentional, consequence of the spec):** `list_projects()`/`project_exists()`/`list_projects_with_metadata()` now read from the `projects` table instead of scanning `projects/` directories. `get_project_dir`/`get_project_path` (path-traversal guards, used by every other service to resolve `files/`) are unchanged — they still resolve real filesystem paths, since `files/`/`outputs/` stay on disk.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_project_service.py
from db.repositories import projects_repo
import services.project_service as project_service


def test_create_project_registers_db_row_and_creates_dirs(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert project_service.create_project("New Proj") is True
    assert projects_repo.exists("New Proj") is True
    assert (tmp_path / "projects" / "New Proj" / "files").is_dir()
    assert (tmp_path / "projects" / "New Proj" / "outputs").is_dir()


def test_create_project_returns_false_if_already_exists(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_service.create_project("Dup")
    assert project_service.create_project("Dup") is False


def test_list_projects_reflects_db_not_disk(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_service.create_project("Alpha")
    project_service.create_project("Beta")
    assert project_service.list_projects() == ["Alpha", "Beta"]


def test_metadata_round_trip(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_service.create_project("P")
    project_service.save_project_metadata("P", {"description": "desc", "archived": True})
    assert project_service.get_project_metadata("P") == {"description": "desc", "archived": True}


def test_config_round_trip_selected_files(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_service.create_project("P")
    config = project_service.load_project_config("P")
    assert config["selected_files"] == []
    assert config["selected_file_ids"] == []
    assert config["created"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_project_service.py -v`
Expected: FAIL — `create_project` still writes `config.json`/no DB row, `list_projects` still scans disk

- [ ] **Step 3: Implement**

```python
# services/project_service.py — replace the JSON-backed functions; keep path helpers and prompt functions unchanged
from datetime import datetime
from pathlib import Path

from .storage import read_text, write_text
from db.repositories import projects_repo, sources_repo

_INVALID_PATH_CHARS = set('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def get_projects_dir():
    projects_dir = Path("projects")
    projects_dir.mkdir(exist_ok=True)
    return projects_dir


def _contains_control_chars(value):
    return any(ord(ch) < 32 for ch in value)


def _is_reserved_windows_name(value):
    stem = value.split(".", 1)[0].rstrip(" .").upper()
    return stem in _WINDOWS_RESERVED_NAMES


def _normalize_path_component(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized in {".", ".."}:
        return None
    if any(ch in _INVALID_PATH_CHARS for ch in normalized):
        return None
    if "\x00" in normalized or _contains_control_chars(normalized):
        return None
    if normalized.endswith((" ", ".")):
        return None
    if _is_reserved_windows_name(normalized):
        return None
    return normalized


def normalize_project_name(project_name):
    return _normalize_path_component(project_name)


def normalize_filename(filename):
    return _normalize_path_component(filename)


def is_valid_project_name(project_name):
    return normalize_project_name(project_name) is not None


def list_projects():
    return [p["name"] for p in projects_repo.list_all()]


def get_project_dir(project_name):
    normalized = normalize_project_name(project_name)
    if normalized is None:
        return None
    projects_dir = get_projects_dir()
    projects_dir.mkdir(parents=True, exist_ok=True)
    projects_dir = projects_dir.resolve()
    project_dir = (projects_dir / normalized).resolve()
    try:
        project_dir.relative_to(projects_dir)
    except ValueError:
        return None
    return project_dir


def get_project_path(project_name, *parts):
    project_dir = get_project_dir(project_name)
    if project_dir is None:
        return None
    target_path = project_dir.joinpath(*parts).resolve()
    try:
        target_path.relative_to(project_dir)
    except ValueError:
        return None
    return target_path


def get_project_files_dir(project_name):
    return get_project_path(project_name, "files")


def get_project_file_path(project_name, filename):
    normalized = normalize_filename(filename)
    files_dir = get_project_files_dir(project_name)
    if normalized is None or files_dir is None:
        return None
    file_path = (files_dir / normalized).resolve()
    try:
        file_path.relative_to(files_dir)
    except ValueError:
        return None
    return file_path


def project_exists(project_name):
    normalized = normalize_project_name(project_name)
    return bool(normalized and projects_repo.exists(normalized))


def create_project(project_name):
    normalized = normalize_project_name(project_name)
    if normalized is None:
        raise ValueError("Invalid project name.")
    if projects_repo.exists(normalized):
        return False

    project_dir = get_project_dir(normalized)
    if project_dir is None:
        raise ValueError("Invalid project name.")
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(exist_ok=True)
    (project_dir / "files").mkdir(exist_ok=True)
    (project_dir / "outputs").mkdir(exist_ok=True)

    projects_repo.get_or_create_id(normalized)
    return True


def load_project_config(project_name):
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    selected_ids = sources_repo.list_selected_ids(project_id)
    sources_by_id = {s["id"]: s for s in sources_repo.list_for_project(project_id)}
    selected_files = [sources_by_id[sid]["filename"] for sid in selected_ids if sid in sources_by_id]
    selected_file_ids = [
        sources_by_id[sid]["stable_file_id"] for sid in selected_ids if sid in sources_by_id
    ]
    return {
        "name": project_name,
        "created": projects_repo.get_created_at(project_name),
        "selected_files": selected_files,
        "selected_file_ids": selected_file_ids,
    }


def save_project_config(project_name, config):
    project_id = projects_repo.get_or_create_id(project_name)
    target_stable_ids = set(config.get("selected_file_ids", []))
    current_selected_ids = set(sources_repo.list_selected_ids(project_id))
    sources_by_stable_id = {
        s["stable_file_id"]: s for s in sources_repo.list_for_project(project_id)
    }
    target_ids = {
        sources_by_stable_id[sid]["id"]
        for sid in target_stable_ids
        if sid in sources_by_stable_id
    }
    for source_id in target_ids - current_selected_ids:
        sources_repo.set_selected(project_id, source_id, True)
    for source_id in current_selected_ids - target_ids:
        sources_repo.set_selected(project_id, source_id, False)


def get_project_metadata(project_name):
    return projects_repo.get_metadata(project_name)


def save_project_metadata(project_name, metadata):
    projects_repo.save_metadata(
        project_name, metadata.get("description", ""), bool(metadata.get("archived", False))
    )


def list_projects_with_metadata():
    return [
        {"name": p["name"], "description": p["description"], "archived": p["archived"]}
        for p in projects_repo.list_all()
    ]


def load_project_prompt(project_name, prompt_type):
    prompt_file = get_project_path(project_name, f"{prompt_type}.txt")
    if prompt_file is None:
        return ""
    return read_text(prompt_file, "")


def save_project_prompt(project_name, prompt_type, content):
    prompt_file = get_project_path(project_name, f"{prompt_type}.txt")
    if prompt_file is None:
        raise ValueError("Invalid project name.")
    write_text(prompt_file, content if isinstance(content, str) else str(content or ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_project_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/project_service.py tests/test_project_service.py
git commit -m "refactor(project_service): back config/metadata with SQLite instead of JSON files"
```

---

## Task 13: Cut over `file_index_service.py`

**Files:**
- Modify: `services/file_index_service.py`
- Modify: `tests/test_file_index_service.py` (existing tests patch `get_project_dir` and inspect `file_index.json` on disk directly — these need to patch the repo layer instead)

**Interfaces:** identical public signatures — `reconcile_file_index`, `get_file_id`, `ensure_file_id`, `rename_file_in_index`, `remove_file_from_index`, `resolve_file_refs_to_names`, `resolve_ids_to_names`, `resolve_names_to_ids`, `reconcile_selected_file_ids`, `toggle_selected_file`. **Simplification note:** with `project_selected_sources` as the single source of truth (ID-based), the old `_build_selected_state`'s "prefer IDs, fall back to names" reconciliation dance is no longer needed — selection state is always ID-based now.

- [ ] **Step 1: Update the failing tests**

```python
# tests/test_file_index_service.py — replace the "Integration tests with file system" section
# (keep the pure-helper tests for _dedupe/_is_hidden_source_file/_normalize_index_data/_is_internal_temp_file unchanged)

from unittest.mock import patch

from db.repositories import projects_repo, sources_repo
from services.file_index_service import (
    ensure_file_id,
    get_file_id,
    reconcile_file_index,
    remove_file_from_index,
    rename_file_in_index,
    resolve_file_refs_to_names,
    toggle_selected_file,
)


def _setup_project_dir(tmp_path, project_name, files=None):
    project_dir = tmp_path / "projects" / project_name
    files_dir = project_dir / "files"
    files_dir.mkdir(parents=True)
    for filename in (files or []):
        (files_dir / filename).write_text("content", encoding="utf-8")
    return project_dir


@patch("services.file_index_service.get_project_dir")
def test_reconcile_registers_new_files_as_sources(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["a.txt", "b.txt"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    filenames = {rec["filename"] for rec in entries.values()}
    assert filenames == {"a.txt", "b.txt"}
    assert all(fid.startswith("f_") for fid in entries)


@patch("services.file_index_service.get_project_dir")
def test_reconcile_is_stable_across_calls(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["a.txt"])
    mock_dir.return_value = project_dir

    first = reconcile_file_index("proj")
    second = reconcile_file_index("proj")
    assert first == second


@patch("services.file_index_service.get_project_dir")
def test_reconcile_excludes_temp_files(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["real.txt"])
    (project_dir / "files" / ".write_abc.tmp").write_text("temp", encoding="utf-8")
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    filenames = {rec["filename"] for rec in entries.values()}
    assert ".write_abc.tmp" not in filenames


@patch("services.file_index_service.get_project_dir")
def test_get_file_id_returns_correct_id(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["doc.pdf"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "doc.pdf", entries)
    assert file_id in entries


@patch("services.file_index_service.get_project_dir")
def test_rename_preserves_id(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["old.txt"])
    mock_dir.return_value = project_dir
    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "old.txt", entries)

    (project_dir / "files" / "old.txt").rename(project_dir / "files" / "new.txt")
    rename_file_in_index("proj", "old.txt", "new.txt")

    entries_after = reconcile_file_index("proj")
    assert entries_after[file_id]["filename"] == "new.txt"


@patch("services.file_index_service.get_project_dir")
def test_toggle_selected_file(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["a.txt"])
    mock_dir.return_value = project_dir
    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "a.txt", entries)

    assert toggle_selected_file("proj", filename="a.txt") is True
    state = reconcile_selected_file_ids_for_test("proj")
    assert file_id in state
    assert toggle_selected_file("proj", filename="a.txt") is False


def reconcile_selected_file_ids_for_test(project_name):
    from services.file_index_service import reconcile_selected_file_ids
    return reconcile_selected_file_ids(project_name)["selected_file_ids"]


@patch("services.file_index_service.get_project_dir")
def test_resolve_file_refs_to_names_excludes_hidden(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["a.txt"])
    mock_dir.return_value = project_dir
    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "a.txt", entries)

    names = resolve_file_refs_to_names("proj", [file_id, "insights.json"])
    assert names == ["a.txt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_file_index_service.py -v`
Expected: FAIL — functions still read/write `file_index.json` on disk, `toggle_selected_file` still touches `config.json`

- [ ] **Step 3: Implement**

```python
# services/file_index_service.py
from .project_service import get_project_dir
from db.repositories import projects_repo, sources_repo

HIDDEN_SOURCE_FILES = {"insights.json", "insights_history.json", "excluded_competitors.json"}


def _is_internal_temp_file(filename):
    return isinstance(filename, str) and filename.startswith(".write_") and filename.endswith(".tmp")


def _is_hidden_source_file(filename):
    return isinstance(filename, str) and filename in HIDDEN_SOURCE_FILES


def _dedupe(items):
    out = []
    seen = set()
    for item in items or []:
        if not isinstance(item, str):
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _normalize_index_data(raw):
    # Retained for any remaining callers that pass raw legacy JSON through;
    # reconcile_file_index itself no longer reads this shape from disk.
    if not isinstance(raw, dict):
        return {}
    files = raw.get("files")
    if not isinstance(files, dict):
        return {}
    entries = {}
    for file_id, rec in files.items():
        if not isinstance(file_id, str) or not file_id:
            continue
        if isinstance(rec, str):
            entries[file_id] = {"filename": rec, "created_at": None, "updated_at": None}
            continue
        if not isinstance(rec, dict):
            continue
        filename = rec.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        entries[file_id] = {
            "filename": filename,
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
        }
    return entries


def _existing_files(project_name):
    project_dir = get_project_dir(project_name)
    if project_dir is None:
        return set()
    files_dir = project_dir / "files"
    if not files_dir.exists():
        return set()
    return {
        p.name
        for p in files_dir.iterdir()
        if p.is_file() and not _is_internal_temp_file(p.name)
    }


def _entries_for_project(project_id):
    return {
        s["stable_file_id"]: {
            "filename": s["filename"], "created_at": s["created_at"], "updated_at": s["updated_at"]
        }
        for s in sources_repo.list_for_project(project_id)
    }


def reconcile_file_index(project_name):
    if not project_name:
        return {}
    project_id = projects_repo.get_or_create_id(project_name)
    existing = _existing_files(project_name)
    entries = _entries_for_project(project_id)

    indexed_names = {rec["filename"] for rec in entries.values()}
    for filename in sorted(existing):
        if filename in indexed_names:
            continue
        import secrets
        stable_file_id = f"f_{secrets.token_hex(8)}"
        sources_repo.upsert(project_id, stable_file_id, filename)

    return _entries_for_project(project_id)


def _name_to_id(entries):
    return {rec["filename"]: file_id for file_id, rec in entries.items()}


def _id_to_name(entries):
    return {file_id: rec["filename"] for file_id, rec in entries.items()}


def get_file_id(project_name, filename, entries=None):
    if not filename:
        return None
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    return _name_to_id(idx).get(filename)


def ensure_file_id(project_name, filename):
    if not filename:
        return None
    entries = reconcile_file_index(project_name)
    return get_file_id(project_name, filename, entries)


def rename_file_in_index(project_name, old_name, new_name):
    if not old_name or not new_name or old_name == new_name:
        return False
    project_id = projects_repo.get_or_create_id(project_name)
    entries = reconcile_file_index(project_name)
    target_id = get_file_id(project_name, old_name, entries)
    if not target_id:
        ensure_file_id(project_name, new_name)
        return False
    source = sources_repo.get_by_stable_id(project_id, target_id)
    sources_repo.rename(source["id"], new_name)
    return True


def remove_file_from_index(project_name, filename):
    if not filename:
        return None
    project_id = projects_repo.get_or_create_id(project_name)
    entries = reconcile_file_index(project_name)
    target_id = get_file_id(project_name, filename, entries)
    if not target_id:
        return None
    source = sources_repo.get_by_stable_id(project_id, target_id)
    sources_repo.delete(source["id"])
    return target_id


def resolve_file_refs_to_names(project_name, refs, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    id_to_name = _id_to_name(idx)
    valid_names = {name for name in id_to_name.values() if not _is_hidden_source_file(name)}
    out, seen = [], set()
    for ref in refs or []:
        if not isinstance(ref, str):
            continue
        filename = id_to_name.get(ref) if ref in id_to_name else (ref if ref in valid_names else None)
        if filename and not _is_hidden_source_file(filename) and filename not in seen:
            seen.add(filename)
            out.append(filename)
    return out


def resolve_ids_to_names(project_name, file_ids, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    id_to_name = _id_to_name(idx)
    out, seen = [], set()
    for file_id in file_ids or []:
        filename = id_to_name.get(file_id) if isinstance(file_id, str) else None
        if filename and not _is_hidden_source_file(filename) and filename not in seen:
            seen.add(filename)
            out.append(filename)
    return out


def resolve_names_to_ids(project_name, filenames, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    name_to_id = _name_to_id(idx)
    out, seen = [], set()
    for filename in filenames or []:
        if not isinstance(filename, str) or _is_hidden_source_file(filename):
            continue
        file_id = name_to_id.get(filename)
        if file_id and file_id not in seen:
            seen.add(file_id)
            out.append(file_id)
    return out


def reconcile_selected_file_ids(project_name, entries=None):
    if not project_name:
        return {"selected_file_ids": [], "selected_files": [], "file_index": {}}
    project_id = projects_repo.get_or_create_id(project_name)
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    id_to_name = _id_to_name(idx)
    sources_by_id = {s["id"]: s for s in sources_repo.list_for_project(project_id)}
    selected_ids = [
        sources_by_id[sid]["stable_file_id"]
        for sid in sources_repo.list_selected_ids(project_id)
        if sid in sources_by_id and not _is_hidden_source_file(sources_by_id[sid]["filename"])
    ]
    selected_files = [id_to_name[fid] for fid in selected_ids if fid in id_to_name]
    return {"selected_file_ids": selected_ids, "selected_files": selected_files, "file_index": id_to_name}


def toggle_selected_file(project_name, filename=None, file_id=None):
    if not project_name:
        return None
    project_id = projects_repo.get_or_create_id(project_name)
    entries = reconcile_file_index(project_name)
    if file_id and file_id in entries:
        target_stable_id = file_id
    elif filename:
        target_stable_id = get_file_id(project_name, filename, entries)
    else:
        target_stable_id = None
    if not target_stable_id:
        return None

    source = sources_repo.get_by_stable_id(project_id, target_stable_id)
    currently_selected = source["id"] in sources_repo.list_selected_ids(project_id)
    sources_repo.set_selected(project_id, source["id"], not currently_selected)
    return not currently_selected
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_file_index_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/file_index_service.py tests/test_file_index_service.py
git commit -m "refactor(file_index_service): back file index and selection with SQLite"
```

---

## Task 14: Cut over `artifact_service.py`

**Files:**
- Modify: `services/artifact_service.py`

**Interfaces:** `load_artifacts(project_name) -> dict`, `save_artifacts(project_name, artifacts) -> None` — same signatures, but the returned dict shape must stay compatible with real callers: `routes/artifacts.py` and `routes/files.py` read `existing.get("filename")`, `art.get("content")`, `art.get("name")` off these entries directly. **Content and filename are not new DB columns** — per the spec's "files/ stays on disk" decision, an artefact's `content` lives only in its backing `.md` file; `load_artifacts` reconstructs `filename` via the `source_id` FK and reads `content` from that file on disk. (`type` has no column in the schema — it's always reconstructed as `"text"`, a known, accepted fidelity gap: every artefact created through today's `/api/artifacts` route already defaults to `"text"` unless explicitly overridden, which no current call site does.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_service.py
from db.repositories import artefacts_repo, projects_repo, sources_repo
from services.project_service import get_project_file_path
import services.artifact_service as artifact_service


def test_load_artifacts_reconstructs_filename_and_content_from_disk(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pid = projects_repo.get_or_create_id("P")
    files_dir = tmp_path / "projects" / "P" / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "My Artefact.md").write_text("artefact body text", encoding="utf-8")
    sid = sources_repo.upsert(pid, "f_abc", "My Artefact.md")
    artefacts_repo.upsert("art_1", pid, "My Artefact", source_id=sid)

    loaded = artifact_service.load_artifacts("P")
    assert loaded["art_1"]["name"] == "My Artefact"
    assert loaded["art_1"]["filename"] == "My Artefact.md"
    assert loaded["art_1"]["content"] == "artefact body text"
    assert loaded["art_1"]["type"] == "text"


def test_save_artifacts_upserts_each_entry_and_links_source(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pid = projects_repo.get_or_create_id("P")
    sources_repo.upsert(pid, "f_abc", "Doc.md")
    artifact_service.save_artifacts(
        "P", {"art_1": {"id": "art_1", "name": "First", "filename": "Doc.md"}}
    )
    row = artefacts_repo.get("art_1")
    assert row["name"] == "First"
    source = sources_repo.get_by_stable_id(pid, "f_abc")
    assert row["source_id"] == source["id"]


def test_load_artifacts_empty_for_unknown_project(temp_db):
    assert artifact_service.load_artifacts("Nope") == {}


def test_load_artifacts_handles_missing_backing_file_gracefully(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "Orphaned")  # no source_id — legacy/un-migrated artefact
    loaded = artifact_service.load_artifacts("P")
    assert loaded["art_1"]["filename"] is None
    assert loaded["art_1"]["content"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_service.py -v`
Expected: FAIL — still backed by `artifacts.json`

- [ ] **Step 3: Implement**

```python
# services/artifact_service.py
from db.repositories import artefacts_repo, projects_repo, sources_repo
from .project_service import get_project_file_path
from .storage import read_text


def _resolve_filename(project_id, source_id):
    if source_id is None:
        return None
    match = next(
        (s for s in sources_repo.list_for_project(project_id) if s["id"] == source_id), None
    )
    return match["filename"] if match else None


def load_artifacts(project_name):
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    result = {}
    for row in artefacts_repo.list_for_project(project_id):
        filename = _resolve_filename(project_id, row["source_id"])
        content = ""
        if filename:
            path = get_project_file_path(project_name, filename)
            if path is not None:
                content = read_text(path, "")
        result[row["id"]] = {
            "id": row["id"],
            "name": row["name"],
            "filename": filename,
            "content": content,
            "created_at": row["created_at"],
            "type": "text",
        }
    return result


def save_artifacts(project_name, artifacts):
    project_id = projects_repo.get_or_create_id(project_name)
    for art_id, art in (artifacts or {}).items():
        source_id = None
        filename = art.get("filename")
        if filename:
            source_row = sources_repo.get_by_filename(project_id, filename)
            if source_row is not None:
                source_id = source_row["id"]
        artefacts_repo.upsert(art_id, project_id, art.get("name", art_id), source_id=source_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/artifact_service.py tests/test_artifact_service.py
git commit -m "refactor(artifact_service): back artifacts with SQLite"
```

---

## Task 15: Cut over `chat_service.py`

**Files:**
- Modify: `services/chat_service.py`

**Interfaces:** `load_chat_sessions(project_name) -> dict`, `load_chat_sessions_locked(project_name) -> dict` (no longer needs a special locked variant — SQLite's own transaction handling covers the consistent-snapshot need; kept as an alias for call-site compatibility), `save_chat_sessions(project_name, sessions) -> None`, `create_new_chat(project_name) -> str`, `append_message_to_chat(project_name, chat_id, message) -> bool`, plus three **new** functions needed because `routes/chats.py` currently bypasses this service entirely (see Task 18b): `delete_chat(project_name, chat_id) -> bool`, `rename_chat(project_name, chat_id, new_name) -> bool`, `link_artifact_to_message_by_index(project_name, chat_id, index, artifact_id) -> "ok"|"not_found"|"out_of_range"` (index is the 0-based array position `routes/chats.py` already receives from the frontend; `seq` in `project_messages` is 1-based, so this function does the `seq = index + 1` conversion internally).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_service.py
from db.repositories import projects_repo
import services.chat_service as chat_service


def test_create_new_chat_returns_unique_ids(temp_db):
    projects_repo.get_or_create_id("P")
    id1 = chat_service.create_new_chat("P")
    id2 = chat_service.create_new_chat("P")
    assert id1 != id2


def test_append_message_and_load_sessions(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    assert chat_service.append_message_to_chat("P", chat_id, {"role": "user", "content": "hi"}) is True
    sessions = chat_service.load_chat_sessions("P")
    assert sessions[chat_id]["messages"][0]["content"] == "hi"


def test_append_message_to_missing_chat_returns_false(temp_db):
    projects_repo.get_or_create_id("P")
    assert chat_service.append_message_to_chat("P", "nope", {"role": "user", "content": "hi"}) is False


def test_load_chat_sessions_locked_matches_unlocked(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    chat_service.append_message_to_chat("P", chat_id, {"role": "user", "content": "hi"})
    assert chat_service.load_chat_sessions_locked("P") == chat_service.load_chat_sessions("P")


def test_delete_chat(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    assert chat_service.delete_chat("P", chat_id) is True
    assert chat_id not in chat_service.load_chat_sessions("P")


def test_delete_chat_missing_returns_false(temp_db):
    projects_repo.get_or_create_id("P")
    assert chat_service.delete_chat("P", "nope") is False


def test_rename_chat(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    assert chat_service.rename_chat("P", chat_id, "New Name") is True
    assert chat_service.load_chat_sessions("P")[chat_id]["name"] == "New Name"


def test_link_artifact_to_message_by_index(temp_db):
    from db.repositories import artefacts_repo
    projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", projects_repo.get_id("P"), "Artefact")
    chat_id = chat_service.create_new_chat("P")
    chat_service.append_message_to_chat("P", chat_id, {"role": "user", "content": "first"})
    chat_service.append_message_to_chat("P", chat_id, {"role": "assistant", "content": "second"})

    assert chat_service.link_artifact_to_message_by_index("P", chat_id, 1, "art_1") == "ok"
    messages = chat_service.load_chat_sessions("P")[chat_id]["messages"]
    assert messages[1]["artifact_id"] == "art_1"


def test_link_artifact_to_message_out_of_range(temp_db):
    projects_repo.get_or_create_id("P")
    chat_id = chat_service.create_new_chat("P")
    chat_service.append_message_to_chat("P", chat_id, {"role": "user", "content": "only one"})
    assert chat_service.link_artifact_to_message_by_index("P", chat_id, 5, "art_1") == "out_of_range"


def test_link_artifact_to_message_chat_not_found(temp_db):
    projects_repo.get_or_create_id("P")
    assert chat_service.link_artifact_to_message_by_index("P", "nope", 0, "art_1") == "not_found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chat_service.py -v`
Expected: FAIL — still backed by `chat_history.json`

- [ ] **Step 3: Implement**

```python
# services/chat_service.py
import secrets
from datetime import datetime

from db.repositories import chat_repo, projects_repo


def _session_to_dict(session_row, messages):
    return {
        "name": session_row["name"],
        "created": session_row["created_at"],
        "messages": [
            {"role": m["role"], "content": m["content"], "artifact_id": m["artefact_id"]}
            for m in messages
        ],
    }


def load_chat_sessions(project_name):
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    sessions = {}
    for session_row in chat_repo.list_sessions_for_project(project_id):
        messages = chat_repo.list_messages(session_row["id"])
        sessions[session_row["id"]] = _session_to_dict(session_row, messages)
    return sessions


def load_chat_sessions_locked(project_name):
    """SQLite transactions already give a consistent read; kept for call-site compatibility."""
    return load_chat_sessions(project_name)


def save_chat_sessions(project_name, sessions):
    # No longer used for bulk writes now that each mutation (create/append) writes
    # directly through chat_repo; kept only so any lingering caller doesn't crash.
    pass


def create_new_chat(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    chat_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    while chat_repo.get_session(chat_id) is not None:
        chat_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    chat_repo.create_session(chat_id, project_id, f"Research {datetime.now().strftime('%b %d, %H:%M:%S')}")
    return chat_id


def append_message_to_chat(project_name, chat_id, message):
    if chat_repo.get_session(chat_id) is None:
        return False
    chat_repo.append_message(
        chat_id, message.get("role", "user"), message.get("content", ""),
        artefact_id=message.get("artifact_id"),
    )
    return True


def delete_chat(project_name, chat_id):
    if chat_repo.get_session(chat_id) is None:
        return False
    chat_repo.delete_session(chat_id)
    return True


def rename_chat(project_name, chat_id, new_name):
    if chat_repo.get_session(chat_id) is None:
        return False
    chat_repo.rename_session(chat_id, new_name)
    return True


def link_artifact_to_message_by_index(project_name, chat_id, index, artifact_id):
    if chat_repo.get_session(chat_id) is None:
        return "not_found"
    seq = index + 1
    messages = chat_repo.list_messages(chat_id)
    if index < 0 or index >= len(messages):
        return "out_of_range"
    chat_repo.link_artefact_to_message(chat_id, seq, artifact_id)
    return "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chat_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/chat_service.py tests/test_chat_service.py
git commit -m "refactor(chat_service): back chat sessions and messages with SQLite"
```

---

## Task 16: Cut over `research_run_service.py`

**Files:**
- Modify: `services/research_run_service.py`

**Interfaces:** `create_run(project_name, response_id, chat_id, prompt_text) -> run_id`, `update_run(project_name, run_id, **fields) -> bool`, `complete_run(project_name, run_id, artifact_id=None) -> bool`, `fail_run(project_name, run_id, error_message) -> bool`, `cancel_run(project_name, run_id) -> bool`, `is_duplicate_run(project_name, prompt_text, debounce_seconds=30) -> bool`, `load_runs(project_name) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_research_run_service.py
from db.repositories import projects_repo
import services.research_run_service as research_run_service


def test_create_and_load_runs(temp_db):
    projects_repo.get_or_create_id("P")
    run_id = research_run_service.create_run("P", "resp_1", "chat_1", "a prompt")
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "running"


def test_complete_run(temp_db):
    projects_repo.get_or_create_id("P")
    run_id = research_run_service.create_run("P", "resp_1", "chat_1", "a prompt")
    assert research_run_service.complete_run("P", run_id, artifact_id="art_1") is True
    runs = research_run_service.load_runs("P")
    assert runs[run_id]["status"] == "completed"
    assert runs[run_id]["artifact_id"] == "art_1"


def test_is_duplicate_run_detects_recent_identical_prompt(temp_db):
    projects_repo.get_or_create_id("P")
    research_run_service.create_run("P", "resp_1", "chat_1", "same prompt")
    assert research_run_service.is_duplicate_run("P", "same prompt") is True
    assert research_run_service.is_duplicate_run("P", "different prompt") is False


def test_is_duplicate_run_ignores_completed(temp_db):
    projects_repo.get_or_create_id("P")
    run_id = research_run_service.create_run("P", "resp_1", "chat_1", "same prompt")
    research_run_service.complete_run("P", run_id)
    assert research_run_service.is_duplicate_run("P", "same prompt") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_research_run_service.py -v`
Expected: FAIL — still backed by `research_runs.json`

- [ ] **Step 3: Implement**

```python
# services/research_run_service.py
import secrets
from datetime import datetime, timedelta

from db.repositories import projects_repo, research_runs_repo


def create_run(project_name, response_id, chat_id, prompt_text):
    project_id = projects_repo.get_or_create_id(project_name)
    prompt_preview = (prompt_text[:200] + "...") if len(prompt_text) > 200 else prompt_text
    run_id = f"run_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}"
    while research_runs_repo.get(run_id) is not None:
        run_id = f"run_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}"
    research_runs_repo.create(run_id, project_id, response_id, chat_id, prompt_preview)
    return run_id


def load_runs(project_name):
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    return {
        row["id"]: {
            "id": row["id"], "project": project_name, "response_id": row["response_id"],
            "chat_id": row["chat_session_id"], "prompt_preview": row["prompt_preview"],
            "status": row["status"], "artifact_id": row["artefact_id"],
            "created_at": row["created_at"], "completed_at": row["completed_at"],
            "error": row["error"], "updated_at": row["updated_at"],
        }
        for row in research_runs_repo.list_for_project(project_id)
    }


def update_run(project_name, run_id, **fields):
    column_map = {"chat_id": "chat_session_id", "artifact_id": "artefact_id"}
    mapped = {column_map.get(k, k): v for k, v in fields.items()}
    return research_runs_repo.update(run_id, **mapped)


def complete_run(project_name, run_id, artifact_id=None):
    return update_run(
        project_name, run_id, status="completed", artifact_id=artifact_id,
        completed_at=datetime.now().isoformat(),
    )


def fail_run(project_name, run_id, error_message):
    return update_run(
        project_name, run_id, status="failed", error=error_message,
        completed_at=datetime.now().isoformat(),
    )


def cancel_run(project_name, run_id):
    return update_run(project_name, run_id, status="cancelled", completed_at=datetime.now().isoformat())


def is_duplicate_run(project_name, prompt_text, debounce_seconds=30):
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return False
    preview = (prompt_text[:200] + "...") if len(prompt_text) > 200 else prompt_text
    cutoff = (datetime.now() - timedelta(seconds=debounce_seconds)).isoformat()
    return research_runs_repo.find_recent_running_or_queued_with_preview(project_id, preview, cutoff) is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_research_run_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/research_run_service.py tests/test_research_run_service.py
git commit -m "refactor(research_run_service): back research runs with SQLite"
```

---

## Task 17: Cut over `reference_integrity_service.py`

**Files:**
- Modify: `services/reference_integrity_service.py`
- Modify: `tests/test_reference_integrity_service.py`

**Interfaces:** `reconcile_project_references(project_name) -> {"changed": bool, "selected_files": list}`, `replace_file_references(project_name, old_name, new_name) -> {"changed": bool}`, `remove_file_references(project_name, filename, file_id=None) -> {"changed": bool}`.

**Behavior simplification (per spec §11/Review Focus):** since `project_selected_sources` and `finding_evidence` both key off the stable `source_id` (not filename), a **rename no longer needs any reference-pruning work at all** — the source row is renamed in place (Task 13), and every table pointing at `source_id` keeps working automatically. `replace_file_references` becomes a no-op that returns `{"changed": False}`. `remove_file_references` still has real work: unselecting the deleted source and unlinking it from the *current* insights version only (per the Review Focus item on this exact scenario).

**Known accepted side effect of this simplification:** `routes/files.py`'s `rename_file()` has a loop (lines ~101-112 in the pre-migration file) that patches any artifact whose `filename` field matched the old name, updating both `filename` and a derived `name` (stem). Because `artifact_service.load_artifacts` (Task 14) now derives `filename` dynamically from the renamed `sources` row, that loop's `art.get("filename") == old_name` check never matches post-migration — it becomes inert, not broken. The filename side of this (artifact always shows the current filename) is now handled automatically and *more* correctly than before. The one real behavior change: an artifact's **display name** no longer auto-updates to match a new filename stem when the backing file is renamed via the file browser (as opposed to renamed through the artifact UI itself, which still updates the name explicitly). This is a minor, accepted regression — not fixed here, since fixing it would mean modifying `rename_file()` for a reason unrelated to this plan's scope; flagged so a reviewer doesn't mistake the now-dead loop for a bug introduced by this migration.

- [ ] **Step 1: Update the failing tests**

```python
# tests/test_reference_integrity_service.py — replace file-based tests with repo-based ones
from db.repositories import projects_repo, research_tasks_repo, sources_repo
from services import insights_service
from services.reference_integrity_service import (
    remove_file_references,
    replace_file_references,
)


def _sample_data_linked_to(stable_file_id):
    data = {"generated_at": "2026-01-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "", "phases": {}}
    for key in [str(i) for i in range(1, 8)]:
        data["phases"][key] = {
            "title": f"Phase {key}", "summary": "A summary" if key == "1" else "MISSING",
            "confidence": "medium" if key == "1" else "none", "evidence_sources": [],
            "gaps": [], "suggested_topics": [],
            "linked_files": [], "linked_file_ids": [stable_file_id] if key == "1" else [],
        }
    return data


def test_replace_file_references_is_a_no_op_now(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sources_repo.upsert(pid, "f_abc", "old.txt")
    result = replace_file_references("P", "old.txt", "new.txt")
    assert result == {"changed": False}


def test_remove_file_references_unselects_source(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "doc.txt")
    sources_repo.set_selected(pid, sid, True)
    remove_file_references("P", "doc.txt", file_id="f_abc")
    assert sources_repo.list_selected_ids(pid) == []


def test_remove_file_references_unlinks_from_current_version_only(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sources_repo.upsert(pid, "f_abc", "doc.txt")
    insights_service.save_insights("P", _sample_data_linked_to("f_abc"))

    before = insights_service.load_current_insights("P")
    assert before["phases"]["1"]["linked_file_ids"] == ["f_abc"]

    remove_file_references("P", "doc.txt", file_id="f_abc")

    after = insights_service.load_current_insights("P")
    assert after["phases"]["1"]["linked_file_ids"] == []

    # Historical version is untouched
    history = insights_service.load_insights_history("P")
    assert history[0]["data"]["phases"]["1"]["linked_file_ids"] == ["f_abc"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reference_integrity_service.py -v`
Expected: FAIL — still reads/writes raw `files/insights.json` and `config.json`

- [ ] **Step 3: Implement**

```python
# services/reference_integrity_service.py
from db.repositories import projects_repo, research_tasks_repo, sources_repo

HIDDEN_SOURCE_FILES = {"insights.json", "insights_history.json", "excluded_competitors.json"}


def reconcile_project_references(project_name):
    """File-index reconciliation already self-heals selection state on every
    reconcile_file_index() call (Task 13); nothing additional to prune here
    now that references are ID-based. Kept for call-site compatibility.
    """
    if not project_name:
        return {"changed": False, "selected_files": []}
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {"changed": False, "selected_files": []}
    sources_by_id = {s["id"]: s for s in sources_repo.list_for_project(project_id)}
    selected_files = [
        sources_by_id[sid]["filename"]
        for sid in sources_repo.list_selected_ids(project_id)
        if sid in sources_by_id
    ]
    return {"changed": False, "selected_files": selected_files}


def replace_file_references(project_name, old_name, new_name):
    """A no-op: project_selected_sources and finding_evidence both key off the
    stable source_id, not filename, so a rename (Task 13's sources_repo.rename)
    is already reflected everywhere automatically.
    """
    return {"changed": False}


def remove_file_references(project_name, filename, file_id=None):
    if not project_name or not filename:
        return {"changed": False}
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {"changed": False}

    source = sources_repo.get_by_filename(project_id, filename)
    if source is None:
        return {"changed": False}

    changed = False
    if source["id"] in sources_repo.list_selected_ids(project_id):
        sources_repo.set_selected(project_id, source["id"], False)
        changed = True

    latest_version = research_tasks_repo.get_latest_version(project_id)
    if latest_version is not None:
        research_tasks_repo.unlink_evidence_by_source_in_version(latest_version["id"], source["id"])
        changed = True

    return {"changed": changed}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reference_integrity_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/reference_integrity_service.py tests/test_reference_integrity_service.py
git commit -m "refactor(reference_integrity_service): simplify pruning now references are ID-based"
```

---

## Task 18: Wire `routes/files.py` and `routes/projects.py` for the three reserved filenames

**Files:**
- Modify: `routes/files.py`, `routes/projects.py`
- Test: `tests/test_routes_insights_wiring.py`

**Interfaces:** No signature changes to any route — same URL, same request/response JSON shape. Internally, `POST /api/files` checks `filename` before falling through to `file_service.save_project_file`; `GET /api/projects/<name>` checks for the three filenames when assembling its `files` dict.

- [ ] **Step 1: Write the failing test**

Since `routes/files.py` and `routes/projects.py` weren't fully read line-by-line in this plan's research (only summarized), this step uses Flask's test client against the real app rather than reaching into exact line numbers — find the `POST /api/files` handler's filename branch point and the `GET /api/projects/<name>` handler's file-assembly loop by inspection before editing, per Step 3.

```python
# tests/test_routes_insights_wiring.py
import json

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


def _create_project(client, name):
    client.post("/api/projects", json={"name": name})


def test_posting_insights_json_does_not_create_a_disk_file(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _create_project(client, "P")
    payload = {"generated_at": "2026-01-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
               "phases": {str(i): {"title": f"Phase {i}", "summary": "MISSING", "confidence": "none",
                                     "evidence_sources": [], "gaps": [], "suggested_topics": [],
                                     "linked_files": [], "linked_file_ids": []} for i in range(1, 8)}}
    resp = client.post("/api/files", json={
        "project": "P", "filename": "insights.json", "content": json.dumps(payload)
    })
    assert resp.status_code == 200
    assert not (tmp_path / "projects" / "P" / "files" / "insights.json").exists()


def test_get_project_returns_insights_json_content_from_db(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _create_project(client, "P")
    payload = {"generated_at": "2026-01-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
               "phases": {str(i): {"title": f"Phase {i}", "summary": "A summary" if i == 1 else "MISSING",
                                     "confidence": "none", "evidence_sources": [], "gaps": [],
                                     "suggested_topics": [], "linked_files": [], "linked_file_ids": []}
                          for i in range(1, 8)}}
    client.post("/api/files", json={"project": "P", "filename": "insights.json", "content": json.dumps(payload)})

    resp = client.get("/api/projects/P")
    data = resp.get_json()
    insights = json.loads(data["files"]["insights.json"])
    assert insights["phases"]["1"]["summary"] == "A summary"


def test_other_filenames_still_write_to_disk(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _create_project(client, "P")
    resp = client.post("/api/files", json={"project": "P", "filename": "notes.txt", "content": "hello"})
    assert resp.status_code == 200
    assert (tmp_path / "projects" / "P" / "files" / "notes.txt").read_text(encoding="utf-8") == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_routes_insights_wiring.py -v`
Expected: FAIL — `insights.json` still lands on disk and is not reconstructed from the DB

- [ ] **Step 3: Implement**

`routes/files.py`'s `POST /api/files` view (`save_file()`) has two branches: a multipart file-upload branch (`"file" in request.files`) and a JSON-body branch (`data is not None`). The three reserved filenames are only ever sent via the JSON-body branch (app.js's `saveInsightsToFile`-style calls always POST `{project, filename, content}`), so only that branch needs the special-case, inserted before the existing `save_project_file`/`ensure_file_id` calls:

```python
# routes/files.py — add this import at the top, alongside the existing service imports
from services.insights_service import (
    save_excluded_competitors,
    save_insights,
)
```

```python
# routes/files.py — replace the "if data is not None:" branch inside save_file()
    if data is not None:
        filename = data.get("filename")
        content = data.get("content")

        if filename == "insights.json":
            import json
            save_insights(project, json.loads(content))
            return jsonify({"success": True})
        if filename == "insights_history.json":
            # History is derived from saved versions, not written directly;
            # accept and no-op so any caller still POSTing it doesn't error.
            return jsonify({"success": True})
        if filename == "excluded_competitors.json":
            import json
            save_excluded_competitors(project, json.loads(content))
            return jsonify({"success": True})

        try:
            save_project_file(project, filename, content)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        ensure_file_id(project, filename)
        return jsonify({"success": True})
```

`routes/projects.py`'s `GET /api/projects/<project_name>` view (`get_project()`) builds its response's `files` dict from `load_project_files(project_name)` at line 87. Inject the three reconstructed blobs right after that line, overwriting any stale on-disk copy that might exist from before migration:

```python
# routes/projects.py — add this import at the top, alongside the existing service imports
from services.insights_service import (
    load_current_insights,
    load_excluded_competitors,
    load_insights_history,
)
```

```python
# routes/projects.py — inside get_project(), replace this line:
    files = load_project_files(project_name)

# with:
    import json
    files = load_project_files(project_name)
    files["insights.json"] = json.dumps(load_current_insights(project_name))
    files["insights_history.json"] = json.dumps(load_insights_history(project_name))
    files["excluded_competitors.json"] = json.dumps(load_excluded_competitors(project_name))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_routes_insights_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add routes/files.py routes/projects.py tests/test_routes_insights_wiring.py
git commit -m "feat(routes): back insights/history/excluded-competitors files with SQLite"
```

---

## Task 18b: Cut over `routes/chats.py` (discovered during self-review)

**Why this task exists:** `docs/ARCHITECTURE.md` §3 already flagged this: `routes/chats.py`'s `DELETE`/`rename`/`append`/`link-artifact` handlers do direct read-modify-write on `chat_history.json` via `services.storage.update_json` with inline updater closures — **bypassing `chat_service.py` entirely**. Task 15 cut over `chat_service.py`, but that alone does nothing for these four routes, since they never called `chat_service` in the first place. Left unfixed, Task 20 (which removes `storage.update_json`) would break this file with an `ImportError`. This is the third and final exception to the "only `routes/files.py`/`routes/projects.py` change" constraint.

**Files:**
- Modify: `routes/chats.py`
- Test: `tests/test_routes_chats.py`

**Interfaces:** No URL or request/response shape changes — `index` stays a 0-based integer in the `link-artifact` request body exactly as today; `services/chat_service.py`'s new functions (Task 15) handle the 0-based-to-1-based `seq` conversion internally.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_routes_chats.py
import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import artefacts_repo, projects_repo


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


def _create_project_and_chat(client, monkeypatch, tmp_path, name="P"):
    monkeypatch.chdir(tmp_path)
    client.post("/api/projects", json={"name": name})
    resp = client.post("/api/chats", json={"project": name})
    return resp.get_json()["chat_id"]


def test_append_then_delete_chat(client, tmp_path, monkeypatch):
    chat_id = _create_project_and_chat(client, monkeypatch, tmp_path)
    resp = client.post(
        f"/api/chats/{chat_id}/append",
        json={"project": "P", "role": "user", "content": "hello"},
    )
    assert resp.get_json()["success"] is True

    resp = client.delete(f"/api/chats/{chat_id}", query_string={"project": "P"})
    assert resp.get_json()["success"] is True

    project_resp = client.get("/api/projects/P")
    assert chat_id not in project_resp.get_json()["chats"]


def test_rename_chat(client, tmp_path, monkeypatch):
    chat_id = _create_project_and_chat(client, monkeypatch, tmp_path)
    resp = client.post(f"/api/chats/{chat_id}/rename", json={"project": "P", "name": "Renamed"})
    assert resp.get_json()["success"] is True
    project_resp = client.get("/api/projects/P")
    assert project_resp.get_json()["chats"][chat_id]["name"] == "Renamed"


def test_append_to_missing_chat_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client.post("/api/projects", json={"name": "P"})
    resp = client.post(
        "/api/chats/nope/append", json={"project": "P", "role": "user", "content": "hi"}
    )
    assert resp.status_code == 404


def test_link_artifact_to_message_by_index(client, tmp_path, monkeypatch):
    chat_id = _create_project_and_chat(client, monkeypatch, tmp_path)
    client.post(f"/api/chats/{chat_id}/append", json={"project": "P", "role": "user", "content": "first"})
    client.post(f"/api/chats/{chat_id}/append", json={"project": "P", "role": "assistant", "content": "second"})
    pid = projects_repo.get_id("P")
    artefacts_repo.upsert("art_1", pid, "Artefact")

    resp = client.post(
        f"/api/chats/{chat_id}/link-artifact",
        json={"project": "P", "index": 1, "artifact_id": "art_1"},
    )
    assert resp.get_json()["success"] is True

    project_resp = client.get("/api/projects/P")
    messages = project_resp.get_json()["chats"][chat_id]["messages"]
    assert messages[1]["artifact_id"] == "art_1"


def test_link_artifact_out_of_range_returns_400(client, tmp_path, monkeypatch):
    chat_id = _create_project_and_chat(client, monkeypatch, tmp_path)
    client.post(f"/api/chats/{chat_id}/append", json={"project": "P", "role": "user", "content": "only"})
    resp = client.post(
        f"/api/chats/{chat_id}/link-artifact",
        json={"project": "P", "index": 5, "artifact_id": "art_1"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_routes_chats.py -v`
Expected: FAIL — handlers still call `storage.update_json` against a `chat_history.json` that `chat_service`/`chat_repo` never populate

- [ ] **Step 3: Implement**

```python
# routes/chats.py
from flask import Blueprint, jsonify, request, session

from services.chat_service import (
    create_new_chat,
    delete_chat,
    link_artifact_to_message_by_index,
    rename_chat,
    append_message_to_chat,
)
from services.project_service import normalize_project_name, project_exists

chats_bp = Blueprint("chats", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


@chats_bp.route("/api/chats", methods=["POST"])
def new_chat():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    chat_id = create_new_chat(project)
    if not chat_id:
        return jsonify({"success": False, "error": "Invalid project"}), 400
    return jsonify({"success": True, "chat_id": chat_id})


@chats_bp.route("/api/chats/<chat_id>", methods=["DELETE"])
def delete_chat_route(chat_id):
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    if delete_chat(project, chat_id):
        return jsonify({"success": True})
    return jsonify({"success": False}), 400


@chats_bp.route("/api/chats/<chat_id>/rename", methods=["POST"])
def rename_chat_route(chat_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    new_name = data.get("name")
    if not project:
        return jsonify({"error": "No project"}), 400
    if rename_chat(project, chat_id, new_name):
        return jsonify({"success": True})
    return jsonify({"success": False}), 400


@chats_bp.route("/api/chats/<chat_id>/append", methods=["POST"])
def append_chat_message(chat_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    role = data.get("role")
    content = data.get("content")
    if not project or not role or content is None:
        return jsonify({"success": False, "error": "Missing fields"}), 400
    if append_message_to_chat(project, chat_id, {"role": role, "content": content}):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Chat not found"}), 404


@chats_bp.route("/api/chats/<chat_id>/link-artifact", methods=["POST"])
def link_artifact_to_message(chat_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    index = data.get("index")
    artifact_id = data.get("artifact_id")
    if project is None or index is None or not artifact_id:
        return jsonify({"success": False, "error": "Missing fields"}), 400

    try:
        idx = int(index)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid index"}), 400

    result = link_artifact_to_message_by_index(project, chat_id, idx, artifact_id)
    if result == "not_found":
        return jsonify({"success": False, "error": "Chat not found"}), 400
    if result == "out_of_range":
        return jsonify({"success": False, "error": "Index out of range"}), 400
    return jsonify({"success": True})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_routes_chats.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add routes/chats.py tests/test_routes_chats.py
git commit -m "refactor(routes/chats): stop bypassing chat_service, use repository-backed functions"
```

---

## Task 19: Run the real migration script against sample data, verify acceptance criteria

**Files:** none created/modified — this is a manual verification task, not a code task.

- [ ] **Step 1: Back up real project data before touching anything**

```bash
cp -r projects projects.pre-migration-backup
```

- [ ] **Step 2: Run the migration against the real `projects/` directory**

```bash
python -m db.migrate
```

Expected: prints a per-project row-count report; exits 0; `projects/*/*.json` files are all still present and byte-identical to before (verify with `diff -r projects projects.pre-migration-backup` — expect no differences).

- [ ] **Step 3: Start the app and manually verify the acceptance criteria**

```bash
python app.py
```

Open each of the 7 real projects in the browser (per `docs/ARCHITECTURE - Pre-Rebuild.md` §5, these are: "Bach Food and Nutrition for Fed", "GillianTest", "International Student MBAs", "M Proj Mgmt", "Mast Public Health - 042926", "MOL - Grad Dip Psych - 040926", "MOL - MBA - 042926"). For each: confirm the artefact list matches what it showed before migration, confirm research run history/status matches, confirm chat history is intact. This directly exercises the stated acceptance criteria ("existing projects can be opened after migration and contain the same artefacts, research runs and phase associations").

- [ ] **Step 4: Remove the manual backup once verified**

```bash
rm -rf projects.pre-migration-backup
```

(The real backup is simply the untouched `projects/*/*.json` files themselves, per §8 of the spec — this manual copy was only an extra precaution for this one verification pass.)

---

## Task 20: Trim `services/storage.py` to text-only

**Files:**
- Modify: `services/storage.py`
- Modify/delete: `tests/test_storage.py` (remove tests for the deleted JSON functions, keep text-function tests)

**Note on the spec's wording:** the spec says "`services/storage.py` is deleted only after every service module has been cut over." That's imprecise — `project_service.py`'s `load_project_prompt`/`save_project_prompt` (explicitly out of scope, §2 Non-goals) still need `read_text`/`write_text` forever. This task removes only the now-unused JSON functions (`read_json`, `write_json`, `update_json`, `_read_json_reliable`), not the file.

- [ ] **Step 1: Confirm nothing still imports the JSON functions**

```bash
grep -rn "from .storage import\|from services.storage import" services/ routes/ | grep -E "read_json|write_json|update_json"
```

Expected: no output (every service that used these was cut over in Tasks 12-17).

- [ ] **Step 2: Remove the JSON-specific functions**

```python
# services/storage.py — delete read_json, _read_json_reliable, _write_json_unlocked,
# write_json, and update_json. Keep: _get_lock, _replace_with_retry, read_text, write_text.
import os
import tempfile
import threading
import time
from pathlib import Path

_locks = {}
_locks_lock = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _locks_lock:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def _replace_with_retry(tmp_path: str, target_path: Path, attempts: int = 5, delay_s: float = 0.05):
    last_error = None
    for i in range(attempts):
        try:
            os.replace(tmp_path, str(target_path))
            return
        except (PermissionError, OSError) as e:
            last_error = e
            if i == attempts - 1:
                break
            time.sleep(delay_s * (i + 1))
    raise last_error


def read_text(path: Path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return default


def write_text(path: Path, content: str):
    lock = _get_lock(path)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".write_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            _replace_with_retry(tmp, path)
        except (PermissionError, OSError):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                os.unlink(tmp)
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
```

```python
# tests/test_storage.py — remove any test referencing read_json/write_json/update_json/
# _read_json_reliable; keep (or add, if not already present) coverage for read_text/write_text
# and _replace_with_retry's PermissionError fallback path.
```

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: PASS (all tests, including Tasks 1-19's)

- [ ] **Step 4: Run ruff**

Run: `python -m ruff check .`
Expected: no errors (in particular, no unused imports left behind in any cut-over service file)

- [ ] **Step 5: Commit**

```bash
git add services/storage.py tests/test_storage.py
git commit -m "refactor(storage): remove JSON functions, keep text I/O for prompt files"
```
