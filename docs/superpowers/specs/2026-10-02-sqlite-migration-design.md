# Phase 1 — Replace JSON Application State with SQLite — Design Spec

**Status:** Approved for planning (pending final user sign-off on this document)
**Related:** `docs/ARCHITECTURE.md` (current-state architecture, written first; this spec assumes that document as ground truth for today's behavior)

## 1. Goal

Replace the JSON-file-per-concern storage model (`projects/{name}/config.json`, `metadata.json`, `chat_history.json`, `artifacts.json`, `file_index.json`, `research_runs.json`, `insights.json`) with a single SQLite database per deployment, accessed exclusively through a new repository layer. This is explicitly scoped to **application knowledge and state** — not large external (HEIMS) datasets.

**Acceptance criteria (verbatim from requirements):** existing projects can be opened after migration and contain the same artefacts, research runs, and phase associations.

## 2. Non-goals

- No change to `routes/*.py` request/response **shapes** or `static/app.js` — this is a storage-layer swap underneath the existing API, invisible to the frontend. (One deliberate, narrow exception: `routes/files.py` and `routes/projects.py` gain internal special-casing for three reserved filenames — see §3 and §6 — but the JSON shapes they return/accept are byte-for-byte unchanged, so `static/app.js` needs no changes.)
- No change to uploaded file or artefact *content* storage — `projects/{name}/files/` stays on disk.
- No backend worker/scheduler introduced (out of scope; a separate, paused design exists for deep-research concurrency and is deliberately *not* part of this phase — see §9).
- No requirement to get `research_tasks`/`evidence`/`findings`/`finding_evidence`/`agent_decisions` "right" in a product sense — today's app has no structured equivalent for these, so Phase 1 creates correct schema and a best-effort migration, not a finished feature.
- `system_prompt.txt`/`project_prompt.txt` stay as plain text files — no table was requested for these, they're outside the JSON-file set this phase targets, and `project_service.save_project_prompt`/`load_project_prompt` are unchanged.

## 3. Key decisions (confirmed with user)

| Decision | Choice | Why |
|---|---|---|
| Scope of new tables (research_tasks/sources/evidence/findings/finding_evidence/agent_decisions) | Create schema now; best-effort migrate what data exists | Unblocks later phases without over-designing a feature that doesn't exist yet |
| DB access library | stdlib `sqlite3` + hand-rolled migration runner | Zero new dependencies; matches `storage.py`'s existing hand-rolled style (5 pinned deps total today) |
| Cutover model | One-shot CLI migration script, then full cutover; JSON stays as untouched forensic backup | Matches "keep JSON as backup until migration is verified"; avoids dual-write drift risk |
| File content location | Metadata only moves to SQLite; `projects/{name}/files/` stays on disk | Matches "not large HEIMS datasets"; avoids storing document bytes in the DB |
| `gaps` vs `suggested_topics` mapping | `suggested_topics` → new `research_tasks` rows (`status='not_started'`); `gaps` → `findings.gaps_notes` text column | A suggested topic is an actionable next research task; a gap is a note tightly bound to the finding it was observed alongside |
| DB file location | `instance/app.db` (Flask's conventional instance folder), env-overridable via `DATABASE_PATH`, added to `.gitignore` | This repo lives inside OneDrive sync, which is why `storage.py` already retries on `PermissionError` for JSON writes. A live SQLite file is far less tolerant of being locked/renamed mid-write by a sync client than an atomic-replace JSON write is. Keeping `app.db` outside the synced `projects/` tree avoids that failure mode entirely. |
| `insights.json`/`insights_history.json`/`excluded_competitors.json` live sync | **In scope.** `routes/files.py` (`POST /api/files`) and `routes/projects.py` (`GET /api/projects/<name>`) special-case these three reserved filenames and redirect to a new `services/insights_service.py` instead of plain file I/O, reconstructing byte-equivalent JSON at read time | Originally deferred as a one-time snapshot (see prior draft); user chose to bring it in now rather than migrate this data twice. Every other filename keeps today's plain file behavior untouched. |
| Findings versioning | `findings` is append-only — a full new set of rows is written on every insights save, linked to a new `project_insight_versions` row | `insights_history.json` already stores a full snapshot per save today with a UI to browse old versions (`currentInsightsVersion`, version nav). Matching that exactly avoids silently regressing an existing feature. |

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

CREATE TABLE project_selected_sources (
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, source_id)
    -- Replaces config.json's selected_files/selected_file_ids arrays (which files are
    -- active as chat/insight context for this project). Found missing during plan
    -- write-up self-review -- config.json is in the Goal's JSON-file list but this
    -- field had no table.
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

CREATE TABLE project_insight_versions (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    version         INTEGER NOT NULL,       -- 1, 2, 3... per project, matches old insightsHistory[].version
    generated_at    TEXT NOT NULL,          -- old insights.json.generated_at
    competitors_json TEXT,                  -- old insights.json.competitors, stored as raw JSON (no dedicated table requested for this)
    competitor_landscape_markdown TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE TABLE findings (
    id                  INTEGER PRIMARY KEY,
    research_task_id    INTEGER NOT NULL REFERENCES research_tasks(id),
    insight_version_id  INTEGER NOT NULL REFERENCES project_insight_versions(id),
    content             TEXT NOT NULL,      -- old phase.summary
    gaps_notes          TEXT,               -- old phase.gaps, joined to one text block
    confidence          TEXT,
    effective_date      TEXT,               -- "as of" date of the underlying researched facts, if stated
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(research_task_id, insight_version_id)
    -- Append-only: every insights save writes one findings row per phase (all 7, even
    -- unchanged ones, matching today's full-blob-per-version behavior) under a new
    -- insight_version_id. "Current" state = the findings joined to the latest version.
    -- The UNIQUE constraint guards against a save accidentally inserting two rows for
    -- the same phase within one version (e.g. a retried transaction).
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

CREATE TABLE excluded_competitors (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    competitor_name TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(project_id, competitor_name)
);
```

`PHASE_DEFINITIONS` (today hardcoded only in `static/app.js`) is ported into a shared Python constant (`services/phases.py` or similar) used to seed `research_tasks` rows per project and to drive the existing `insight_type`/`phase-N` token-tuning logic in `ai.py` — giving the backend phase knowledge it has never had, without changing any behavior.

## 5. Migration script

`db/migrate.py`, a one-shot, re-run-safe CLI:

1. Opens/creates `instance/app.db` (path from `DATABASE_PATH` config, default `instance/app.db`). Applies pending `db/migrations/NNNN_*.sql` files in order, tracked in `schema_migrations`. Re-running when fully applied is a no-op.
2. Walks every `projects/*/` directory. For each:
   - `config.json` + `metadata.json` → one `projects` row (upsert by `name`); `config.json.selected_files`/`selected_file_ids` → `project_selected_sources` rows (resolved to `source_id` via `stable_file_id`/filename match against `sources`, inserted after `sources` are migrated for that project).
   - `file_index.json` → `sources` rows (upsert by `project_id, stable_file_id`).
   - `artifacts.json` → `artefacts` rows (upsert by `id`), linked to `sources` by matching the artefact's backing filename.
   - `chat_history.json` → one `chat_sessions` row per session key, `project_messages` rows per message (`seq` = original array position).
   - `research_runs.json` → `research_runs` rows (upsert by `id`).
   - `excluded_competitors.json`, if present → `excluded_competitors` rows (upsert by `project_id, competitor_name`).
   - `insights_history.json`, if present → one `project_insight_versions` row per history entry (`version` = entry's `version` field, `generated_at`/`competitors`/`competitor_landscape_markdown` from that entry's full `data` snapshot), and for each entry's `phases[key]`:
     - One `research_tasks` row (`phase_key=key`), upserted by `(project_id, phase_key)` — identity is shared across versions, only created once.
     - One `findings` row per version per phase (non-"MISSING" `summary` + `gaps` joined), linked to that version's `project_insight_versions` row.
     - Each `evidence_sources` string → one `evidence` row (`raw_text`; `source_id` resolved via `linked_file_ids`/`linked_files` cross-lookup against `sources` where possible) linked via `finding_evidence` to that version's finding.
     - Each `suggested_topics` string → an additional ad-hoc `research_tasks` row (`phase_key=NULL`, `status='not_started'`), created once (not re-created per historical version, since these are forward-looking tasks, not historical facts).
   - `insights.json` alone (no `insights_history.json`, or `insights.json`'s content differs from the last history entry — e.g. an edit made after the last history seed): treated as one additional, final `project_insight_versions` row on top of whatever `insights_history.json` contributed, so the *current* state on disk always wins as the latest version.
   - None of today's real sample projects have any of these three files — this path is exercised by synthetic test fixtures (see §7) until real usage exists.
3. All inserts are upserts keyed on preserved stable IDs (project name, `stable_file_id`, artefact/run/chat-session id, `(project_id, phase_key)`, `(project_id, version)`) — safe to re-run after a partial failure without duplicating rows.
4. Source JSON files are never modified or deleted.
5. Prints a per-project, per-table row-count report for manual verification.

## 6. Repository / service layer

New `db/` package:
- `db/connection.py` — context-managed `sqlite3` connection factory; sets `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, a busy timeout.
- `db/migrations/*.sql` — plain numbered SQL files (the schema in §4 split into an initial migration).
- `db/repositories/` — one module per aggregate (`projects_repo.py`, `sources_repo.py` covering sources + `project_selected_sources`, `artefacts_repo.py`, `chat_repo.py`, `research_runs_repo.py`, `research_tasks_repo.py` covering tasks/findings/evidence/finding_evidence/insight_versions, `excluded_competitors_repo.py`, `agent_decisions_repo.py`). Plain functions over raw rows — no ORM.

Every existing `services/*.py` module keeps its current public function names, parameters, and return shapes unchanged; only internals swap from `storage.read_json/write_json/update_json` to repository calls. `services/storage.py` is deleted only after every service module has been cut over — not before, and not as part of this spec's first task.

**New `services/insights_service.py`** (the one place with genuinely new logic, not just a storage swap):
- `save_insights(project_name, data)` — called when `POST /api/files` receives `filename == "insights.json"`. Opens one DB transaction: creates a new `project_insight_versions` row (`version` = previous max + 1), then for each of the 7 phase keys in `data["phases"]`, upserts a `research_tasks` row (by `(project_id, phase_key)`, creating it on first use) and inserts a fresh `findings` row linked to the new version, plus `evidence`/`finding_evidence` rows for that phase's `evidence_sources`/`linked_file_ids`, plus ad-hoc `research_tasks` rows for `suggested_topics`. All 7 phases get a new findings row every save, matching today's full-blob-per-version behavior.
- `load_current_insights(project_name)` — called from `GET /api/projects/<name>` to populate `files["insights.json"]`. Reconstructs the exact JSON shape `createEmptyInsightsData()`/`normalizeInsightsData()` expect, from the latest `project_insight_versions` row and its joined `findings`/`evidence`/`finding_evidence`.
- `load_insights_history(project_name)` — called when `filename == "insights_history.json"` is requested; reconstructs the `[{version, generated_at, data}]` array shape from all `project_insight_versions` rows (oldest to newest), each with its full nested `data` object rebuilt the same way as `load_current_insights` but pinned to that version.
- `save_excluded_competitors(project_name, names)` / `load_excluded_competitors(project_name)` — thin wrappers over `excluded_competitors_repo`, triggered by `filename == "excluded_competitors.json"`.

**Route changes (the one deliberate exception to "zero route changes"):** `routes/files.py`'s `POST /api/files` handler checks `filename` against the three reserved names (`insights.json`, `insights_history.json`, `excluded_competitors.json`) before falling through to the existing generic `file_service.save_project_file` path, and calls the matching `insights_service` function instead. `routes/projects.py`'s `GET /api/projects/<name>` does the equivalent on the read side when assembling the `files` dict in its response. Every other filename is completely unaffected. `static/app.js` sends/receives the identical JSON shapes either way, so it needs no changes.

## 7. Testing

- `tests/test_db_migrations.py` — schema applies cleanly to an empty DB; re-applying is a no-op; a missing/out-of-order migration file is rejected.
- `tests/test_migration_script.py` — **the acceptance-criteria test.** Copies real sample project folders (e.g. "M Proj Mgmt", which has genuine runs/artefacts/chat data) into a temp dir, runs the migration script, and asserts row-for-row parity against the original JSON for artefacts, research runs, and phase associations. Also asserts a project with no `insights.json` migrates cleanly to zero `research_tasks` rows (not an error).
- `tests/test_repositories_*.py` — CRUD round-trip per repository module against a temp DB.
- `tests/test_insights_service.py` — synthetic fixtures (since no real project has this data yet): round-trips a multi-save sequence through `save_insights`/`load_current_insights`/`load_insights_history` and asserts the reconstructed JSON is shape-and-content-equivalent to what was saved, across multiple versions (i.e. version 1's data is still exactly recoverable after version 3 is saved). Also covers `excluded_competitors` save/load.
- Existing tests asserting JSON-file side effects (`test_file_index_service.py`, `test_reference_integrity_service.py`, etc.) are updated as part of that specific service module's migration task, not as a separate cleanup pass.

## 8. Rollback

Cutover is one-shot, not dual-write. If an issue is found post-migration: fix the bug, delete `instance/app.db`, re-run `db/migrate.py` against the untouched JSON. Nothing authoritative is ever deleted, so this is always cheap.

## 9. Explicitly out of scope / deferred

- The paused "concurrent deep research job queue" design (see conversation history / future spec) is *not* part of this phase. The `research_runs` schema above (`response_id` nullable, `status` including `'queued'`) was shaped to not block that work later, but no queueing logic is implemented here.
- No background worker/scheduler process.
- No change to how `insights.json`'s phase workflow is *generated* (the prompt assembly, phase boundaries, sequencing in `generatePhaseInsight`/`generateInsights`) — that stays entirely client-side per `docs/ARCHITECTURE.md` §8-10. Only *where the resulting data is stored* changes, via `services/insights_service.py` (§6) — and that storage is now genuinely live, not a one-time snapshot, since the "build it in now" decision brought `routes/files.py`/`routes/projects.py` special-casing into scope.
- `effective_date`/`retrieved_at` columns (on `sources`/`evidence`/`findings`) have no corresponding field in any current JSON file — the migration script and `insights_service` both leave them `NULL`. They exist as forward-looking schema for when the app starts tracking source currency, not as something this phase populates.
