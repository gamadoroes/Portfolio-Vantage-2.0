# Phase 2 — Structured Research-Task State: Design Spec

**Status:** Approved by user, pending written-spec review.
**Depends on:** Phase 1 (SQLite migration, merged to `vantage-fresh`), the I1 insights-history fix (merged).

## 1. Context and Goal

Today, a "research task" in the data model (`research_tasks` table, `db/repositories/research_tasks_repo.py`)
is a thin row that exists purely to anchor the versioned `findings` table to one of
the 7 fixed phases (`services/phases.py`) — enforced by `UNIQUE(project_id, phase_key)`.
It carries no real state: `status` and `confidence` columns exist on it today but are
**never read or written anywhere in the application** (confirmed by grep across the
codebase during design) — dead columns from the original Phase 1 migration design.

The user wants a first-class "research task" concept: a trackable unit of work with
a real status lifecycle, dependencies on other tasks, retry/review semantics, and
support for multiple tasks under one phase (e.g. one task per competitor — "Product /
La Trobe", "Product / Torrens", "Product / UniSQ", "Product / Deakin" — all under
phase 4, "Product Features").

**Acceptance criterion (user-supplied):** phases no longer have to correspond to
exactly one research run.

**Production data fact (verified during design, directly relevant to migration risk):**
all 7 real projects currently have **zero** rows in `research_tasks` and **zero** in
`findings` — nobody has used the Insights/Phase 1-7 findings feature in production
yet. This removes any data-migration risk for this feature; it does not remove the
need to protect the *mechanism* (findings/versions), which the I1 fix just stabilized
and which must not regress.

## 2. Naming Decision — Why a New Table, Not a Rebuilt One

The obvious-looking approach — relax `research_tasks`' `UNIQUE(project_id, phase_key)`
constraint and add the new columns to it in place — was tested empirically and
rejected. SQLite's standard "rebuild" recipe for changing a table-level constraint
(`CREATE new table` → `INSERT ... SELECT` → `DROP old table` → `RENAME`) requires
dropping a table that `findings.research_task_id` and `agent_decisions.research_task_id`
declare a foreign key against. Two things were confirmed directly against a live
SQLite connection, mirroring `db/migrate_runner.py`'s exact execution pattern:

1. With `PRAGMA foreign_keys = ON` (always on per `db/connection.py`), `DROP TABLE`
   on a table referenced by another table's FK **fails** with
   `FOREIGN KEY constraint failed` whenever any child row exists referencing it —
   not just on current production data (which has none), but on any future row.
2. `PRAGMA foreign_keys = OFF` issued **after** `BEGIN` (exactly how
   `migrate_runner.py` executes every statement in a migration file, since it issues
   `BEGIN` itself before parsing the file's own statements) is a **documented no-op**
   — SQLite only honors a change to this pragma outside an active transaction. This
   was also confirmed directly: the pragma reports back `1` (still on) and the drop
   still fails.

Making the rebuild work would require patching `migrate_runner.py` itself to special-case
one migration (toggle the pragma before opening its transaction, then verify with
`PRAGMA foreign_key_check` after). That is extra, permanent complexity in a
shared, already-tested module, for a rename that buys nothing functionally.

**Decision:** leave `research_tasks` / `findings` / `project_insight_versions` /
`evidence` completely untouched. The existing versioned-findings mechanism (anchored
to "one task row per phase") keeps working exactly as it does today, unmodified —
zero risk to the I1 fix. The new, richer entity lives in a **new table**,
`research_work_items`, with no uniqueness constraint on `(project_id, phase_key)` —
multiple rows per phase are allowed from the first row. The public vocabulary (API
routes, service module, user-facing language) still calls this a "research task",
matching the user's checklist; only the underlying table/repo module name is
disambiguated from the pre-existing `research_tasks` table to avoid confusing the
two mechanisms in code.

| Concept | Table | Repo module | Role |
|---|---|---|---|
| Existing (unchanged) | `research_tasks` | `research_tasks_repo.py` | Anchors versioned findings to a phase; also used by the existing "suggested ad-hoc task" feature (`create_ad_hoc_task`) |
| New (this spec) | `research_work_items` | `research_work_items_repo.py` | Trackable unit of research work: status, dependencies, scores, retries |

These two tables are not linked to each other. A `research_work_item` does not need
a `research_tasks` row to exist, and vice versa.

## 3. Data Model

One new migration file: `db/migrations/0003_research_work_items.sql`. Purely additive
— two new tables, two new nullable columns on existing tables. No rebuilds, no
pragma toggling, no change to `migrate_runner.py`.

```sql
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
    updated_at                TEXT NOT NULL
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

Notes:
- `phase_key` is nullable (matches `research_tasks.phase_key`'s existing convention
  for ad-hoc, non-phase-scoped items), but in practice every work item created from
  the Insights UI will have one of the 7 phase keys.
- `entities_json`, `source_requirements_json`, `identified_gaps_json` store JSON
  arrays of strings, following the existing `competitors_json` convention
  (`project_insight_versions.competitors_json`) rather than introducing a new array
  storage style.
- `priority` is free-text (`"low"`/`"medium"`/`"high"`, app-validated), matching the
  existing no-DB-`CHECK`-constraints style already used for `status`/`confidence`
  elsewhere in this schema — validation happens in the service layer, not SQL.
- No `CHECK` constraint enforces the status enum either, for the same reason; the
  service layer (section 4) is the single place that validates transitions.
- `research_runs.research_work_item_id` / `artefacts.research_work_item_id` are
  optional tags, not required. Existing runs/artefacts (all of which predate this
  table) simply have `NULL` here — no backfill needed or attempted.

## 4. Status Lifecycle

Nine statuses, exactly as specified:
`PROPOSED`, `READY`, `RUNNING`, `REVIEWING`, `FOLLOW_UP_REQUIRED`,
`WAITING_FOR_HUMAN`, `COMPLETE`, `FAILED`, `SKIPPED`.

A work item is always created as `PROPOSED`. Allowed transitions (anything not
listed here is rejected by the service layer with a clear error — no silent no-ops):

| From | To | Guard |
|---|---|---|
| `PROPOSED` | `READY` | All `depends_on` work items are `COMPLETE` or `SKIPPED` (checked at transition time, not continuously) |
| `PROPOSED` | `SKIPPED` | — |
| `READY` | `RUNNING` | — |
| `READY` | `SKIPPED` | — |
| `RUNNING` | `REVIEWING` | — |
| `RUNNING` | `FAILED` | increments `retry_count` |
| `RUNNING` | `COMPLETE` | only when `human_review_required = 0` |
| `REVIEWING` | `COMPLETE` | only when `human_review_required = 0` |
| `REVIEWING` | `FOLLOW_UP_REQUIRED` | — |
| `REVIEWING` | `WAITING_FOR_HUMAN` | only when `human_review_required = 1` |
| `WAITING_FOR_HUMAN` | `REVIEWING` | — |
| `WAITING_FOR_HUMAN` | `COMPLETE` | — |
| `WAITING_FOR_HUMAN` | `FAILED` | increments `retry_count` |
| `FOLLOW_UP_REQUIRED` | `READY` | same dependency guard as `PROPOSED → READY` |
| `FAILED` | `READY` | only when `retry_count < max_retries` |
| `FAILED` | `SKIPPED` | — |

`COMPLETE` and `SKIPPED` are **terminal** — no transitions out. Reopening finished or
abandoned work means creating a new `research_work_items` row (optionally depending
on the old one), not mutating a terminal row. This keeps the status machine simple
and keeps a durable history of what was actually attempted, matching the append-only
philosophy already used for `findings`/`evidence` elsewhere in this schema.

This phase explicitly does **not** auto-transition anything (per the user's scope
decision: data model + manual transitions, no auto-dispatch). `READY`-eligibility via
the dependency guard is evaluated only when something actually requests the
`PROPOSED → READY` (or `FOLLOW_UP_REQUIRED → READY`) transition — nothing watches
dependencies in the background and flips status on its own. `human_review_required`
is a stored flag read by the guard above; nothing else reacts to it automatically in
this phase (e.g. there is no notification system) — that is reserved for a future
phase and is explicitly out of scope here.

## 5. Dependencies

`research_work_item_dependencies(work_item_id, depends_on_work_item_id)` is a plain
many-to-many join table — a work item can depend on several others, and can be
depended on by several others. Self-dependency (`work_item_id = depends_on_work_item_id`)
and cycles are rejected at the service layer (depth-first search over existing edges
before inserting a new one) — not enforced by SQL, consistent with this schema's
existing style of putting invariants in the service layer rather than `CHECK`
constraints.

Dependencies only span work items within the **same project** (no cross-project
dependency graphs) — enforced by checking both items' `project_id` match before
allowing the edge.

## 6. Aggregation — Deterministic Phase Roll-up

A new function, `services/research_task_service.py::compute_phase_rollup(project_id,
phase_key)`, computes a deterministic summary over that phase's work items — no LLM
involved, pure aggregation:

- `status_counts`: dict of status → count, over all work items in that phase.
- `weakest_confidence`-equivalent: the lowest `completeness_score` among `COMPLETE`
  work items (`None` if no work items are `COMPLETE` yet).
- `merged_gaps`: the union of `identified_gaps_json` across all work items in the
  phase (not just `COMPLETE` ones — an incomplete task's known gaps are still useful
  signal), de-duplicated, order-preserving.

This result becomes one new key, `task_summary`, added to the phase payload that
`services/insights_service.py::_build_phase_data` already returns. The existing
`summary`, `confidence`, `gaps`, `evidence_sources`, `linked_files`, `linked_file_ids`
keys are **not** touched or replaced — `task_summary` rides alongside them. This is
the resolution to the "aggregate synthesis" design question: the existing
AI-authored narrative (written by whatever process calls `save_insights`) keeps
working exactly as today; the new roll-up is purely additive, computed fresh on
every read (no caching, no snapshotting — there's no version history for work-item
state, only for the separate findings/versions mechanism).

## 7. Service and Repository Layer

**`db/repositories/research_work_items_repo.py`** (new file):
- `create(project_id, phase_key, title, **optional_fields) -> id`
- `get(id)`
- `list_for_project(project_id)`
- `list_for_phase(project_id, phase_key)`
- `update_fields(id, **fields)` — generic field update (mirrors
  `research_runs_repo.update`'s existing `**fields` pattern), used for scores,
  gaps, retry_count, etc. Does not validate status transitions — that's the service
  layer's job.
- `add_dependency(work_item_id, depends_on_work_item_id)`
- `remove_dependency(work_item_id, depends_on_work_item_id)`
- `list_dependencies(work_item_id)` — what this item depends on
- `list_dependents(work_item_id)` — what depends on this item

**`services/research_task_service.py`** (new file):
- `ALLOWED_TRANSITIONS`: a `dict[str, set[str]]` encoding the table in section 4.
- `create_task(project_id, phase_key, title, **optional_fields)` — always creates
  the row with `status='PROPOSED'` (callers cannot set an initial status); if
  `priority` is supplied, validates it is one of `low`/`medium`/`high` and rejects
  anything else with `ValueError`, then delegates to the repo.
- `transition_task(work_item_id, new_status)` — looks up the current status, checks
  `new_status` is a legal transition from it (section 4's table), checks the
  additional guards (dependency completeness, `retry_count < max_retries`,
  `human_review_required`), raises a `ValueError` with a clear message on rejection,
  otherwise calls `update_fields(status=new_status, ...)` (incrementing
  `retry_count` where the table specifies it).
- `add_dependency(work_item_id, depends_on_work_item_id)` — validates same-project,
  no self-reference, no cycle (DFS over `list_dependencies` transitively), then
  delegates to the repo.
- `compute_phase_rollup(project_id, phase_key)` — section 6.

## 8. Routes

New blueprint, `routes/research_tasks.py` (registered in `app.py` alongside the
existing blueprints):

- `POST /api/research-tasks` — body: `{project, phase_key, title, objective?,
  priority?, research_method?, entities?, expected_output?,
  source_requirements?, human_review_required?, max_retries?}`. Creates a
  `PROPOSED` work item.
- `GET /api/research-tasks?project=&phase_key=` — list, optionally filtered to one
  phase.
- `POST /api/research-tasks/<id>/transition` — body: `{status}`. Calls
  `transition_task`; returns 400 with the `ValueError` message on an illegal
  transition.
- `POST /api/research-tasks/<id>/dependencies` — body: `{depends_on_task_id}`.
- `DELETE /api/research-tasks/<id>/dependencies/<depends_on_id>`.
- No `DELETE /api/research-tasks/<id>` — matching the append-only philosophy from
  section 4; abandoning work means transitioning to `SKIPPED`, not deleting the row.

**Explicitly out of scope for this phase:** wiring a "start research for this task"
button that creates a `research_runs` row pre-tagged with `research_work_item_id`.
The column exists (section 3) and a future phase can populate it, but no route in
this phase modifies `routes/ai.py`'s existing research-run creation flow. This keeps
the phase's blast radius to new code only — nothing in the existing deep-research
lifecycle changes.

## 9. UI Integration

Additive only, per the global constraint (preserve the existing Phase 1-7
presentation layer): each phase tab in `static/app.js` gains a new panel, below the
existing summary/evidence display, listing that phase's `research_work_items` —
title, status badge, priority, entities, scores, identified gaps — plus the
`task_summary` roll-up as a header line above the list ("3 of 4 complete · weakest
completeness 0.6 · 2 open gaps"). Creating a task, transitioning its status, and
adding dependencies happen through this panel, calling the new routes in section 8.
The existing single summary/confidence text and the History view are untouched.

## 10. Testing Strategy

- `tests/test_db_migrations.py` — new migration applies cleanly; both new tables and
  both new columns exist; existing tests (`test_apply_migrations_is_idempotent`, now
  asserting 3 migrations) updated.
- `tests/test_repositories_research_work_items.py` (new) — CRUD, dependency
  add/remove, cycle rejection, same-project enforcement, listing.
- `tests/test_research_task_service.py` (new) — every legal transition in section
  4's table succeeds; every illegal one is rejected with `ValueError`; the
  dependency guard on `→ READY` blocks correctly when a dependency isn't
  `COMPLETE`/`SKIPPED` and allows it once satisfied; `FAILED → READY` is blocked once
  `retry_count >= max_retries`; `compute_phase_rollup` produces correct counts,
  weakest-completeness, and de-duplicated merged gaps on a hand-built fixture of
  several work items in mixed states.
- `tests/test_routes_research_tasks.py` (new) — route-level happy path and
  error-path (400 on illegal transition) tests, following the existing pattern in
  `tests/test_routes_chats.py`.
- `tests/test_insights_service.py` — add a test confirming `_build_phase_data` now
  includes a `task_summary` key without breaking any existing assertion on the other
  keys.
- Full existing suite (177 tests as of the I1 fix) re-run at the end to confirm zero
  regression — this phase must not touch `research_tasks`, `findings`, `evidence`,
  or `project_insight_versions` at all, so a regression there would indicate a
  spec violation, not an acceptable tradeoff.

## 11. Explicitly Out of Scope (this phase)

- Any automatic orchestration: nothing auto-creates tasks, auto-transitions status
  based on dependency completion, or auto-dispatches a `research_run`. All
  transitions are explicit calls triggered by a human action in the UI.
- Modifying `routes/ai.py`'s existing deep-research creation/polling flow.
- Backfilling `research_work_item_id` on any existing `research_runs`/`artefacts`
  rows (there's nothing meaningful to backfill it with).
- Any change to `research_tasks`, `findings`, `evidence`, `project_insight_versions`,
  or `agent_decisions`.
- A parent/child task hierarchy (rejected in favor of flat sibling tasks under one
  phase, per the subtask-modeling design question).
- Hard deletion of work items.
