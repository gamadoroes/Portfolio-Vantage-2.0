# Phase 3 — Research Supervisor: Design Spec

**Status:** Approved by user, pending written-spec review.
**Depends on:** Phase 2 (structured research-task state, merged to `vantage-fresh`).

## 1. Context and Goal

Phase 2 introduced `research_work_items` — a trackable research task with a
9-state lifecycle, dependencies, retry/review tracking, and a deterministic
phase roll-up — but deliberately stopped at "data model + manual
transitions only": every status change happens because a human clicked
something. The user's checklist calls this out explicitly as a follow-up
("Allow tasks to run independently where dependencies permit" was listed
but not auto-dispatched).

Phase 3 builds the decision-making layer Phase 2 deferred: a single
LLM-driven "supervisor" that looks at a project's objective, its existing
findings, and its work items' current state, and decides the next research
action — proposing tasks, dispatching eligible ones, reviewing what came
back, spawning follow-ups, and eventually declaring the project ready for
final synthesis. The user explicitly asked for **one supervisor, not seven
autonomous agents** — a single decision-making process that reasons across
all phases at once, not one independent agent per phase.

**Acceptance criterion (user-supplied):** the supervisor can determine the
next research action without the user manually selecting every phase.

## 2. Scope Decisions (confirmed with user during brainstorming)

- **Trigger model:** explicit "Run Supervisor" button. No background
  scheduling, no auto-fire on task completion. The user clicks, one
  decision happens, the response comes back. This matches Phase 2's
  precedent (everything today is request/response; nothing runs without an
  HTTP request) and leaves room to add auto-looping later without a
  redesign.
- **Decisions per click:** exactly one. The supervisor reads current state,
  makes one decision, executes it, logs it, returns. No internal loop, no
  runaway-loop guard needed for this phase — a user who wants faster
  progress just clicks again.
- **Research methods:** two — `FILE_ANALYSIS` (ask Claude over the
  project's already-uploaded source files; today's `/api/chat` code path)
  and `TARGETED_WEB` (an OpenAI deep-research web-search job; today's
  `/api/deep-research/start` code path). The supervisor picks between them
  per task instead of a human clicking a specific button.
- **Structured-output mechanism:** Anthropic native tool-use. One real tool
  per action type, called with `tool_choice: {"type": "any"}` so the model
  is always forced to call one of the tools — never free text. This is a
  strict upgrade over the JSON-in-markdown-fence pattern already used
  elsewhere in `routes/ai.py` (e.g. `populate_competitor`), not a
  continuation of it.
- **`FILE_ANALYSIS` prompt:** a new, minimal, self-contained backend prompt
  built from the task's `objective`/`entities`/`expected_output` fields —
  deliberately NOT a port of the rich client-side Phase 1-7 prompt
  templates in `static/app.js` (those remain entirely client-side and
  human-triggered, per the existing architecture documented in CLAUDE.md).
  Porting those server-side is explicitly out of scope for this phase.

## 3. Data Model

One new, purely additive migration: `db/migrations/0004_agent_decisions_work_items.sql`.

```sql
ALTER TABLE agent_decisions ADD COLUMN research_work_item_id INTEGER REFERENCES research_work_items(id);
```

The existing `agent_decisions.research_task_id` column (FK to the legacy,
Phase-1 `research_tasks` table) is untouched and unused by this phase —
same "new column, don't touch the old one" pattern Phase 2 already used
successfully for `research_runs`/`artefacts`. `decision_type` stores the
action name (e.g. `"DISPATCH_TASK"`); `detail` stores a JSON blob with the
full tool input plus the execution outcome. No other schema change.

The `research_runs.research_work_item_id` column already exists from Phase
2 but nothing populates or queries it yet — this phase is the first to use
it. No signature change to `research_runs_repo.create` is needed for this:
`DISPATCH_TASK`'s handler calls the existing generic
`research_runs_repo.update(run_id, research_work_item_id=task_id, ...)`
immediately after `create()`, the same way every other optional run field
is already set today.

The same migration also adds one more nullable column needed by
`DISPATCH_TASK` (§4.3): `FILE_ANALYSIS` runs synchronously via Claude, not
via OpenAI's `response_id`-based polling, so there's nowhere today to store
its output text. `research_runs` gains:

```sql
ALTER TABLE research_runs ADD COLUMN output_text TEXT;
```

Full migration file, both changes together:

```sql
ALTER TABLE agent_decisions ADD COLUMN research_work_item_id INTEGER REFERENCES research_work_items(id);
ALTER TABLE research_runs ADD COLUMN output_text TEXT;
```

## 4. Action Vocabulary

Nine actions, each a real Anthropic tool (`name` + `description` +
`input_schema`), each mapping onto Phase 2's existing status machine rather
than inventing new states. All `task_id` fields refer to
`research_work_items.id` (integer), never the string slugs in the user's
illustrative example.

**Execution safety rule (binding for every handler below):** a handler may
read directly from any repository module when no validation is needed —
exactly how `research_task_service.compute_phase_rollup` already reads
`research_work_items_repo` directly for a report. But any handler that
*mutates* work-item or run state must go through the existing validated
service layer (`research_task_service.transition_task`/`create_task`/
`add_dependency`/`update_fields` for work items;
`research_run_service.create_run`/`update_run` for runs) — never
`research_work_items_repo`/`research_runs_repo` directly for a write. This
is what "prevent the supervisor from modifying arbitrary application state
directly" means concretely: the supervisor's tools are a thin dispatch
layer over functions that were already safe to call from a human-triggered
route, not a new, separately-trusted write path.

### 4.1 `propose_tasks`

Creates one or more new `research_work_items` (status `PROPOSED`),
optionally wiring dependencies both to existing tasks and between tasks in
the same batch.

```json
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
            "depends_on_batch_indices": {"type": "array", "items": {"type": "integer"}, "description": "Zero-based indices into this same tasks array."}
          },
          "required": ["phase_key", "title"]
        }
      },
      "reason": {"type": "string", "description": "Why these tasks are needed now."}
    },
    "required": ["tasks", "reason"]
  }
}
```

**Handler:** creates all tasks first (collecting the batch's new real ids in
array order), then applies `depends_on_existing_ids` and
`depends_on_batch_indices` (resolved against the just-created ids) via
`research_task_service.add_dependency`. A cycle or cross-project violation
raises `ValueError` exactly as it does for a human-triggered call — caught
by the route, recorded as a failed decision, not a 500.

### 4.2 `mark_ready`

```json
{
  "name": "mark_ready",
  "description": "Transition a PROPOSED or FOLLOW_UP_REQUIRED task to READY, making it eligible for dispatch. Only legal if all of its dependencies are COMPLETE or SKIPPED -- the backend re-checks this regardless of what you believe.",
  "input_schema": {
    "type": "object",
    "properties": {
      "task_id": {"type": "integer"},
      "reason": {"type": "string"}
    },
    "required": ["task_id", "reason"]
  }
}
```

**Handler:** `research_task_service.transition_task(task_id, "READY")`.

### 4.3 `dispatch_task`

```json
{
  "name": "dispatch_task",
  "description": "Transition a READY task to RUNNING and start the chosen research method. FILE_ANALYSIS runs synchronously against the project's uploaded source files and completes before this call returns. TARGETED_WEB starts an async web-research job -- its outcome is reviewed on a LATER supervisor call via review_outcome, not this one.",
  "input_schema": {
    "type": "object",
    "properties": {
      "task_id": {"type": "integer"},
      "research_method": {"type": "string", "enum": ["FILE_ANALYSIS", "TARGETED_WEB"]},
      "reason": {"type": "string", "description": "Why this method was chosen over the other."}
    },
    "required": ["task_id", "research_method", "reason"]
  }
}
```

**Handler** (uses `research_run_service`, not `research_runs_repo`
directly — it already owns run-id generation and project-ownership
checks, same principle as every other handler using
`research_task_service` instead of `research_work_items_repo`):
1. `research_task_service.transition_task(task_id, "RUNNING")`.
2. `run_id = research_run_service.create_run(project_name,
   response_id=None, chat_id=None, prompt_text=<task objective>)`, then
   `research_run_service.update_run(project_name, run_id,
   research_work_item_id=task_id)` — `update_run`'s existing `**fields`
   passthrough already supports arbitrary column names it doesn't
   explicitly map (§3's new column included), no change to
   `research_run_service` needed for this call.
3. `FILE_ANALYSIS`: builds the minimal backend prompt (§2) from the task's
   `objective`/`entities`/`expected_output` and the project's currently
   selected/linked source files, calls `llm_service.prompt_completion`
   synchronously, then `research_run_service.update_run(project_name,
   run_id, status="completed", output_text=<result>,
   completed_at=<now>)` — the same `output_text` column added in §3 — and
   returns.
4. `TARGETED_WEB`: calls `openai_service.start_deep_research` exactly as
   `/api/deep-research/start` does today, then
   `research_run_service.update_run(project_name, run_id,
   response_id=<id>)` (status stays `"running"`), and returns immediately.
   A *separate*, pre-existing mechanism (the browser's own deep-research
   polling, already wired to `/api/deep-research/status/<id>`) continues to
   advance that run's status in the background exactly as it does for
   human-triggered runs today — this phase does not change that polling
   mechanism at all.

### 4.4 `review_outcome`

```json
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
      "reason": {"type": "string"}
    },
    "required": ["task_id", "outcome", "reason"]
  }
}
```

**Handler:** looks up the task's most recent run (new repo function,
§5). If no run exists, or the run's `status` is still `running`, returns a
failed execution result (`"error": "No finished run to review"`) rather
than letting the model force a transition against no evidence. Otherwise:
`research_task_service.set_review_scores(task_id, completeness_score=...,
evidence_score=..., identified_gaps=...)` (§5), then branches on `outcome`:
- `outcome` is `COMPLETE` or `FOLLOW_UP_REQUIRED`: transition
  `RUNNING→REVIEWING`, then `REVIEWING→outcome` (both legal per Phase 2's
  table). Passing through `REVIEWING` here is a deliberate choice, not
  required by the state machine (`RUNNING→COMPLETE` is also directly
  legal) — it makes "this outcome was reviewed" a real, visible point in
  the task's history rather than an instantaneous skip.
- `outcome` is `FAILED`: transition `RUNNING→FAILED` directly. This is
  **not** a style choice — Phase 2's transition table has no
  `REVIEWING→FAILED` edge at all (`REVIEWING`'s only legal destinations are
  `COMPLETE`, `FOLLOW_UP_REQUIRED`, `WAITING_FOR_HUMAN`), so routing a
  `FAILED` outcome through `REVIEWING` first would make the transition
  illegal and raise `ValueError`. The handler must check `outcome` before
  choosing which transition sequence to run, not transition to `REVIEWING`
  unconditionally first.

### 4.5 `create_followup_task`

Matches the user's example schema shape most closely.

```json
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
      "reason": {"type": "string"}
    },
    "required": ["task_id", "followup_title", "reason"]
  }
}
```

**Handler:** `transition_task(task_id, "FOLLOW_UP_REQUIRED")`, creates the
new task via `create_task` (same `phase_key` as the original), then
`add_dependency(task_id, new_task_id)` so the state machine's existing
dependency guard naturally blocks the original from going `READY` again
until the follow-up resolves.

### 4.6 `request_human_review`

```json
{
  "name": "request_human_review",
  "description": "Flag a task as requiring human judgment before it can be marked complete.",
  "input_schema": {
    "type": "object",
    "properties": {
      "task_id": {"type": "integer"},
      "reason": {"type": "string"}
    },
    "required": ["task_id", "reason"]
  }
}
```

**Handler:** `research_task_service.flag_for_human_review(task_id)` (§5 —
today only `create_task` sets this flag, at creation time; this is the
first caller that sets it retroactively). If the task's current
status is `REVIEWING`, also calls
`transition_task(task_id, "WAITING_FOR_HUMAN")` (legal per Phase 2's
table, and only reachable when the flag is set — so the handler must set
the flag first, in the same call, before attempting the transition). If
the task is in an earlier status (e.g. still `RUNNING`), only the flag is
set now; the existing `REVIEWING→COMPLETE`/`→WAITING_FOR_HUMAN` guard
(already implemented in Phase 2) naturally honors it once the task gets
there.

### 4.7 `skip_task`

```json
{
  "name": "skip_task",
  "description": "Abandon a task permanently. Use when it's no longer useful to pursue.",
  "input_schema": {
    "type": "object",
    "properties": {
      "task_id": {"type": "integer"},
      "reason": {"type": "string"}
    },
    "required": ["task_id", "reason"]
  }
}
```

**Handler:** `transition_task(task_id, "SKIPPED")`.

### 4.8 `trigger_synthesis`

```json
{
  "name": "trigger_synthesis",
  "description": "Request final Phase 7 (Options for OES) synthesis across all prior phases. Only call this when you believe every phase has sufficient completed research. The backend will verify this independently and reject the request if prerequisites are not actually met.",
  "input_schema": {
    "type": "object",
    "properties": {
      "reason": {"type": "string"}
    },
    "required": ["reason"]
  }
}
```

**Handler — backend-gated, not LLM-trusted.** Checks a deterministic
prerequisite: every key in `PHASE_DEFINITIONS` (phases `"1"` through
`"6"` — phase `"7"` is the synthesis itself, excluded from its own
prerequisite) has at least one `research_work_items` row with
`status="COMPLETE"` for this project. If not satisfied: raises
`ValueError("Phases missing completed research: <list>")` — caught by the
same orchestration catch-point as every other handler's illegal-state
error (§7), logged as a rejected decision, not executed, not a crash.

**Resolved during plan-writing (the existing Phase 7 path is a streaming
SSE route, not something callable synchronously without either
refactoring `routes/ai.py` or inventing new streaming-consumer plumbing —
both avoided):** if satisfied, this handler does **not** call into the
existing `/api/chat` route. It follows the same pattern as `dispatch_task`'s
`FILE_ANALYSIS` case (§4.3, §2) — a new, minimal, non-streaming backend
prompt, built from the phases 1-6 summaries already available via
`insights_service.load_current_insights`, sent through
`llm_service.prompt_completion` synchronously. The result is written back
via the *existing*, already-safe `insights_service.save_insights` (merging
into `phases["7"]`), so the next time a human opens the Insights tab the
Phase 7 card shows the supervisor's synthesis exactly as if a human had
generated it — just via a plainer prompt than the rich one the "Generate"
button on that card uses. `routes/ai.py` is not modified by this phase.

### 4.9 `no_action`

```json
{
  "name": "no_action",
  "description": "Nothing useful can be done right now (e.g. all eligible tasks are already running, or everything is blocked on an async result). Use this instead of forcing an action that doesn't make sense.",
  "input_schema": {
    "type": "object",
    "properties": {
      "reason": {"type": "string"}
    },
    "required": ["reason"]
  }
}
```

**Handler:** no-op. Still recorded as a decision — "the supervisor
correctly determined there was nothing to do" is meaningful, inspectable
history, not noise to discard.

## 5. Service and Repository Additions

**`services/research_task_service.py`** gains two small functions — needed
so `review_outcome` (§4.4) and `request_human_review` (§4.6) can mutate
work-item fields without violating §4's execution-safety rule by calling
`research_work_items_repo.update_fields` directly:

```python
def set_review_scores(task_id, completeness_score=None, evidence_score=None, identified_gaps=None):
    fields = {}
    if completeness_score is not None:
        fields["completeness_score"] = completeness_score
    if evidence_score is not None:
        fields["evidence_score"] = evidence_score
    if identified_gaps is not None:
        fields["identified_gaps_json"] = json.dumps(identified_gaps)
    research_work_items_repo.update_fields(task_id, **fields)


def flag_for_human_review(task_id):
    research_work_items_repo.update_fields(task_id, human_review_required=1)
```

These are the only two new functions added to this file in Phase 3 — the
transition table and `ALLOWED_TRANSITIONS` itself (Phase 2) are not
touched.

**`db/repositories/research_runs_repo.py`** gains one new function:

```python
def find_latest_for_work_item(work_item_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM research_runs WHERE research_work_item_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (work_item_id,),
        ).fetchone()
```

**`db/repositories/agent_decisions_repo.py`** gains one new parameter and
one new query function:

```python
def record(project_id, decision_type, detail, research_task_id=None, research_work_item_id=None):
    # existing INSERT, with the new column added
    ...

def list_for_project(project_id, limit=50):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM agent_decisions WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
```

## 6. Context Builder

One new function, `services/supervisor_service.py::build_context(project_id)`,
assembles everything Claude sees for a decision, as a single structured
text block (following the existing `_build_files_context` clipping
convention in `routes/ai.py` so an old, decision-heavy project can't blow
the context window):

- Project objective (`load_project_prompt(project, "project_prompt")`).
- Per-phase state from the *existing* insights mechanism
  (`insights_service.load_current_insights` — `summary`/`confidence` per
  phase) — read-only context, "what's already been established." The
  supervisor never writes to this system.
- Every `research_work_items` row for the project (via
  `research_work_items_repo.list_for_project`): id, phase_key, title,
  status, priority, scores, gaps, retry_count/max_retries,
  human_review_required, its dependencies (via `list_dependencies`), and
  — if a run exists — that run's status/output via
  `find_latest_for_work_item`.
- The last 10 `agent_decisions` for this project (via the new
  `list_for_project`), so the supervisor has continuity and doesn't repeat
  a rejected or already-handled decision.
- The 7 fixed phase definitions (`services/phases.py::PHASE_DEFINITIONS`),
  so the supervisor knows the full universe of phases even for ones with
  no tasks yet.

## 7. Orchestration — `run_supervisor_cycle(project_name)`

The single entry point, called once per `POST /api/supervisor/run`:

1. Resolve `project_id`, build context (§6).
2. Call Claude with all nine tools (§4) and
   `tool_choice={"type": "any"}`, system prompt describing the supervisor's
   role and the explicit instruction to call exactly one tool.
3. Extract the single `tool_use` content block (Anthropic's response may
   include preceding text; only the tool call matters here — if the model
   somehow returns no tool_use block despite `tool_choice: "any"`, that's
   treated as a hard error surfaced to the UI, not silently retried).
4. Dispatch to the matching handler (§4's "Handler" descriptions) inside a
   `try/except ValueError` — a `ValueError` from the service layer (e.g. an
   illegal transition, a cycle, a missing dependency) becomes
   `{"success": False, "error": str(exc)}`, not a 500.
5. Record the decision: `agent_decisions_repo.record(project_id,
   decision_type=tool_name, detail=json.dumps({"input": tool_input,
   "execution": execution_result}), research_work_item_id=the task_id if
   the action has one else None)`.
6. Return `{"decision": {"action": tool_name, "input": tool_input},
   "execution": execution_result}` to the caller.

## 8. Routes

New blueprint, `routes/supervisor.py`:

- `POST /api/supervisor/run` — body `{project}`. Runs one cycle (§7),
  returns its result. 400 if no project. A caught exception from the LLM
  call itself (network error, API error) returns
  `{"success": False, "error": str(exc)}`, matching the existing
  exception-handling convention already used throughout `routes/ai.py`.
- `GET /api/supervisor/decisions?project=` — returns
  `{"decisions": [...]}`, each with `id`, `decision_type`, `detail`
  (parsed JSON), `research_work_item_id`, `created_at` — for the UI
  inspector.

## 9. UI

Additive only, same pattern as Phase 2's task panel (per the global
constraint: preserve the existing Phase 1-7 presentation layer). A new
"Supervisor" section in the Insights tab, below the phase cards: a "Run
Supervisor" button and a decision-log list underneath showing, per
decision, the action name, a human-readable one-line summary built from
the tool input (e.g. "Dispatched task #14 (Product / La Trobe) via
TARGETED_WEB"), the model's stated reason, and the execution
outcome (success/error, with the error message if any). This directly
satisfies "make decisions inspectable from the UI" — the log is the
primary way a human verifies what the supervisor has been doing, not a
side effect of querying the database.

## 10. Testing Strategy

- `tests/test_db_migrations.py` — migration 0004 applies; `agent_decisions`
  gains `research_work_item_id`; `research_runs` gains `output_text`.
- `tests/test_repositories_agent_decisions.py` (new) — `record` with and
  without `research_work_item_id`; `list_for_project` ordering/limit.
- `tests/test_repositories_research_runs.py` — add a test for
  `find_latest_for_work_item` (most-recent-first, correctly scoped to one
  work item).
- `tests/test_supervisor_service.py` (new, the bulk of this phase's test
  coverage) — one test per handler using a **fake/stubbed LLM response**
  (a hand-constructed tool-use payload, not a real Anthropic call) so
  behavior is tested deterministically:
  - `propose_tasks` creates tasks and wires both existing-id and
    batch-index dependencies correctly.
  - `mark_ready` respects the dependency guard (blocked case included).
  - `dispatch_task` for `FILE_ANALYSIS` creates a completed run
    synchronously (LLM call itself mocked) and transitions to `RUNNING`
    then leaves it there (review is a separate call, per §4.3/§4.4's
    split).
  - `dispatch_task` for `TARGETED_WEB` creates a running run (OpenAI call
    mocked) and does not attempt to review it.
  - `review_outcome` rejects a task with no finished run; succeeds for
    `COMPLETE`, `FOLLOW_UP_REQUIRED`, and `FAILED` outcomes, transitioning
    through the correct path for each (confirming `FAILED` goes directly
    from `RUNNING`, not through `REVIEWING`).
  - `create_followup_task` transitions the original to
    `FOLLOW_UP_REQUIRED`, creates the new task, and wires the dependency
    such that the original cannot go `READY` until the new one resolves.
  - `request_human_review` sets the flag; transitions to
    `WAITING_FOR_HUMAN` only when current status is `REVIEWING`, only sets
    the flag otherwise.
  - `skip_task` transitions to `SKIPPED`.
  - `trigger_synthesis` — the prerequisite gate rejects when any of phases
    1-6 lacks a `COMPLETE` task, and (with a mocked Phase-7 call) succeeds
    when all do.
  - `no_action` records a decision and performs no state change.
  - Every handler's outcome round-trips through `agent_decisions_repo` —
    assert the row exists with correct `decision_type`/
    `research_work_item_id` after each call.
- `tests/test_routes_supervisor.py` (new) — route-level tests with the LLM
  call mocked at the `anthropic` client boundary (following this
  codebase's existing convention of not making real external API calls in
  tests): a full `POST /api/supervisor/run` round trip for at least one
  action, plus the `ValueError`-from-service → `400`/`error` path, plus
  `GET /api/supervisor/decisions` listing.
- Full existing suite re-run at the end (242 as of Phase 2's merge) to
  confirm zero regression — this phase must not touch
  `research_tasks`/`findings`/`evidence`/`project_insight_versions`, and
  must not modify Phase 2's `research_task_service.py` transition table
  itself (only append the two small functions in §5).

## 11. Explicitly Out of Scope (this phase)

- Background scheduling or auto-fire on task/run completion — the
  supervisor only runs when explicitly invoked via the button.
- Multi-decision looping within one click, and therefore no runaway-loop
  guard (nothing to guard against yet).
- Porting the rich client-side Phase 1-7 prompt templates
  (`static/app.js`) to the backend for `FILE_ANALYSIS` — this phase uses a
  new, minimal, backend-only prompt instead (§2).
- Any change to the existing human-triggered chat/deep-research UI flows —
  the supervisor is an additional, parallel way to dispatch research, not
  a replacement.
- Any change to `research_tasks`, `findings`, `evidence`,
  `project_insight_versions`, or the versioned-insights-history mechanism.
- Any change to `db/migrate.py`/`db/migrate_runner.py`.
- A UI for editing/retrying a past decision — the log is read-only
  (inspection), not an undo/redo mechanism.
