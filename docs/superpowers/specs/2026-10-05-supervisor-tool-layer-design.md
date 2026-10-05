# Phase 4a: Supervisor tool layer — Design

**Date:** 2026-10-05
**Status:** Approved in conversation; awaiting written-spec review
**Roadmap:** Phase 4, part a. Part b (new capabilities) gets its own spec later.
**Builds on:** Research Board (`2026-10-05-research-board-design.md`), Phase 3 Supervisor, Phase 2 task state.

## 1. Goal

Every application action the Research Supervisor or the user's board buttons can take goes through **one registry of explicit tools**, each with checked inputs, structured results, a list of allowed callers, clean failures and a log entry. The Supervisor can only act through the tools on its menus.

**Acceptance criterion (from the roadmap):** supervisor behaviour is constrained to explicitly supported application actions — enforced by the registry and proven by tests.

### Decisions already made (by the user)

| Topic | Decision |
|---|---|
| Approval | Tools that spend money or change what runs (`launch_deep_research`, `update_task_status`, `generate_synthesis`; later `run_targeted_search`) are **not** callable by the Supervisor. They stay behind the user's buttons. |
| Changing later | Must be easy: each tool's allowed callers is one list; moving a tool to the Supervisor is a one-line change. |
| Scope | 4a = the tool layer + the ten tools that wrap existing behaviour. 4b = `run_targeted_search`, `search_existing_evidence`, `save_source`, `save_evidence`, `create_finding`, `query_powerbi`. |
| Approach | One tool layer everything goes through (not a Supervisor-only gateway, not decorators). |

### Out of scope

- Everything in 4b (above).
- A multi-step Supervisor loop (the Supervisor calling read tools itself). It still makes one decision per call.
- Any change to the review limits in `docs/TO-TEST.md` (`MAX_RUN_OUTPUT_REVIEW_CHARS`, `MAX_REVIEW_OUTPUT_TOTAL_CHARS`, `MAX_CONTEXT_CHARS`, `MAX_WEB_SOURCES_LISTED`).
- Any visible change to the Research tab.

## 2. The tool layer

New package `services/tools/`.

### 2.1 A tool

Each tool is registered once with:

- **name** and **description** (the description is what Claude reads);
- **input model** — a pydantic model (pydantic is already installed via the Anthropic/OpenAI SDKs; it becomes an explicit pinned entry in `requirements_flask.txt`). The model's JSON schema is what the Supervisor is given as the tool's `input_schema`, so the two cannot drift;
- **result** — success with structured data, or a failure (§2.3);
- **allowed callers** — a set drawn from `supervisor`, `user` (board buttons), `system` (board refresh and internal steps);
- **handler** — calls existing services; never the database connection, raw SQL, or files directly (§5).

**List fields accept a single string.** The live Supervisor has sent a bulleted string where an array was asked for (`identified_gaps`, `focus`). Input models coerce a string into a list by splitting lines and stripping bullet markers (same rule as `supervisor_service.as_text_list`), so this never fails validation.

### 2.2 One door: `run_tool(name, caller, project_name, inputs, parent_call_id=None)`

In order:
1. Unknown tool → `not_allowed`. Caller not in the tool's allowed callers → `not_allowed`. Nothing runs.
2. Validate `inputs` with the input model → `invalid_input` naming the field(s) and why.
3. Check every card/run id in the inputs belongs to `project_name` → otherwise `not_found` (does not reveal that the id exists elsewhere).
4. Run the handler.
5. Expected failures raised by the handler keep their code and plain message. Any other exception → `internal` with the message "Something went wrong running <tool>"; the exception text goes to the log only.
6. Log the call (§2.4) — always, including refusals.
7. Return the result.

A handler that needs another tool (e.g. `evaluate_research_output` creating a follow-up) calls `run_tool` with its own call id as `parent_call_id`, so child calls are validated and logged the same way.

### 2.3 Results and failure codes

`ToolResult`: `{ok: true, data: {...}}` or `{ok: false, error: {code, message, fields?}}`.

| Code | Meaning | HTTP (board routes) |
|---|---|---|
| `not_allowed` | unknown tool, or caller not allowed | 403 |
| `invalid_input` | inputs failed validation | 400 |
| `not_found` | card/run not in this project | 404 |
| `conflict` | card in the wrong state, or changed meanwhile | 409 |
| `unavailable` | Claude/OpenAI unreachable or erroring, drafting unavailable | 503 |
| `internal` | anything unexpected | 500 |

The board's existing messages for 400/404/409/503 keep their wording.

### 2.4 Logging — migration `0006` (additive)

```sql
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

Logged inputs and results are **clipped**: each string value to 500 characters, each JSON document to 4,000 characters. Reports, prompts and anything secret-shaped therefore never land whole in the log. For an internal error the log also keeps the exception type and message (clipped).

The Activity feed is unchanged: it keeps reading `agent_decisions`. `tool_calls` is the detailed audit trail.

## 3. The ten tools in 4a

| Tool | Supervisor | User | System |
|---|---|---|---|
| `get_project_state` | ✓ | ✓ | ✓ |
| `get_task` | ✓ | ✓ | ✓ |
| `create_research_task` | ✓ | ✓ | |
| `create_followup_task` | ✓ | ✓ | |
| `request_human_review` | ✓ | | |
| `evaluate_research_output` | ✓ | | |
| `update_task_status` | | ✓ | ✓ |
| `launch_deep_research` | | ✓ | |
| `check_research_run` | | | ✓ |
| `generate_synthesis` | | ✓ | |
| `no_action` (Supervisor's explicit "nothing to do") | ✓ | | |

### 3.1 Inputs and outputs

- **`get_project_state`** — inputs: none. Output: objective; phases (key, title, frameworks); every card (id, phase, title, status, method, priority, focus, rationale, scores, gaps, retries, dependencies, latest run status); existing Insights phase summaries; Phase 7 readiness; the 10 most recent decisions. Read-only.
- **`get_task`** — `{task_id: int}`. Output: the card as above plus its latest run: status, error, elapsed seconds, `report_excerpt` (clipped to `MAX_RUN_OUTPUT_REVIEW_CHARS`, unchanged) and `report_chars_total`. Read-only.
- **`create_research_task`** — `{tasks: [1..10 of {phase_key: "1".."6", title: 1–200 chars, research_method: TARGETED_WEB|FILE_ANALYSIS, focus: ≤10 items ≤80 chars, rationale: ≤1000 chars, framework_key?: one of the phase's frameworks, priority?: low|medium|high, prompt_text?: 40–30000 chars, depends_on_existing_ids?: [int], depends_on_batch_indices?: [int]}], reason?: ≤1000 chars}`. Everything is validated before anything is created (phase 7 refused; dependency ids must exist in the project; batch indices in range; no loops). Without `prompt_text` the prompt is drafted from the framework (today's behaviour, including the fallback placeholder). Output: `{created_task_ids, drafted_from_framework}`. Cards are always **PROPOSED**.
- **`create_followup_task`** — `{task_id, title, focus?, research_method?, rationale?}`. Creates a PROPOSED card in the same phase with `suggested_from_work_item_id`, prompt drafted from the original's framework. Output: `{followup_task_id}`.
- **`request_human_review`** — `{task_id, reason}`. Flags the card; if it is REVIEWING, moves it to WAITING_FOR_HUMAN. Output: `{task_id, new_status}`.
- **`evaluate_research_output`** — `{task_id, completeness_score: 0–1, evidence_score: 0–1, identified_gaps: [str], outcome: COMPLETE|FOLLOW_UP_REQUIRED|NEEDS_HUMAN|FAILED, reason, followup?: {title, focus?, research_method?, rationale?}}`. The card must be REVIEWING (claimed by the system) → otherwise `conflict`. Records scores and gaps; FOLLOW_UP_REQUIRED with a follow-up calls `create_followup_task`; NEEDS_HUMAN (or COMPLETE on a card already flagged) calls `request_human_review`; then moves the card to the outcome. Output: `{task_id, outcome, new_status, followup_task_id?}`.
- **`update_task_status`** — `{task_id, action}`.
  - User actions: `approve`, `back_to_draft`, `skip`, `restore`, `retry`, `accept`, `mark_failed`, `needs_followup`.
  - System actions: `run_failed` (RUNNING → FAILED when the run failed or was abandoned).
  - Each action keeps today's rules exactly (approve validations and snapshot guard, retry limit, dependency check, atomic claims). An action not allowed for the caller → `not_allowed`.
  - `needs_followup` moves WAITING_FOR_HUMAN → FOLLOW_UP_REQUIRED and calls `create_followup_task`.
  - Output: `{task_id, new_status}`.
- **`launch_deep_research`** — `{task_ids: [1..20 int]}`. Starts web or "my files" research on cards that are still READY (today's `start_runs`); SYNTHESIS cards are refused here. Output: `{started, not_ready, failed: [{id, error}]}`.
- **`check_research_run`** — `{task_id}`. For a web run still in progress: asks OpenAI once and, if finished, stores the report (today's per-run sync). Output: `{task_id, run_status}`.
- **`generate_synthesis`** — `{task_id}`. Starts the Phase 7 options report on a READY SYNTHESIS card (today's synthesis path, background). Output: same shape as `launch_deep_research`.
- **`no_action`** — `{reason}`. Output: `{}`.

## 4. How the Supervisor and the board use the tools

### 4.1 Supervisor menus (fixed)

- **Drafting** ("Draft next researches"): the system calls `get_project_state` and formats the briefing (same information and limits as today's `build_context`; existing `build_context` tests keep passing). Claude is offered only `create_research_task` and `no_action`. Its choice runs through `run_tool(caller="supervisor")`. The decision is recorded in `agent_decisions` as today.
- **Reviewing** (automatic): the system claims the card (internal step, not a tool), calls `get_task` to build the briefing (same 8,000-character view), and forces Claude to call `evaluate_research_output`, which runs through `run_tool(caller="supervisor")`. On failure the claim is released, as today.
- A tool returned by the model that is not on the menu is refused (`not_allowed`), logged, and the decision recorded as failed. Nothing runs.

### 4.2 Board buttons (caller `user`)

| Button | Tool |
|---|---|
| Approve, Back to draft, Skip, Restore, Retry, Accept, Mark failed, Needs follow-up | `update_task_status` |
| Run | `launch_deep_research` for web/"my files" cards; `generate_synthesis` for the options report; results merged into today's response shape |
| + Add research | `create_research_task` (with `prompt_text` when the user wrote their own) |

Edit, Re-draft, Save objective and Draft options report are not on Phase 4's list and stay as board actions. Routes and response shapes are unchanged; `ToolResult` failures map to today's HTTP codes (§2.3).

### 4.3 Board refresh (caller `system`)

For each card: `check_research_run` for in-progress web runs; `update_task_status(run_failed)` when a run failed or was abandoned; report save/link stays an internal refresh step (becomes `save_source` in 4b); then the Supervisor's review (§4.1). The refresh lock, stale-claim release and per-card error isolation are unchanged.

### 4.4 Code that moves

- `supervisor_service.handle_propose_tasks` → `create_research_task`; `_apply_review` → `evaluate_research_output`; `create_followup_card` → `create_followup_task`.
- `board_service` card actions become thin calls to `update_task_status`; `run` calls the launch tools.
- `research_execution_service.sync_web_research_runs` is split so one run can be checked (`check_research_run`).
- The services underneath (`research_task_service`, `research_execution_service`, `prompt_drafting_service`) keep their logic; tools call them.

## 5. Safety rules

1. **No raw access.** No tool input has a field for SQL, a table, a file path or a filename. Inputs are ids, fixed choices and length-limited text.
2. **Project scoping.** Every id is checked against the project before the handler runs (§2.2 step 3).
3. **Import boundary.** Modules in `services/tools/` do not import `db.connection`, `sqlite3`, `os`, `pathlib`, `shutil` or `builtins.open`, and do not call `open(`. A test reads the package's source to enforce this.
4. **Caller lists are the only permission.** No handler checks callers itself; `run_tool` does, from the registry.
5. **Clipped logs** (§2.4).

## 6. Testing

pytest; Anthropic and OpenAI always faked.

- **The door:** unknown tool; caller not allowed (refused, logged, handler not run); invalid inputs (field named); other project's id → `not_found`; expected failure keeps code/message; unexpected exception → `internal` with details only in the log; every call logged; child calls carry `parent_call_id`; string-for-list coercion.
- **Each tool:** at least one success, one invalid-input and one wrong-state (or not-found) case. Tools that spend money or change what runs: the Supervisor is refused.
- **Menus (acceptance criterion):** every tool on the drafting and review menus allows `supervisor`; every other tool and every `update_task_status` action refuses `supervisor`; a model reply naming an off-menu tool runs nothing and records a failed decision.
- **Structure:** the import-boundary test (§5.3); no input model has a field named or described as a path, filename, SQL or table.
- **Logging:** clipping of long strings and long JSON.
- **Regression:** all existing tests pass, including board routes (same status codes and response shapes) and `build_context` tests.
- **Browser:** the stubbed 27-step Research tab walkthrough, re-run at the end.

## 7. Build order

1. Migration 0006 and the `tool_calls` repository.
2. The tool framework: registry, `run_tool`, results, codes, clipping, string-for-list coercion; pin pydantic.
3. Read tools: `get_project_state`, `get_task`.
4. Card tools: `create_research_task`, `create_followup_task`, `request_human_review`, `no_action`.
5. `evaluate_research_output`, and the Supervisor's drafting and review switched to menus over the registry.
6. `update_task_status`, `launch_deep_research`, `check_research_run`, `generate_synthesis`; board buttons and refresh switched to the tools.
7. Structural tests (import boundary, input fields, menus), CLAUDE.md note, browser walkthrough.
