# Phase 1 — Replace JSON Application State with SQLite — Design Spec

**Status:** Approved for planning (pending final user sign-off on this document)
**Related:** `docs/ARCHITECTURE.md` (current-state architecture, written first; this spec assumes that document as ground truth for today's behavior)

## 1. Goal

Replace the JSON-file-per-concern storage model (`projects/{name}/config.json`, `metadata.json`, `chat_history.json`, `artifacts.json`, `file_index.json`, `research_runs.json`, `insights.json`) with a single SQLite database per deployment, accessed exclusively through a new repository layer. This is explicitly scoped to **application knowledge and state** — not large external (HEIMS) datasets.

**Acceptance criteria (verbatim from requirements):** existing projects can be opened after migration and contain the same artefacts, research runs, and phase associations.

## 2. Non-goals

- No change to `routes/*.py` request/response contracts or `static/app.js` — this is a storage-layer swap underneath the existing API, invisible to the frontend.
- No change to uploaded file or artefact *content* storage — `projects/{name}/files/` stays on disk.
- No backend worker/scheduler introduced (out of scope; a separate, paused design exists for deep-research concurrency and is deliberately *not* part of this phase — see §9).
- No requirement to get `research_tasks`/`evidence`/`findings`/`finding_evidence`/`agent_decisions` "right" in a product sense — today's app has no structured equivalent for these, so Phase 1 creates correct schema and a best-effort migration, not a finished feature.

## 3. Key decisions (confirmed with user)

| Decision | Choice | Why |
|---|---|---|
| Scope of new tables (research_tasks/sources/evidence/findings/finding_evidence/agent_decisions) | Create schema now; best-effort migrate what data exists | Unblocks later phases without over-designing a feature that doesn't exist yet |
| DB access library | stdlib `sqlite3` + hand-rolled migration runner | Zero new dependencies; matches `storage.py`'s existing hand-rolled style (5 pinned deps total today) |
| Cutover model | One-shot CLI migration script, then full cutover; JSON stays as untouched forensic backup | Matches "keep JSON as backup until migration is verified"; avoids dual-write drift risk |
| File content location | Metadata only moves to SQLite; `projects/{name}/files/` stays on disk | Matches "not large HEIMS datasets"; avoids storing document bytes in the DB |
| `gaps` vs `suggested_topics` mapping | `suggested_topics` → new `research_tasks` rows (`status='not_started'`); `gaps` → `findings.gaps_notes` text column | A suggested topic is an actionable next research task; a gap is a note tightly bound to the finding it was observed alongside |
| DB file location | `instance/app.db` (Flask's conventional instance folder), env-overridable via `DATABASE_PATH`, added to `.gitignore` | This repo lives inside OneDrive sync, which is why `storage.py` already retries on `PermissionError` for JSON writes. A live SQLite file is far less tolerant of being locked/renamed mid-write by a sync client than an atomic-replace JSON write is. Keeping `app.db` outside the synced `projects/` tree avoids that failure mode entirely. |

## 4. Schema

All tables include `created_at`/`updated_at` (ISO8601 TEXT, like today's JSON) unless noted. `retrieved_at` and `effective_date` are added only where they carry real meaning (provenance/currency of researched facts), per the requirement to add these "where relevant."

```sql
CREATE TABLE schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,      -- matches projects/{name}/ folder name today
    description TEXT,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE sources (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    stable_file_id  TEXT NOT NULL,         -- f_{16-hex}, preserved from file_index.json for continuity
    filename        TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'upload',  -- 'upload' | 'generated' | 'deep_research_output'
    retrieved_at    TEXT,                  -- when fetched/ingested, if known (nullable)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(project_id, stable_file_id)
);

CREATE TABLE artefacts (
    id          TEXT PRIMARY KEY,          -- art_{epoch}_{4hex}, preserved from artifacts.json
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    source_id   INTEGER REFERENCES sources(id),  -- backing .md file
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE chat_sessions (
    id          TEXT PRIMARY KEY,          -- {YYYYMMDD}_{HHMMSS}_{4hex}, preserved
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE project_messages (
    id              INTEGER PRIMARY KEY,
    chat_session_id TEXT NOT NULL REFERENCES chat_sessions(id),
    seq             INTEGER NOT NULL,      -- replaces fragile array-index artefact linking
    role            TEXT NOT NULL,         -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    artefact_id     TEXT REFERENCES artefacts(id),
    created_at      TEXT NOT NULL,
    UNIQUE(chat_session_id, seq)
);

CREATE TABLE research_runs (
    id            TEXT PRIMARY KEY,        -- run_{epoch}_{4hex}, preserved
    project_id    INTEGER NOT NULL REFERENCES projects(id),
    response_id   TEXT,                    -- OpenAI response id; NULL until a run is actually submitted
    chat_session_id TEXT REFERENCES chat_sessions(id),
    status        TEXT NOT NULL,           -- 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
    artefact_id   TEXT REFERENCES artefacts(id),
    prompt_preview TEXT,                   -- <=200 chars, as today
    error         TEXT,
    created_at    TEXT NOT NULL,
    completed_at  TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE research_tasks (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    phase_key   TEXT,                      -- '1'-'7' for the fixed phases; NULL for ad-hoc tasks from suggested_topics
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'not_started',  -- 'not_started' | 'in_progress' | 'done'
    confidence  TEXT,                      -- 'none' | 'medium' | ... (mirrors today's string values)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(project_id, phase_key)           -- NULLs (ad-hoc tasks) are exempt from this constraint in SQLite
);

CREATE TABLE findings (
    id               INTEGER PRIMARY KEY,
    research_task_id INTEGER NOT NULL REFERENCES research_tasks(id),
    content          TEXT NOT NULL,         -- old phase.summary
    gaps_notes       TEXT,                  -- old phase.gaps, joined to one text block
    confidence       TEXT,
    effective_date   TEXT,                  -- "as of" date of the underlying researched facts, if stated
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE evidence (
    id            INTEGER PRIMARY KEY,
    project_id    INTEGER NOT NULL REFERENCES projects(id),
    source_id     INTEGER REFERENCES sources(id),  -- set only when resolvable to a real uploaded/generated file
    raw_text      TEXT,                     -- old evidence_sources string, when not resolvable to a source
    retrieved_at  TEXT,
    effective_date TEXT,
    created_at    TEXT NOT NULL
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
    detail           TEXT,                  -- free-form JSON text
    created_at       TEXT NOT NULL
);
```

`PHASE_DEFINITIONS` (today hardcoded only in `static/app.js`) is ported into a shared Python constant (`services/phases.py` or similar) used to seed `research_tasks` rows per project and to drive the existing `insight_type`/`phase-N` token-tuning logic in `ai.py` — giving the backend phase knowledge it has never had, without changing any behavior.

## 5. Migration script

`db/migrate.py`, a one-shot, re-run-safe CLI:

1. Opens/creates `instance/app.db` (path from `DATABASE_PATH` config, default `instance/app.db`). Applies pending `db/migrations/NNNN_*.sql` files in order, tracked in `schema_migrations`. Re-running when fully applied is a no-op.
2. Walks every `projects/*/` directory. For each:
   - `config.json` + `metadata.json` → one `projects` row (upsert by `name`).
   - `file_index.json` → `sources` rows (upsert by `project_id, stable_file_id`).
   - `artifacts.json` → `artefacts` rows (upsert by `id`), linked to `sources` by matching the artefact's backing filename.
   - `chat_history.json` → one `chat_sessions` row per session key, `project_messages` rows per message (`seq` = original array position).
   - `research_runs.json` → `research_runs` rows (upsert by `id`).
   - `insights.json`, if present (none of today's real projects have one — this path is exercised only by synthetic test fixtures until real usage exists):
     - Each `phases[key]` → one `research_tasks` row (`phase_key=key`).
     - Non-"MISSING" `summary` (+ `gaps` joined) → one `findings` row.
     - Each `evidence_sources` string → one `evidence` row (`raw_text`; `source_id` resolved via `linked_file_ids`/`linked_files` cross-lookup against `sources` where possible).
     - `linked_file_ids` → `finding_evidence` rows (the one genuinely relational fact in today's data — not best-effort, this migrates exactly).
     - Each `suggested_topics` string → an additional ad-hoc `research_tasks` row (`phase_key=NULL`, `status='not_started'`).
3. All inserts are upserts keyed on preserved stable IDs (project name, `stable_file_id`, artefact/run/chat-session id) — safe to re-run after a partial failure without duplicating rows.
4. Source JSON files are never modified or deleted.
5. Prints a per-project, per-table row-count report for manual verification.

## 6. Repository / service layer

New `db/` package:
- `db/connection.py` — context-managed `sqlite3` connection factory; sets `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, a busy timeout.
- `db/migrations/*.sql` — plain numbered SQL files (the schema in §4 split into an initial migration).
- `db/repositories/` — one module per aggregate (`projects_repo.py`, `sources_repo.py`, `artefacts_repo.py`, `chat_repo.py`, `research_runs_repo.py`, `research_tasks_repo.py` covering tasks/findings/evidence/finding_evidence, `agent_decisions_repo.py`). Plain functions over raw rows — no ORM.

Every existing `services/*.py` module keeps its current public function names, parameters, and return shapes unchanged; only internals swap from `storage.read_json/write_json/update_json` to repository calls. **This means zero changes to `routes/*.py` or `static/app.js`.** `services/storage.py` is deleted only after every service module has been cut over — not before, and not as part of this spec's first task.

## 7. Testing

- `tests/test_db_migrations.py` — schema applies cleanly to an empty DB; re-applying is a no-op; a missing/out-of-order migration file is rejected.
- `tests/test_migration_script.py` — **the acceptance-criteria test.** Copies real sample project folders (e.g. "M Proj Mgmt", which has genuine runs/artefacts/chat data) into a temp dir, runs the migration script, and asserts row-for-row parity against the original JSON for artefacts, research runs, and phase associations. Also asserts a project with no `insights.json` migrates cleanly to zero `research_tasks` rows (not an error).
- `tests/test_repositories_*.py` — CRUD round-trip per repository module against a temp DB.
- Existing tests asserting JSON-file side effects (`test_file_index_service.py`, `test_reference_integrity_service.py`, etc.) are updated as part of that specific service module's migration task, not as a separate cleanup pass.

## 8. Rollback

Cutover is one-shot, not dual-write. If an issue is found post-migration: fix the bug, delete `instance/app.db`, re-run `db/migrate.py` against the untouched JSON. Nothing authoritative is ever deleted, so this is always cheap.

## 9. Explicitly out of scope / deferred

- The paused "concurrent deep research job queue" design (see conversation history / future spec) is *not* part of this phase. The `research_runs` schema above (`response_id` nullable, `status` including `'queued'`) was shaped to not block that work later, but no queueing logic is implemented here.
- No background worker/scheduler process.
- No change to how `insights.json`'s phase workflow is *generated* — it stays entirely client-side per `docs/ARCHITECTURE.md` §8-10.
- **`research_tasks`/`findings`/`evidence`/`finding_evidence` are a one-time snapshot, not a live sync.** `insights.json` is written today through the generic `POST /api/files` route — a plain file write with no awareness of these new tables. This phase does not add a dedicated insights endpoint that parses saved `insights.json` content back into the new tables on every save. Practical effect: the migration script populates these tables once, from whatever `insights.json` exists (today: nothing, in every real project) at migration time; any phase data a user generates or edits *after* migration keeps landing only in the `insights.json` file on disk, not in these tables, until a later phase adds that write path. Flagging this now so it isn't a surprise when these tables don't reflect new phase activity post-cutover.
- `effective_date`/`retrieved_at` columns (on `sources`/`evidence`/`findings`) have no corresponding field in any current JSON file — the migration script leaves them `NULL` for all migrated rows. They exist as forward-looking schema for when the app starts tracking source currency, not as something this phase populates.
