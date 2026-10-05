# Research Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Prompt Developer tab with a Research tab where the Supervisor drafts framework-built research cards, the user approves and runs them, and finished research is reviewed automatically and saved as phase-linked sources.

**Architecture:** Server first. A new migration adds prompt/rationale fields; `research_task_service` gains the new lifecycle edges and atomic "claims"; frameworks move to `services/prompt_frameworks.json`; a new `research_execution_service` owns starting runs and saving reports (user-triggered only); `supervisor_service` is cut down to drafting and reviewing; `board_service` + `routes/board.py` expose the board. The UI is a new `static/board.js` whose render functions are pure and tested with Node. The old tab and panels are removed last.

**Tech Stack:** Python 3.13, Flask, SQLite (`db/`), pytest, Anthropic SDK (tool use), OpenAI Responses API (deep research), vanilla JS, Node 24 (tests only).

**Spec:** `docs/superpowers/specs/2026-10-05-research-board-design.md` (mockup: `docs/mockups/research-board.html`)

**Refinements to the spec made while planning (deliberate):**
- `research_runs.report_linked_at` is added in migration 0005, so a report is linked to its phase **once**. If the user later unlinks it, it is not re-linked.
- Report files are saved only for runs the board started (`research_runs.prompt_text` is set). Older Phase 3 runs never produce report files.
- A fallback prompt carries a marker line, and Approve refuses any prompt containing it. That's how "Couldn't draft from the framework" stays visible without a new column.
- `PUT /api/board/objective` saves the objective from the board.
- Every card action endpoint returns the fresh board state.
- A "My files" or options-report run that has stayed `running` for over 30 minutes is failed on refresh. The app may have restarted mid-run.
- File analysis uses `max_tokens=8000`, not 3000, because framework prompts ask for long structured reports.

## Global Constraints

- Migrations are additive only. Never edit `0001`–`0004`. The new file is `db/migrations/0005_research_board.sql`.
- Do not change the values of `MAX_RUN_OUTPUT_REVIEW_CHARS = 8000`, `MAX_REVIEW_OUTPUT_TOTAL_CHARS = 24000`, `MAX_CONTEXT_CHARS = 60000` or `MAX_WEB_SOURCES_LISTED = 20`. These are the subject of the user's own experiment in `docs/TO-TEST.md`.
- The Supervisor must have **no** code path that moves a card to `READY` or `RUNNING`. Only `board_service` user actions and `research_execution_service.start_runs` do that.
- Tests never call Anthropic or OpenAI. Patch `services.llm_service.prompt_completion`, `supervisor_service.anthropic.Anthropic`, and `research_execution_service.openai_service.start_deep_research` / `retrieve_deep_research`.
- Any test that creates a project, reads or writes `project_prompt`, or saves files must call `monkeypatch.chdir(tmp_path)` first. `projects/` is resolved relative to the current directory.
- Bash on Windows: the repo path contains `!!`. Start bash commands with `set +H;` or use PowerShell.
- `python -m ruff check .` must pass. Don't add any new Python or JS dependencies.
- User-facing text is plain, sentence-case English with no jargon. Status labels come verbatim from the spec §2 table.
- Run the full suite with `python -m pytest tests/ -q`. The baseline before Task 1 is 338 passed.

## Review Focus

1. **The old browser poller picking up board runs.** `resumeActiveRuns()` in `app.js` polls every non-terminal research run. On completion it saves a duplicate Artifact and marks the run complete without its text. It must skip runs that have a `research_work_item_id` (Task 4 adds the field; Task 10 adds the skip).
2. **Cards created before the board.** Phase 3 cards can be `READY` with no `prompt_text` or method. Run must refuse them with a clear message and leave them `READY`, rather than sending an empty prompt (Task 4).
3. **Report titles with characters Windows forbids** (`Fees: 2025/26?`). Saving must still produce a valid filename (Task 5).
4. **Insights generation running while the board links a report.** The browser later saves its older in-memory insights over the link. Refresh with `defer_linking` must leave the report unlinked and link it on a later refresh (Task 7). The Insights tab reloads on show when the board has linked something (Task 10).
5. **Timezones.** The browser stores `generated_at` as UTC (`…Z`), while the server stores naive local time. The "summaries are older than your newest research" check must compare them correctly (Task 7).

---

### Task 1: Migration 0005 and repository support

**Files:**
- Create: `db/migrations/0005_research_board.sql`
- Modify: `db/repositories/research_work_items_repo.py`
- Test: `tests/test_db_migrations.py`, `tests/test_repositories_research_work_items.py`

**Interfaces:**
- Produces: `research_work_items_repo.create(..., prompt_text=None, framework_key=None, rationale=None, suggested_from_work_item_id=None)`. Also produces `research_work_items_repo.claim_status(id, from_status, to_status) -> bool`. New columns: `research_work_items.prompt_text|framework_key|rationale|suggested_from_work_item_id` and `research_runs.prompt_text|report_stable_file_id|report_linked_at`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_db_migrations.py`, change `assert count == 4` to `assert count == 5`, then append:

```python
def test_research_board_columns(temp_db):
    with get_connection() as conn:
        item_cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_work_items)").fetchall()}
        run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_runs)").fetchall()}
    assert {"prompt_text", "framework_key", "rationale", "suggested_from_work_item_id"}.issubset(item_cols)
    assert {"prompt_text", "report_stable_file_id", "report_linked_at"}.issubset(run_cols)
```

Append to `tests/test_repositories_research_work_items.py` (make sure `projects_repo` and `research_work_items_repo` are imported at the top):

```python
def test_create_stores_board_fields(temp_db):
    pid = projects_repo.get_or_create_id("P")
    origin = research_work_items_repo.create(pid, "4", "Origin")
    wid = research_work_items_repo.create(
        pid, "4", "Follow-up", prompt_text="Find the fees", framework_key="oes-product-features",
        rationale="Fees were missing", suggested_from_work_item_id=origin,
    )
    row = research_work_items_repo.get(wid)
    assert row["prompt_text"] == "Find the fees"
    assert row["framework_key"] == "oes-product-features"
    assert row["rationale"] == "Fees were missing"
    assert row["suggested_from_work_item_id"] == origin


def test_claim_status_moves_only_from_expected_status(temp_db):
    pid = projects_repo.get_or_create_id("P")
    wid = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(wid, status="READY")
    assert research_work_items_repo.claim_status(wid, "READY", "RUNNING") is True
    assert research_work_items_repo.get(wid)["status"] == "RUNNING"


def test_claim_status_second_claim_fails(temp_db):
    pid = projects_repo.get_or_create_id("P")
    wid = research_work_items_repo.create(pid, "4", "Task")
    research_work_items_repo.update_fields(wid, status="READY")
    assert research_work_items_repo.claim_status(wid, "READY", "RUNNING") is True
    assert research_work_items_repo.claim_status(wid, "READY", "RUNNING") is False
    assert research_work_items_repo.get(wid)["status"] == "RUNNING"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_db_migrations.py tests/test_repositories_research_work_items.py -v`
Expected: FAIL. The count is 4, the columns are missing, and `create()` gets an unexpected keyword, and `claim_status` doesn't exist.

- [ ] **Step 3: Write the migration**

`db/migrations/0005_research_board.sql`:

```sql
-- Research Board (2026-10-05). Fully additive: seven new nullable columns.
-- See docs/superpowers/specs/2026-10-05-research-board-design.md section 3.

ALTER TABLE research_work_items ADD COLUMN prompt_text TEXT;
ALTER TABLE research_work_items ADD COLUMN framework_key TEXT;
ALTER TABLE research_work_items ADD COLUMN rationale TEXT;
ALTER TABLE research_work_items ADD COLUMN suggested_from_work_item_id INTEGER REFERENCES research_work_items(id);

-- The full prompt as sent (prompt_preview is clipped to 200 characters), the report
-- file saved from the run's output, and when that file was linked to its phase.
ALTER TABLE research_runs ADD COLUMN prompt_text TEXT;
ALTER TABLE research_runs ADD COLUMN report_stable_file_id TEXT;
ALTER TABLE research_runs ADD COLUMN report_linked_at TEXT;
```

- [ ] **Step 4: Extend the repository**

In `db/repositories/research_work_items_repo.py`, replace `create` and add `claim_status` after `update_fields`:

```python
def create(
    project_id, phase_key, title, objective=None, priority=None, research_method=None,
    entities_json=None, expected_output=None, source_requirements_json=None,
    human_review_required=0, max_retries=3, prompt_text=None, framework_key=None,
    rationale=None, suggested_from_work_item_id=None,
):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO research_work_items "
            "(project_id, phase_key, title, objective, priority, research_method, "
            " entities_json, expected_output, source_requirements_json, "
            " human_review_required, max_retries, prompt_text, framework_key, rationale, "
            " suggested_from_work_item_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id, phase_key, title, objective, priority, research_method,
                entities_json, expected_output, source_requirements_json,
                human_review_required, max_retries, prompt_text, framework_key, rationale,
                suggested_from_work_item_id, now, now,
            ),
        )
        return cur.lastrowid
```

```python
def claim_status(id, from_status, to_status):
    """Move a work item from one status to another in a single statement.

    Returns True only if this call made the change, so two requests racing for the
    same card (a double-clicked Run, two open tabs reviewing) cannot both win.
    """
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE research_work_items SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (to_status, now, id, from_status),
        )
        return cur.rowcount == 1
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `python -m pytest tests/test_db_migrations.py tests/test_repositories_research_work_items.py -v` (expected: PASS). Then run `python -m pytest tests/ -q` (expected: everything passes).

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0005_research_board.sql db/repositories/research_work_items_repo.py tests/test_db_migrations.py tests/test_repositories_research_work_items.py
git commit -m "Add migration 0005 and atomic status claims for the Research Board"
```

---

### Task 2: Lifecycle edges, card fields and Phase 7 readiness

**Files:**
- Modify: `services/research_task_service.py`
- Test: `tests/test_research_task_service.py`

**Interfaces:**
- Consumes: `research_work_items_repo.create(... new kwargs)` and `claim_status` (Task 1).
- Produces:
  - `create_task(..., prompt_text=None, framework_key=None, rationale=None, suggested_from_work_item_id=None)`.
  - `claim_transition(work_item_id, from_status, to_status) -> bool`. It raises `ValueError` if the edge isn't in `ALLOWED_TRANSITIONS`.
  - `phase7_readiness(project_id) -> {"ready_phases": [str], "ready_count": int, "unlocked": bool}`.
  - New edges: `READY→PROPOSED`, `SKIPPED→PROPOSED`, `FAILED→PROPOSED`, `REVIEWING→FAILED`, `REVIEWING→RUNNING`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_task_service.py`:

```python
@pytest.mark.parametrize("from_status,to_status", [
    ("READY", "PROPOSED"),
    ("SKIPPED", "PROPOSED"),
    ("FAILED", "PROPOSED"),
    ("REVIEWING", "FAILED"),
    ("REVIEWING", "RUNNING"),
])
def test_research_board_edges_are_legal(temp_db, from_status, to_status):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status=from_status)
    research_task_service.transition_task(task_id, to_status)
    assert research_work_items_repo.get(task_id)["status"] == to_status


def test_reviewing_to_failed_counts_a_retry(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="REVIEWING")
    research_task_service.transition_task(task_id, "FAILED")
    assert research_work_items_repo.get(task_id)["retry_count"] == 1


def test_create_task_stores_board_fields(temp_db):
    pid = projects_repo.get_or_create_id("P")
    origin = research_task_service.create_task(pid, "4", "Origin")
    task_id = research_task_service.create_task(
        pid, "4", "Card", research_method="TARGETED_WEB", entities=["Fees"],
        prompt_text="A long research prompt", framework_key="oes-product-features",
        rationale="Because", suggested_from_work_item_id=origin,
    )
    row = research_work_items_repo.get(task_id)
    assert row["prompt_text"] == "A long research prompt"
    assert row["framework_key"] == "oes-product-features"
    assert row["rationale"] == "Because"
    assert row["suggested_from_work_item_id"] == origin
    assert row["entities_json"] == '["Fees"]'


def test_claim_transition_wins_once(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="READY")
    assert research_task_service.claim_transition(task_id, "READY", "RUNNING") is True
    assert research_task_service.claim_transition(task_id, "READY", "RUNNING") is False


def test_claim_transition_rejects_an_edge_not_in_the_table(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = _make_task(pid, status="PROPOSED")
    with pytest.raises(ValueError):
        research_task_service.claim_transition(task_id, "PROPOSED", "RUNNING")
    assert research_work_items_repo.get(task_id)["status"] == "PROPOSED"


def test_phase7_readiness_counts_phases_with_a_complete_item(temp_db):
    pid = projects_repo.get_or_create_id("P")
    for phase in ("1", "2"):
        tid = research_work_items_repo.create(pid, phase, f"Phase {phase}")
        research_work_items_repo.update_fields(tid, status="COMPLETE")
    tid = research_work_items_repo.create(pid, "3", "Not finished")
    research_work_items_repo.update_fields(tid, status="FOLLOW_UP_REQUIRED")
    readiness = research_task_service.phase7_readiness(pid)
    assert readiness == {"ready_phases": ["1", "2"], "ready_count": 2, "unlocked": False}


def test_phase7_readiness_unlocks_when_all_six_have_complete_items(temp_db):
    pid = projects_repo.get_or_create_id("P")
    for phase in ("1", "2", "3", "4", "5", "6"):
        tid = research_work_items_repo.create(pid, phase, f"Phase {phase}")
        research_work_items_repo.update_fields(tid, status="COMPLETE")
    assert research_task_service.phase7_readiness(pid)["unlocked"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_research_task_service.py -v`
Expected: FAIL. The new edges raise `ValueError`, and `create_task` gets unexpected keywords, and `claim_transition` / `phase7_readiness` don't exist.

- [ ] **Step 3: Implement**

In `services/research_task_service.py`, replace `ALLOWED_TRANSITIONS` with:

```python
ALLOWED_TRANSITIONS = {
    "PROPOSED": {"READY", "SKIPPED"},
    # READY -> PROPOSED: the user un-approves, or edits an approved card.
    "READY": {"RUNNING", "SKIPPED", "PROPOSED"},
    "RUNNING": {"REVIEWING", "FAILED", "COMPLETE"},
    # REVIEWING -> FAILED: review outcome "failed" (the review claim moves RUNNING -> REVIEWING first).
    # REVIEWING -> RUNNING: system only, releasing a review claim after an error or a stale claim.
    "REVIEWING": {"COMPLETE", "FOLLOW_UP_REQUIRED", "WAITING_FOR_HUMAN", "FAILED", "RUNNING"},
    "WAITING_FOR_HUMAN": {"REVIEWING", "COMPLETE", "FAILED"},
    "FOLLOW_UP_REQUIRED": {"READY"},
    # FAILED -> PROPOSED: the user moves a failed card back to draft to edit it.
    "FAILED": {"READY", "SKIPPED", "PROPOSED"},
    "COMPLETE": set(),
    # SKIPPED -> PROPOSED: the user restores a skipped card.
    "SKIPPED": {"PROPOSED"},
}

PHASE7_PREREQUISITE_PHASES = ("1", "2", "3", "4", "5", "6")
```

Replace `create_task` with:

```python
def create_task(
    project_id, phase_key, title, objective=None, priority=None, research_method=None,
    entities=None, expected_output=None, source_requirements=None,
    human_review_required=False, max_retries=3, prompt_text=None, framework_key=None,
    rationale=None, suggested_from_work_item_id=None,
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
        prompt_text=prompt_text,
        framework_key=framework_key,
        rationale=rationale,
        suggested_from_work_item_id=suggested_from_work_item_id,
    )
```

Add after `transition_task`:

```python
def claim_transition(work_item_id, from_status, to_status):
    """Atomically move a card from from_status to to_status; False if it was not in from_status.

    Used where two requests could race for the same card: READY -> RUNNING (Run),
    RUNNING -> REVIEWING (review claim) and REVIEWING -> RUNNING (releasing a claim).
    These edges carry no guards, so skipping transition_task's checks is safe.
    """
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Cannot transition from {from_status} to {to_status}")
    return research_work_items_repo.claim_status(work_item_id, from_status, to_status)
```

Append at the end of the file:

```python
def phase7_readiness(project_id):
    ready = [
        phase_key for phase_key in PHASE7_PREREQUISITE_PHASES
        if any(item["status"] == "COMPLETE" for item in research_work_items_repo.list_for_phase(project_id, phase_key))
    ]
    return {
        "ready_phases": ready,
        "ready_count": len(ready),
        "unlocked": len(ready) == len(PHASE7_PREREQUISITE_PHASES),
    }
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `python -m pytest tests/test_research_task_service.py -v` (expected: PASS). Then run `python -m pytest tests/ -q` (expected: everything passes; the existing illegal-transition cases are unaffected).

- [ ] **Step 5: Commit**

```bash
git add services/research_task_service.py tests/test_research_task_service.py
git commit -m "Add Research Board lifecycle edges, atomic claims and Phase 7 readiness"
```

---
### Task 3: Phase frameworks on the server, and prompt drafting

**Files:**
- Create: `services/prompt_frameworks.json` (generated), `services/prompt_frameworks.py`, `services/prompt_drafting_service.py`
- Modify: `routes/ai.py` (move `_strip_prompt_budget_sections` out)
- Test: `tests/test_prompt_frameworks.py`, `tests/test_prompt_drafting_service.py`

**Interfaces:**
- Consumes: `research_task_service.create_task(... prompt_text, framework_key, rationale, suggested_from_work_item_id)` (Task 2).
- Produces:
  - `prompt_frameworks.PHASE_FRAMEWORKS`, `FRAMEWORK_LABELS`, `frameworks_for_phase(phase_key) -> list[str]`, `resolve_framework(phase_key, framework_key=None) -> str`, and `get_framework(key) -> {"key","label","phase_key","fields","system_prompt"}`.
  - `prompt_drafting_service.FALLBACK_MARKER`, `strip_prompt_budget_sections(text)`, and `draft_prompt(project_name, phase_key, framework_key, title, focus=None, rationale=None) -> {"prompt_text", "framework_key", "drafted": bool}`.
  - `prompt_drafting_service.create_drafted_card(project_name, phase_key, title, research_method=None, focus=None, rationale=None, framework_key=None, priority=None, suggested_from_work_item_id=None) -> {"card_id", "drafted"}`. It creates a `PROPOSED` card and fills its prompt.

- [ ] **Step 1: Generate `services/prompt_frameworks.json` verbatim from `app.js`**

Save this as `extract_frameworks.js` in your scratch/temp folder (not in the repo). Then run it from the repo root with `node <path-to>/extract_frameworks.js`:

```js
const fs = require('fs');
const src = fs.readFileSync('static/app.js', 'utf8');
const start = src.indexOf('const PROMPT_TEMPLATES = {');
if (start < 0) throw new Error('PROMPT_TEMPLATES not found');
const tail = src.slice(start);
const end = /\r?\n\};\r?\n/.exec(tail);
const body = tail.slice(0, end.index + end[0].length).replace('const PROMPT_TEMPLATES =', 'module.exports =');
const mod = { exports: {} };
new Function('module', body)(mod);
const keep = ['oes-landscape', 'oes-student-persona', 'oes-marketing-comparative', 'oes-marketing-website',
  'oes-marketing-sentiment', 'oes-product-features', 'oes-academic-structure', 'oes-academic-unitdive',
  'oes-industry-engagement', 'oes-options-whitespace'];
const out = {};
for (const k of keep) {
  const t = mod.exports[k];
  if (!t) throw new Error('missing ' + k);
  out[k] = { fields: t.fields.map(f => f.label), system_prompt: t.system_prompt };
}
fs.writeFileSync('services/prompt_frameworks.json', JSON.stringify(out, null, 2) + '\n');
console.log('wrote', Object.keys(out).length, 'frameworks');
```

Expected output: `wrote 10 frameworks`. Do **not** hand-edit the prompt text in the JSON.

- [ ] **Step 2: Write the failing tests**

`tests/test_prompt_frameworks.py`:

```python
import pytest

from services import prompt_frameworks


def test_all_ten_frameworks_present_with_text_and_fields():
    keys = set(prompt_frameworks.FRAMEWORK_LABELS)
    assert len(keys) == 10
    for key in keys:
        framework = prompt_frameworks.get_framework(key)
        assert framework["system_prompt"].strip()
        assert framework["fields"]
        assert framework["label"] == prompt_frameworks.FRAMEWORK_LABELS[key]


def test_every_framework_belongs_to_exactly_one_phase():
    mapped = [k for keys in prompt_frameworks.PHASE_FRAMEWORKS.values() for k in keys]
    assert sorted(mapped) == sorted(prompt_frameworks.FRAMEWORK_LABELS)
    assert len(mapped) == len(set(mapped))


def test_framework_text_was_copied_verbatim():
    assert "Phase 1 — LANDSCAPE deep research prompt" in prompt_frameworks.get_framework("oes-landscape")["system_prompt"]
    assert "WHAT / SO WHAT / NOW WHAT FRAMEWORK" in prompt_frameworks.get_framework("oes-options-whitespace")["system_prompt"]
    assert prompt_frameworks.get_framework("oes-landscape")["fields"][0] == "Market / Program Type"


def test_resolve_framework_defaults_to_the_phase_first_framework():
    assert prompt_frameworks.resolve_framework("3") == "oes-marketing-comparative"
    assert prompt_frameworks.resolve_framework("3", "oes-marketing-website") == "oes-marketing-website"
    assert prompt_frameworks.resolve_framework("3", "oes-landscape") == "oes-marketing-comparative"
    assert prompt_frameworks.get_framework("oes-academic-unitdive")["phase_key"] == "5"


def test_unknown_phase_or_framework_raises():
    with pytest.raises(ValueError):
        prompt_frameworks.resolve_framework("9")
    with pytest.raises(ValueError):
        prompt_frameworks.get_framework("deep-research")
```

`tests/test_prompt_drafting_service.py`:

```python
import pytest

from db.repositories import projects_repo, research_work_items_repo
from services import llm_service, project_service, prompt_drafting_service, prompt_frameworks


@pytest.fixture
def project(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    projects_repo.get_or_create_id("P")
    project_service.save_project_prompt("P", "project_prompt", "Assess online Psychology postgrad programs.")
    return "P"


def _capture(monkeypatch, reply="ROLE: analyst. Research every provider in depth and cite all sources."):
    calls = []

    def fake(system_prompt, user_message, max_tokens=4000):
        calls.append({"system": system_prompt, "user": user_message})
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm_service, "prompt_completion", fake)
    return calls


def test_draft_prompt_uses_the_framework_and_card_details(project, monkeypatch):
    calls = _capture(monkeypatch)
    result = prompt_drafting_service.draft_prompt(
        project, "4", None, "Fee structures", focus=["Fees", "FEE-HELP"], rationale="Price drives choice"
    )
    assert result["drafted"] is True
    assert result["framework_key"] == "oes-product-features"
    assert result["prompt_text"].startswith("ROLE: analyst.")
    assert calls[0]["system"] == prompt_frameworks.get_framework("oes-product-features")["system_prompt"]
    user = calls[0]["user"]
    for expected in ("Fee structures", "Fees, FEE-HELP", "Price drives choice",
                     "Assess online Psychology postgrad programs.", "Market / Program Type"):
        assert expected in user


def test_draft_prompt_strips_budget_sections(project, monkeypatch):
    _capture(monkeypatch, reply="Research the fees in detail.\n\n## Token Budget Allocation\n- 10,000 tokens\n\n## Output\nA table.")
    text = prompt_drafting_service.draft_prompt(project, "4", None, "Fees")["prompt_text"]
    assert "Token Budget" not in text
    assert "A table." in text


@pytest.mark.parametrize("reply", [RuntimeError("Anthropic is down"), "   "])
def test_draft_prompt_falls_back_when_drafting_fails(project, monkeypatch, reply):
    _capture(monkeypatch, reply=reply)
    result = prompt_drafting_service.draft_prompt(project, "4", None, "Fee structures", focus=["Fees"])
    assert result["drafted"] is False
    assert result["prompt_text"].startswith(prompt_drafting_service.FALLBACK_MARKER)
    assert "Fee structures" in result["prompt_text"]


def test_create_drafted_card_creates_a_proposed_card_with_its_prompt(project, monkeypatch):
    _capture(monkeypatch)
    result = prompt_drafting_service.create_drafted_card(
        project, "3", "Website review", research_method="TARGETED_WEB", focus=["UX"],
        rationale="Covers competitors", framework_key="oes-marketing-website",
    )
    row = research_work_items_repo.get(result["card_id"])
    assert result["drafted"] is True
    assert row["status"] == "PROPOSED"
    assert row["framework_key"] == "oes-marketing-website"
    assert row["research_method"] == "TARGETED_WEB"
    assert row["rationale"] == "Covers competitors"
    assert row["prompt_text"].startswith("ROLE: analyst.")
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_prompt_frameworks.py tests/test_prompt_drafting_service.py -v`
Expected: FAIL with `ImportError` (the modules don't exist).

- [ ] **Step 4: Implement `services/prompt_frameworks.py`**

```python
# services/prompt_frameworks.py
"""The OES phase research frameworks, moved server-side from static/app.js (PROMPT_TEMPLATES).

prompt_frameworks.json holds each framework's system prompt and form-field labels exactly as
they were in app.js. It was generated mechanically, so do not hand-edit the prompt text.
"""
import json
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("prompt_frameworks.json").read_text(encoding="utf-8"))

# Phase key -> its frameworks, in display order. The first is the phase's default.
PHASE_FRAMEWORKS = {
    "1": ["oes-landscape"],
    "2": ["oes-student-persona"],
    "3": ["oes-marketing-comparative", "oes-marketing-website", "oes-marketing-sentiment"],
    "4": ["oes-product-features"],
    "5": ["oes-academic-structure", "oes-academic-unitdive"],
    "6": ["oes-industry-engagement"],
    "7": ["oes-options-whitespace"],
}

FRAMEWORK_LABELS = {
    "oes-landscape": "Phase 1: The Landscape",
    "oes-student-persona": "Phase 2: The Student",
    "oes-marketing-comparative": "3a: Comparative Marketing Analysis",
    "oes-marketing-website": "3b: Website Review",
    "oes-marketing-sentiment": "3c: Sentiment Analysis & Social Listening",
    "oes-product-features": "Phase 4: Product Features",
    "oes-academic-structure": "5a: Course Structure & Academic Differentiators",
    "oes-academic-unitdive": "5b: Unit-by-Unit Deep Dive",
    "oes-industry-engagement": "Phase 6: Industry Engagement",
    "oes-options-whitespace": "Phase 7: Options for OES",
}

_PHASE_OF = {key: phase for phase, keys in PHASE_FRAMEWORKS.items() for key in keys}


def frameworks_for_phase(phase_key):
    return list(PHASE_FRAMEWORKS.get(str(phase_key), []))


def resolve_framework(phase_key, framework_key=None):
    options = frameworks_for_phase(phase_key)
    if not options:
        raise ValueError(f"Unknown phase: {phase_key!r}")
    return framework_key if framework_key in options else options[0]


def get_framework(framework_key):
    if framework_key not in _DATA or framework_key not in FRAMEWORK_LABELS:
        raise ValueError(f"Unknown framework: {framework_key!r}")
    return {
        "key": framework_key,
        "label": FRAMEWORK_LABELS[framework_key],
        "phase_key": _PHASE_OF[framework_key],
        "fields": list(_DATA[framework_key]["fields"]),
        "system_prompt": _DATA[framework_key]["system_prompt"],
    }
```

- [ ] **Step 5: Implement `services/prompt_drafting_service.py`**

```python
# services/prompt_drafting_service.py
"""Turns a research idea (phase, title, focus, why) into a full research prompt using that
phase's framework. This is the server-side replacement for the Prompt Developer's
"Generate Master Prompt"."""
import re

from db.repositories import projects_repo, research_work_items_repo

from . import llm_service, research_task_service
from .project_service import load_project_prompt
from .prompt_frameworks import get_framework, resolve_framework

# Starts every fallback prompt; Approve refuses a prompt that still contains it.
FALLBACK_MARKER = "[Couldn't draft this from the framework. Edit the prompt before approving.]"


def strip_prompt_budget_sections(prompt_text):
    """Moved verbatim from routes/ai.py (_strip_prompt_budget_sections)."""
    # Paste the old function's body here unchanged.


def _drafting_message(framework, objective, title, focus, rationale):
    focus_text = ", ".join(focus) if focus else "(none given)"
    return (
        "Create a deep research prompt for the research below.\n\n"
        f"PROJECT OBJECTIVE: {objective or '(none set)'}\n"
        f"RESEARCH TITLE: {title}\n"
        f"FOCUS: {focus_text}\n"
        f"WHY THIS RESEARCH: {rationale or '(not given)'}\n\n"
        "Infer these framework inputs from the objective and the research above, and leave out "
        "any you cannot infer: " + "; ".join(framework["fields"]) + "\n\n"
        "OUTPUT RULES:\n"
        "- Do not include token budgets, budget allocation, token counts, runtime limits, or timeframe sections.\n"
        "- Do not include headings like 'Token Budget Allocation' or 'Budget & Timeline'.\n"
        "- Return only the final prompt text."
    )


def fallback_prompt(title, focus, objective):
    lines = [FALLBACK_MARKER, "", f"Research question: {title}"]
    if focus:
        lines.append(f"Focus: {', '.join(focus)}")
    if objective:
        lines.append(f"Project objective: {objective}")
    lines.append("Cite every claim and prefer official university and regulator sources.")
    return "\n".join(lines)


def draft_prompt(project_name, phase_key, framework_key, title, focus=None, rationale=None):
    resolved = resolve_framework(phase_key, framework_key)
    framework = get_framework(resolved)
    objective = load_project_prompt(project_name, "project_prompt") or ""
    try:
        text = llm_service.prompt_completion(
            framework["system_prompt"], _drafting_message(framework, objective, title, focus, rationale)
        )
        text = strip_prompt_budget_sections(text)
    except Exception as exc:  # drafting must never block creating the card
        print(f"[prompt-drafting] could not draft '{title}': {exc}")
        text = None
    if not text or not text.strip():
        return {"prompt_text": fallback_prompt(title, focus, objective), "framework_key": resolved, "drafted": False}
    return {"prompt_text": text.strip(), "framework_key": resolved, "drafted": True}


def create_drafted_card(
    project_name, phase_key, title, research_method=None, focus=None, rationale=None,
    framework_key=None, priority=None, suggested_from_work_item_id=None,
):
    project_id = projects_repo.get_or_create_id(project_name)
    resolved = resolve_framework(phase_key, framework_key)
    card_id = research_task_service.create_task(
        project_id, str(phase_key), title, priority=priority, research_method=research_method,
        entities=focus or None, framework_key=resolved, rationale=rationale,
        suggested_from_work_item_id=suggested_from_work_item_id,
    )
    draft = draft_prompt(project_name, phase_key, resolved, title, focus=focus, rationale=rationale)
    research_work_items_repo.update_fields(card_id, prompt_text=draft["prompt_text"])
    return {"card_id": card_id, "drafted": draft["drafted"]}
```

Move the body of `_strip_prompt_budget_sections` from `routes/ai.py` (about lines 109–141) into `strip_prompt_budget_sections` unchanged. It uses `re`, which is why `re` is imported. Then, in `routes/ai.py`:
- delete the old function;
- add `from services.prompt_drafting_service import strip_prompt_budget_sections as _strip_prompt_budget_sections`. The call in `prompt_dev()` stays as it is.

If ruff then reports `re` as unused in `routes/ai.py`, remove it there.

- [ ] **Step 6: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_prompt_frameworks.py tests/test_prompt_drafting_service.py -v` (expected: PASS). Then run `python -m ruff check .` and `python -m pytest tests/ -q` (expected: clean, all pass).

- [ ] **Step 7: Commit**

```bash
git add services/prompt_frameworks.json services/prompt_frameworks.py services/prompt_drafting_service.py routes/ai.py tests/test_prompt_frameworks.py tests/test_prompt_drafting_service.py
git commit -m "Move the phase frameworks server-side and add framework-based prompt drafting"
```

---

### Task 4: Execution service: starting runs and collecting results

**Files:**
- Create: `services/research_execution_service.py`, `tests/test_research_execution_service.py`
- Move: `tests/test_supervisor_web_research_sync.py` → `tests/test_research_execution_sync.py`
- Modify: `services/supervisor_service.py` (its web-sync code and file helpers move out), `services/research_run_service.py` (`load_runs` adds `research_work_item_id`), `docs/TO-TEST.md` (new location of `MAX_WEB_SOURCES_LISTED`)
- Test: `tests/test_research_run_service.py`

**Interfaces:**
- Consumes:
  - `research_task_service.claim_transition`, `transition_task` and `phase7_readiness` (Task 2);
  - `research_run_service.create_run(project_name, response_id, chat_id, prompt_text) -> run_id`, `update_run(project_name, run_id, **fields)` and `fail_run(project_name, run_id, error)`.
- Produces in `research_execution_service`:
  - `start_runs(project_name, card_ids) -> {"started": [int], "not_ready": [int], "failed": [{"id": int, "error": str}]}`;
  - `run_file_analysis(project_name, card_id, run_id)` and `run_synthesis(project_name, card_id, run_id)` (the background workers);
  - `_spawn(target, *args)` (tests replace it);
  - `sync_web_research_runs(project_name)` (moved unchanged), and `_selected_project_files(project_name)`;
  - constants `MAX_FILE_ANALYSIS_TOTAL_CHARS`, `MAX_FILE_ANALYSIS_PER_FILE_CHARS`, `MAX_WEB_SOURCES_LISTED = 20` (value unchanged), `FILE_ANALYSIS_MAX_TOKENS = 8000` and `ABANDONED_LOCAL_RUN_SECONDS = 1800`.
  - `load_runs()` entries gain `"research_work_item_id"`.

- [ ] **Step 1: Move the web-sync tests**

Run `git mv tests/test_supervisor_web_research_sync.py tests/test_research_execution_sync.py`. In the moved file, replace every `supervisor_service` with `research_execution_service`, including the import line `from services import research_execution_service`. Don't change anything else.

- [ ] **Step 2: Write the failing tests**

`tests/test_research_execution_service.py`:

```python
from types import SimpleNamespace

import pytest

from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
from services import insights_service, llm_service, research_execution_service, research_task_service

PROMPT = "Research the fee structures of every online Psychology postgraduate program in Australia."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Run workers inline instead of on a thread.
    monkeypatch.setattr(research_execution_service, "_spawn", lambda target, *args: target(*args))
    return projects_repo.get_or_create_id("P")


def _ready_card(pid, method="TARGETED_WEB", phase="4", prompt=PROMPT, title="Fees"):
    card_id = research_task_service.create_task(pid, phase, title, research_method=method, prompt_text=prompt)
    research_work_items_repo.update_fields(card_id, status="READY")
    return card_id


def _fake_openai(monkeypatch, fail_for=()):
    sent = []

    def start(prompt):
        sent.append(prompt)
        if prompt in fail_for:
            raise RuntimeError("OpenAI is down")
        return SimpleNamespace(id=f"resp_{len(sent)}", status="queued")

    monkeypatch.setattr(research_execution_service.openai_service, "start_deep_research", start)
    return sent


def test_start_web_run_sends_the_full_prompt_and_links_the_run(pid, monkeypatch):
    sent = _fake_openai(monkeypatch)
    card_id = _ready_card(pid)
    result = research_execution_service.start_runs("P", [card_id])
    assert result == {"started": [card_id], "not_ready": [], "failed": []}
    assert sent == [PROMPT]
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"
    run = research_runs_repo.find_latest_for_work_item(card_id)
    assert run["prompt_text"] == PROMPT
    assert run["response_id"] == "resp_1"
    assert run["status"] == "running"


def test_only_cards_still_approved_are_started(pid, monkeypatch):
    sent = _fake_openai(monkeypatch)
    draft = research_task_service.create_task(pid, "4", "Draft", research_method="TARGETED_WEB", prompt_text=PROMPT)
    other_pid = projects_repo.get_or_create_id("Other")
    foreign = _ready_card(other_pid)
    result = research_execution_service.start_runs("P", [draft, foreign, 99999])
    assert result["started"] == []
    assert result["not_ready"] == [draft, foreign, 99999]
    assert sent == []
    assert research_work_items_repo.get(draft)["status"] == "PROPOSED"
    assert research_work_items_repo.get(foreign)["status"] == "READY"


def test_a_card_cannot_be_started_twice(pid, monkeypatch):
    sent = _fake_openai(monkeypatch)
    card_id = _ready_card(pid)
    research_execution_service.start_runs("P", [card_id])
    second = research_execution_service.start_runs("P", [card_id])
    assert second["not_ready"] == [card_id]
    assert len(sent) == 1


def test_one_failed_start_does_not_stop_the_others(pid, monkeypatch):
    _fake_openai(monkeypatch, fail_for={"Bad prompt that OpenAI rejects for this test case."})
    bad = _ready_card(pid, prompt="Bad prompt that OpenAI rejects for this test case.", title="Bad")
    good = _ready_card(pid, title="Good")
    result = research_execution_service.start_runs("P", [bad, good])
    assert result["started"] == [good]
    assert result["failed"][0]["id"] == bad
    assert "OpenAI is down" in result["failed"][0]["error"]
    assert research_work_items_repo.get(bad)["status"] == "FAILED"
    assert research_runs_repo.find_latest_for_work_item(bad)["status"] == "failed"


@pytest.mark.parametrize("method,prompt", [(None, PROMPT), ("TARGETED_WEB", None), ("TARGETED_WEB", "   ")])
def test_cards_from_before_the_board_without_prompt_or_method_are_refused(pid, monkeypatch, method, prompt):
    sent = _fake_openai(monkeypatch)
    card_id = _ready_card(pid, method=method, prompt=prompt)
    result = research_execution_service.start_runs("P", [card_id])
    assert result["failed"][0]["id"] == card_id
    assert "back to draft" in result["failed"][0]["error"]
    assert research_work_items_repo.get(card_id)["status"] == "READY"
    assert research_runs_repo.find_latest_for_work_item(card_id) is None
    assert sent == []


def test_file_analysis_uses_the_card_prompt_and_completes_the_run(pid, monkeypatch):
    calls = []
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: calls.append((system, user, max_tokens)) or "## Findings\nFees found.")
    card_id = _ready_card(pid, method="FILE_ANALYSIS")
    research_execution_service.start_runs("P", [card_id])
    run = research_runs_repo.find_latest_for_work_item(card_id)
    assert run["status"] == "completed"
    assert run["output_text"] == "## Findings\nFees found."
    assert PROMPT in calls[0][0]
    assert calls[0][2] == research_execution_service.FILE_ANALYSIS_MAX_TOKENS
    # The card waits on the board's refresh to be reviewed.
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"


def test_file_analysis_failure_fails_the_run_but_leaves_the_card_for_refresh(pid, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("Anthropic is down")
    monkeypatch.setattr(llm_service, "prompt_completion", boom)
    card_id = _ready_card(pid, method="FILE_ANALYSIS")
    research_execution_service.start_runs("P", [card_id])
    run = research_runs_repo.find_latest_for_work_item(card_id)
    assert run["status"] == "failed"
    assert "Anthropic is down" in run["error"]
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"


def _complete_phases_1_to_6(pid):
    for phase in ("1", "2", "3", "4", "5", "6"):
        tid = research_work_items_repo.create(pid, phase, f"Done {phase}")
        research_work_items_repo.update_fields(tid, status="COMPLETE")


def test_synthesis_refused_while_phase_7_is_locked(pid, monkeypatch):
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    result = research_execution_service.start_runs("P", [card_id])
    assert result["failed"][0]["id"] == card_id
    assert research_work_items_repo.get(card_id)["status"] == "READY"


def test_synthesis_writes_phase_7_and_completes_the_card(pid, monkeypatch):
    _complete_phases_1_to_6(pid)
    insights_service.save_insights("P", {
        "generated_at": "2026-10-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
        "phases": {str(i): {"title": f"Phase {i}", "summary": f"Summary {i}", "confidence": "high",
                             "evidence_sources": [], "gaps": [], "suggested_topics": [],
                             "linked_files": [], "linked_file_ids": []} for i in range(1, 8)},
    })
    calls = []
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: calls.append(user) or "Options: do X.")
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    research_execution_service.start_runs("P", [card_id])
    assert research_work_items_repo.get(card_id)["status"] == "COMPLETE"
    assert PROMPT in calls[0] and "Summary 3" in calls[0]
    current = insights_service.load_current_insights("P")
    assert current["phases"]["7"]["summary"] == "Options: do X."
    assert current["generated_at"] == "2026-10-01T00:00:00"
    assert research_runs_repo.find_latest_for_work_item(card_id)["output_text"] == "Options: do X."
```

Append to `tests/test_research_run_service.py`. Check that file's imports and fixtures first, and reuse its existing project fixture if it has one. Otherwise use `temp_db`, `tmp_path` and `monkeypatch.chdir(tmp_path)` as below:

```python
def test_load_runs_includes_the_linked_work_item(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from db.repositories import projects_repo, research_work_items_repo
    from services import research_run_service
    pid = projects_repo.get_or_create_id("P")
    card_id = research_work_items_repo.create(pid, "4", "Card")
    run_id = research_run_service.create_run("P", "resp_1", None, "prompt")
    research_run_service.update_run("P", run_id, research_work_item_id=card_id)
    assert research_run_service.load_runs("P")[run_id]["research_work_item_id"] == card_id
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_research_execution_service.py tests/test_research_execution_sync.py tests/test_research_run_service.py -v`
Expected: FAIL with `ImportError` for `research_execution_service`, plus the `KeyError` from `load_runs`.

- [ ] **Step 4: Create `services/research_execution_service.py`**

Move these into the new module, **cut** from `services/supervisor_service.py` with their bodies unchanged:
- `MAX_FILE_ANALYSIS_TOTAL_CHARS`, `MAX_FILE_ANALYSIS_PER_FILE_CHARS`, `MAX_WEB_SOURCES_LISTED`
- `_selected_project_files`, `_needs_web_sync`, `_web_research_error`, `_format_web_research_output`, `sync_web_research_runs`

Then add the new code:

```python
# services/research_execution_service.py
"""Starting research runs and collecting their results.

Only the user's Run action reaches start_runs (through board_service). The Supervisor has
no path here. That's how the "nothing runs without your approval" rule is kept.
"""
import threading
from datetime import datetime

from flask import current_app

from db.repositories import projects_repo, research_runs_repo, research_work_items_repo

from . import insights_service, llm_service, openai_service, research_run_service, research_task_service
from .deep_research_output import extract_deep_research_output
from .file_index_service import HIDDEN_SOURCE_FILES, reconcile_file_index, reconcile_selected_file_ids
from .file_service import load_project_files
from .phases import PHASE_DEFINITIONS

MAX_FILE_ANALYSIS_TOTAL_CHARS = 60000
MAX_FILE_ANALYSIS_PER_FILE_CHARS = 15000
MAX_WEB_SOURCES_LISTED = 20
# Framework prompts ask for long structured reports.
FILE_ANALYSIS_MAX_TOKENS = 8000
SYNTHESIS_MAX_TOKENS = 8000
# A "My files" or options-report run still "running" after this long was cut off (app restart).
ABANDONED_LOCAL_RUN_SECONDS = 1800

RUNNABLE_METHODS = ("TARGETED_WEB", "FILE_ANALYSIS", "SYNTHESIS")
NOT_RUNNABLE_MESSAGE = (
    "This research has no prompt or no method, so it can't run. Move it back to draft and edit it."
)
PHASE7_LOCKED_MESSAGE = "Phase 7 opens when Phases 1 to 6 each have finished research."

FILE_ANALYSIS_SYSTEM_PROMPT = (
    "You are a research analyst for an Australian higher-education competitive analysis "
    "project. Use ONLY the provided source files. If something cannot be answered from them, "
    "say MISSING -- do not guess or use general knowledge."
)

SYNTHESIS_SYSTEM_PROMPT = (
    "You are a senior strategy consultant synthesising a competitive "
    "landscape analysis for Australian higher education. Using ONLY the "
    "phase summaries provided, identify the key strategic options available. "
    "Use the SO WHAT / NOW WHAT framework. Write in Australian English."
)


def _spawn(target, *args):
    """Run target(*args) on a background thread inside an app context. Tests replace this."""
    app = current_app._get_current_object()

    def runner():
        with app.app_context():
            target(*args)

    threading.Thread(target=runner, daemon=True).start()


def start_runs(project_name, card_ids):
    project_id = projects_repo.get_id(project_name)
    result = {"started": [], "not_ready": [], "failed": []}
    for card_id in card_ids:
        card = research_work_items_repo.get(card_id)
        if card is None or project_id is None or card["project_id"] != project_id or card["status"] != "READY":
            result["not_ready"].append(card_id)
            continue
        method = card["research_method"]
        prompt_text = (card["prompt_text"] or "").strip()
        if method not in RUNNABLE_METHODS or not prompt_text:
            result["failed"].append({"id": card_id, "error": NOT_RUNNABLE_MESSAGE})
            continue
        if method == "SYNTHESIS" and not research_task_service.phase7_readiness(project_id)["unlocked"]:
            result["failed"].append({"id": card_id, "error": PHASE7_LOCKED_MESSAGE})
            continue
        if not research_task_service.claim_transition(card_id, "READY", "RUNNING"):
            result["not_ready"].append(card_id)
            continue

        run_id = research_run_service.create_run(project_name, None, None, prompt_text)
        research_run_service.update_run(project_name, run_id, research_work_item_id=card_id, prompt_text=prompt_text)
        try:
            if method == "TARGETED_WEB":
                response = openai_service.start_deep_research(prompt_text)
                research_run_service.update_run(project_name, run_id, response_id=response.id)
            elif method == "FILE_ANALYSIS":
                _spawn(run_file_analysis, project_name, card_id, run_id)
            else:
                _spawn(run_synthesis, project_name, card_id, run_id)
        except Exception as exc:
            research_run_service.fail_run(project_name, run_id, str(exc))
            research_task_service.transition_task(card_id, "FAILED")
            result["failed"].append({"id": card_id, "error": str(exc)})
            continue
        result["started"].append(card_id)
    return result


def _build_file_analysis_messages(prompt_text, files):
    system_prompt = f"{FILE_ANALYSIS_SYSTEM_PROMPT}\n\n# RESEARCH INSTRUCTIONS\n\n{prompt_text}"
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


def run_file_analysis(project_name, card_id, run_id):
    """Background worker. On failure the run is failed; the board's refresh then fails the card."""
    try:
        card = research_work_items_repo.get(card_id)
        system_prompt, user_message = _build_file_analysis_messages(
            card["prompt_text"] or card["title"], _selected_project_files(project_name)
        )
        output_text = llm_service.prompt_completion(system_prompt, user_message, max_tokens=FILE_ANALYSIS_MAX_TOKENS)
        research_run_service.update_run(
            project_name, run_id, status="completed", output_text=output_text,
            completed_at=datetime.now().isoformat(),
        )
    except Exception as exc:
        research_run_service.fail_run(project_name, run_id, str(exc))


def run_synthesis(project_name, card_id, run_id):
    """Background worker for the Phase 7 options report. Writes Insights Phase 7 and completes the card."""
    try:
        card = research_work_items_repo.get(card_id)
        current = insights_service.load_current_insights(project_name)
        summaries = []
        for phase_key, definition in PHASE_DEFINITIONS.items():
            if phase_key == "7":
                continue
            summary = current["phases"].get(phase_key, {}).get("summary", "MISSING")
            if summary and summary != "MISSING":
                summaries.append(f"## {definition['title']}\n\n{summary}")
        user_message = (
            f"{card['prompt_text'] or ''}\n\n# PHASE SUMMARIES\n\n"
            + ("\n\n".join(summaries) if summaries else "(no phase summaries available)")
        )
        text = llm_service.prompt_completion(SYNTHESIS_SYSTEM_PROMPT, user_message, max_tokens=SYNTHESIS_MAX_TOKENS)

        phases = dict(current["phases"])
        phase_7 = dict(phases.get("7", {}))
        phase_7["summary"] = text
        phase_7["confidence"] = "medium"
        phases["7"] = phase_7
        insights_service.save_insights(project_name, {
            # Kept unchanged: generated_at marks when the Phase 1-6 summaries were last refreshed.
            "generated_at": current.get("generated_at") or datetime.now().isoformat(),
            "competitors": current.get("competitors", []),
            "competitor_landscape_markdown": current.get("competitor_landscape_markdown", ""),
            "phases": phases,
        })
        research_run_service.update_run(
            project_name, run_id, status="completed", output_text=text, completed_at=datetime.now().isoformat(),
        )
        research_task_service.transition_task(card_id, "COMPLETE")
    except Exception as exc:
        research_run_service.fail_run(project_name, run_id, str(exc))
```

In `services/supervisor_service.py`, delete the cut definitions and import them instead, so that `handle_dispatch_task` and `run_supervisor_cycle` keep working until Task 6:

```python
from .research_execution_service import (
    MAX_FILE_ANALYSIS_PER_FILE_CHARS,
    MAX_FILE_ANALYSIS_TOTAL_CHARS,
    _selected_project_files,
    sync_web_research_runs,
)
```

Delete imports from `supervisor_service.py` that ruff then reports as unused (for example `extract_deep_research_output` and the file-index helpers). Keep `openai_service`, because `handle_dispatch_task` still uses it.

In `services/research_run_service.py` `load_runs`, add `"research_work_item_id": row["research_work_item_id"],` to the dict built for each row.

In `docs/TO-TEST.md`, under "Where the limits live", change the `MAX_WEB_SOURCES_LISTED` line so it says that constant now lives in `services/research_execution_service.py`. Its value is unchanged.

- [ ] **Step 5: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_research_execution_service.py tests/test_research_execution_sync.py tests/test_research_run_service.py tests/test_supervisor_service.py -v` (expected: PASS). Then run `python -m ruff check .` and `python -m pytest tests/ -q` (expected: clean, all pass).

- [ ] **Step 6: Commit**

```bash
git add services/research_execution_service.py services/supervisor_service.py services/research_run_service.py docs/TO-TEST.md tests/test_research_execution_service.py tests/test_research_execution_sync.py tests/test_research_run_service.py
git commit -m "Add user-triggered research execution service; move web sync out of the Supervisor"
```

---

### Task 5: Report files and phase links

**Files:**
- Modify: `services/research_execution_service.py`
- Test: `tests/test_research_reports.py`

**Interfaces:**
- Consumes: `save_project_file(project_name, filename, content)`, `ensure_file_id(project_name, filename) -> stable_id`, `get_project_file_path(project_name, filename) -> Path | None`, `sources_repo.get_by_stable_id(project_id, stable_id)`, `insights_service.load_current_insights` / `save_insights`.
- Produces in `research_execution_service`:
  - `METHOD_LABELS = {"TARGETED_WEB": "Web research", "FILE_ANALYSIS": "My files", "SYNTHESIS": "Options report"}`;
  - `report_filename(project_name, card, when) -> str`;
  - `save_report(project_name, card_id, run_id) -> stable_file_id`. It's idempotent;
  - `link_report(project_name, card_id, run_id) -> bool`. It returns True only when it added a new link.

- [ ] **Step 1: Write the failing tests**

`tests/test_research_reports.py`:

```python
import re
from pathlib import Path

import pytest

from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
from services import insights_service, research_execution_service, research_task_service


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return projects_repo.get_or_create_id("P")


def _finished(pid, title="Fee structures", run_id="run_1", phase="4"):
    card_id = research_task_service.create_task(pid, phase, title, research_method="TARGETED_WEB", prompt_text="The prompt we sent.")
    research_work_items_repo.update_fields(card_id, status="RUNNING")
    research_runs_repo.create(run_id, pid, "resp_1", None, "The prompt we sent.")
    research_runs_repo.update(run_id, research_work_item_id=card_id, status="completed",
                              output_text="Sources (1):\n- A - https://a.example\n\nThe report body.",
                              prompt_text="The prompt we sent.", completed_at="2026-10-05T10:00:00")
    return card_id


def _files_dir():
    return Path("projects") / "P" / "files"


def test_save_report_writes_a_source_file_and_records_it(pid):
    card_id = _finished(pid)
    stable_id = research_execution_service.save_report("P", card_id, "run_1")
    names = [p.name for p in _files_dir().iterdir()]
    assert len(names) == 1
    assert re.fullmatch(r"Research P4 - Fee structures \(\d{4}-\d{2}-\d{2}\)\.md", names[0])
    content = (_files_dir() / names[0]).read_text(encoding="utf-8")
    assert "# Fee structures" in content
    assert "The prompt we sent." in content
    assert "The report body." in content
    assert stable_id.startswith("f_")
    assert research_runs_repo.get("run_1")["report_stable_file_id"] == stable_id


def test_save_report_is_saved_once(pid):
    card_id = _finished(pid)
    first = research_execution_service.save_report("P", card_id, "run_1")
    second = research_execution_service.save_report("P", card_id, "run_1")
    assert first == second
    assert len(list(_files_dir().iterdir())) == 1


def test_titles_with_characters_windows_forbids_still_save(pid):
    card_id = _finished(pid, title='Fees: 2025/26 <draft> "final"? | *all*')
    research_execution_service.save_report("P", card_id, "run_1")
    (name,) = [p.name for p in _files_dir().iterdir()]
    assert not re.search(r'[<>:"/\\|?*]', name)
    assert name.startswith("Research P4 - Fees 2025 26 draft final all")


def test_two_reports_with_the_same_title_on_the_same_day_get_distinct_names(pid):
    a = _finished(pid, run_id="run_a")
    b = _finished(pid, run_id="run_b")
    research_execution_service.save_report("P", a, "run_a")
    research_execution_service.save_report("P", b, "run_b")
    names = sorted(p.name for p in _files_dir().iterdir())
    assert len(names) == 2
    assert names[1].endswith(" 2.md") or names[0].endswith(" 2.md")


def test_link_report_links_to_the_phase_and_keeps_generated_at(pid):
    insights_service.save_insights("P", {"generated_at": "2026-10-01T09:00:00.000Z", "competitors": [],
                                         "competitor_landscape_markdown": "", "phases": {}})
    card_id = _finished(pid)
    stable_id = research_execution_service.save_report("P", card_id, "run_1")
    assert research_execution_service.link_report("P", card_id, "run_1") is True
    current = insights_service.load_current_insights("P")
    assert stable_id in current["phases"]["4"]["linked_file_ids"]
    assert current["generated_at"] == "2026-10-01T09:00:00.000Z"
    assert research_runs_repo.get("run_1")["report_linked_at"]


def test_a_report_the_user_unlinked_is_not_linked_again(pid):
    card_id = _finished(pid)
    research_execution_service.save_report("P", card_id, "run_1")
    research_execution_service.link_report("P", card_id, "run_1")
    current = insights_service.load_current_insights("P")
    current["phases"]["4"]["linked_file_ids"] = []
    current["phases"]["4"]["linked_files"] = []
    insights_service.save_insights("P", current)

    assert research_execution_service.link_report("P", card_id, "run_1") is False
    assert insights_service.load_current_insights("P")["phases"]["4"]["linked_file_ids"] == []


def test_link_report_does_nothing_when_the_file_was_deleted(pid):
    card_id = _finished(pid)
    research_execution_service.save_report("P", card_id, "run_1")
    for path in _files_dir().iterdir():
        path.unlink()
    assert research_execution_service.link_report("P", card_id, "run_1") is False
    assert research_runs_repo.get("run_1")["report_linked_at"]


def test_link_report_without_a_saved_report_does_nothing(pid):
    card_id = _finished(pid)
    assert research_execution_service.link_report("P", card_id, "run_1") is False
    assert research_runs_repo.get("run_1")["report_linked_at"] is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_research_reports.py -v`
Expected: FAIL with `AttributeError` (no `save_report`).

- [ ] **Step 3: Implement**

Add to the imports of `services/research_execution_service.py`:

```python
import re

from db.repositories import sources_repo

from .file_index_service import ensure_file_id
from .file_service import save_project_file
from .project_service import get_project_file_path
```

(Merge them with the existing import lines; keep ruff's import order.)

Append:

```python
METHOD_LABELS = {"TARGETED_WEB": "Web research", "FILE_ANALYSIS": "My files", "SYNTHESIS": "Options report"}
REPORT_TITLE_MAX_CHARS = 80
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_title(title):
    cleaned = _UNSAFE_FILENAME_CHARS.sub(" ", title or "")
    cleaned = " ".join(cleaned.split()).strip(" .")
    cleaned = cleaned[:REPORT_TITLE_MAX_CHARS].rstrip(" .")
    return cleaned or "Untitled"


def report_filename(project_name, card, when):
    base = f"Research P{card['phase_key']} - {_safe_title(card['title'])} ({when:%Y-%m-%d})"
    candidate = f"{base}.md"
    suffix = 2
    while True:
        path = get_project_file_path(project_name, candidate)
        if path is None:
            raise ValueError(f"Could not make a valid file name for {card['title']!r}")
        if not path.exists():
            return candidate
        candidate = f"{base} {suffix}.md"
        suffix += 1


def _report_content(card, run):
    phase = PHASE_DEFINITIONS.get(card["phase_key"], {}).get("title", "")
    return (
        f"# {card['title']}\n\n"
        f"- Phase: {card['phase_key']} - {phase}\n"
        f"- Method: {METHOD_LABELS.get(card['research_method'], card['research_method'] or '')}\n"
        f"- Finished: {(run['completed_at'] or '')[:10]}\n\n"
        f"## Research prompt\n\n{run['prompt_text'] or card['prompt_text'] or ''}\n\n"
        f"## Report\n\n{run['output_text'] or ''}\n"
    )


def save_report(project_name, card_id, run_id):
    """Save a finished run's report as a source file, once. Returns its stable file id."""
    run = research_runs_repo.get(run_id)
    if run["report_stable_file_id"]:
        return run["report_stable_file_id"]
    card = research_work_items_repo.get(card_id)
    filename = report_filename(project_name, card, datetime.now())
    save_project_file(project_name, filename, _report_content(card, run))
    stable_id = ensure_file_id(project_name, filename)
    research_run_service.update_run(project_name, run_id, report_stable_file_id=stable_id)
    return stable_id


def link_report(project_name, card_id, run_id):
    """Link a saved report to its card's phase, once. A later unlink by the user is respected."""
    run = research_runs_repo.get(run_id)
    if not run["report_stable_file_id"] or run["report_linked_at"]:
        return False
    project_id = projects_repo.get_or_create_id(project_name)
    card = research_work_items_repo.get(card_id)
    linked_at = datetime.now().isoformat()
    source = sources_repo.get_by_stable_id(project_id, run["report_stable_file_id"])
    if source is None:  # the user deleted the file before it was linked
        research_run_service.update_run(project_name, run_id, report_linked_at=linked_at)
        return False

    current = insights_service.load_current_insights(project_name)
    phases = dict(current["phases"])
    phase = dict(phases[card["phase_key"]])
    ids = list(phase.get("linked_file_ids") or [])
    names = list(phase.get("linked_files") or [])
    added = source["stable_file_id"] not in ids
    if added:
        ids.append(source["stable_file_id"])
        names.append(source["filename"])
        phase["linked_file_ids"] = ids
        phase["linked_files"] = names
        phases[card["phase_key"]] = phase
        insights_service.save_insights(project_name, {
            # Unchanged: generated_at marks when summaries were last generated, not when files were linked.
            "generated_at": current.get("generated_at", ""),
            "competitors": current.get("competitors", []),
            "competitor_landscape_markdown": current.get("competitor_landscape_markdown", ""),
            "phases": phases,
        })
    research_run_service.update_run(project_name, run_id, report_linked_at=linked_at)
    return added
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_research_reports.py -v` (expected: PASS). Then run `python -m ruff check .` and `python -m pytest tests/ -q` (expected: clean, all pass).

- [ ] **Step 5: Commit**

```bash
git add services/research_execution_service.py tests/test_research_reports.py
git commit -m "Save finished research as a phase-linked source file, once"
```

---

### Task 6: Supervisor reshaped: drafting and automatic review only

**Files:**
- Modify: `services/supervisor_service.py` (rewrite everything below `build_context`), `routes/supervisor.py` (run endpoint returns 410)
- Test: `tests/test_supervisor_service.py` (keep the `build_context` tests, replace the rest), `tests/test_routes_supervisor.py`

**Interfaces:**
- Consumes:
  - `prompt_drafting_service.create_drafted_card` (Task 3);
  - `prompt_frameworks.FRAMEWORK_LABELS` and `frameworks_for_phase` (Task 3);
  - `research_task_service.claim_transition` (Task 2), plus `transition_task`, `set_review_scores` and `flag_for_human_review`.
- Produces:
  - `supervisor_service.draft_researches(project_name) -> {"action": str, "input": dict, "execution": {"success": bool, "result"|"error": ...}}`;
  - `supervisor_service.review_card(project_name, card_id) -> {"reviewed": bool, ...}`;
  - `supervisor_service.create_followup_card(project_name, original_card_row, followup: dict) -> card_id`;
  - constants `DRAFTING_TOOLS`, `DRAFTING_HANDLERS` (keys exactly `{"propose_tasks", "no_action"}`), `REVIEW_TOOL`, and `REVIEW_OUTCOMES = ("COMPLETE", "FOLLOW_UP_REQUIRED", "NEEDS_HUMAN", "FAILED")`.
- Removed: `TOOL_SCHEMAS`, `TOOL_HANDLERS`, `run_supervisor_cycle` and all `handle_*` functions except `handle_propose_tasks` and `handle_no_action`. Also removed: `_build_file_analysis_prompt`, the `SYNTHESIS_SYSTEM_PROMPT` copy, and the imports from `research_execution_service` added in Task 4.

- [ ] **Step 1: Cut the old tests**

In `tests/test_supervisor_service.py`, keep the imports and every `test_build_context_*` test, plus the `_task_with_run` helper. Delete everything from `def test_tool_schemas_has_nine_tools_with_correct_names` to the end of the file, including the fake Anthropic classes and the `app_context` fixture in that region.

Some kept `build_context` tests call `project_service.save_project_prompt` without changing directory, which leaks a real `projects/P/` into the repo. Add this right after the imports:

```python
@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
```

In `tests/test_routes_supervisor.py`, replace `test_run_supervisor_route` with:

```python
def test_run_supervisor_route_is_retired(client):
    client.post("/api/projects", json={"name": "P"})
    resp = client.post("/api/supervisor/run", json={"project": "P"})
    assert resp.status_code == 410
    assert "Research tab" in resp.get_json()["error"]
```

Delete any other test in that file that posts to `/api/supervisor/run` and expects a decision. Keep the decisions-endpoint tests.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_supervisor_service.py`:

```python
import json  # noqa: E402  (place with the other imports at the top of the file instead)
from types import SimpleNamespace  # noqa: E402

from services import llm_service, research_task_service  # noqa: E402


class _Block:
    def __init__(self, name, input):
        self.type = "tool_use"
        self.name = name
        self.input = input


class _FakeClient:
    def __init__(self, blocks=(), error=None):
        self.calls = []
        self._blocks = list(blocks)
        self._error = error
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(content=self._blocks)


@pytest.fixture
def app_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        yield flask_app


@pytest.fixture
def drafted(monkeypatch):
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: "ROLE: analyst. A complete drafted research prompt.")


def _install(monkeypatch, client):
    monkeypatch.setattr(supervisor_service.anthropic, "Anthropic", lambda api_key: client)
    return client


def _propose(*tasks, reason="Phases have no research yet"):
    return _Block("propose_tasks", {"tasks": list(tasks), "reason": reason})


def _task(phase="1", title="Landscape overview", method="TARGETED_WEB", **extra):
    return dict({"phase_key": phase, "title": title, "research_method": method,
                 "focus": ["Providers"], "rationale": "Phase has no research"}, **extra)


# ---- drafting ----

def test_drafting_offers_only_propose_and_no_action(temp_db, app_context, drafted, monkeypatch):
    projects_repo.get_or_create_id("P")
    client = _install(monkeypatch, _FakeClient([_Block("no_action", {"reason": "Nothing to add"})]))
    supervisor_service.draft_researches("P")
    call = client.calls[0]
    assert {t["name"] for t in call["tools"]} == {"propose_tasks", "no_action"}
    assert call["tool_choice"] == {"type": "any"}
    assert set(supervisor_service.DRAFTING_HANDLERS) == {"propose_tasks", "no_action"}


def test_the_supervisor_has_no_way_to_approve_or_start_research():
    for removed in ("handle_mark_ready", "handle_dispatch_task", "handle_skip_task",
                    "handle_trigger_synthesis", "run_supervisor_cycle", "TOOL_HANDLERS"):
        assert not hasattr(supervisor_service, removed)


def test_propose_creates_drafted_cards_awaiting_approval(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([_propose(
        _task(),
        _task(phase="3", title="Website review", framework_key="oes-marketing-website"),
    )]))
    result = supervisor_service.draft_researches("P")
    assert result["execution"]["success"] is True
    items = research_work_items_repo.list_for_project(pid)
    assert [i["status"] for i in items] == ["PROPOSED", "PROPOSED"]
    assert items[0]["framework_key"] == "oes-landscape"
    assert items[1]["framework_key"] == "oes-marketing-website"
    assert items[0]["prompt_text"].startswith("ROLE: analyst.")
    assert items[0]["rationale"] == "Phase has no research"
    decision = agent_decisions_repo.list_for_project(pid)[0]
    assert decision["decision_type"] == "propose_tasks"
    assert json.loads(decision["detail"])["execution"]["success"] is True


@pytest.mark.parametrize("bad_task", [
    _task(phase="7", title="Options"),
    _task(method="DEEP_MAGIC"),
    _task(title="   "),
    _task(depends_on_batch_indices=[5]),
])
def test_invalid_proposals_create_nothing_and_are_recorded(temp_db, app_context, drafted, monkeypatch, bad_task):
    pid = projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([_propose(_task(title="Fine one"), bad_task)]))
    result = supervisor_service.draft_researches("P")
    assert result["execution"]["success"] is False
    assert research_work_items_repo.list_for_project(pid) == []
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "propose_tasks"


def test_a_cycle_between_proposals_creates_nothing(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([_propose(
        _task(title="A", depends_on_batch_indices=[1]), _task(title="B", depends_on_batch_indices=[0]),
    )]))
    assert supervisor_service.draft_researches("P")["execution"]["success"] is False
    assert research_work_items_repo.list_for_project(pid) == []


def test_batch_dependencies_are_wired_to_real_ids(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([_propose(_task(title="A"), _task(title="B", depends_on_batch_indices=[0]))]))
    supervisor_service.draft_researches("P")
    a, b = research_work_items_repo.list_for_project(pid)
    assert [d["depends_on_work_item_id"] for d in research_work_items_repo.list_dependencies(b["id"])] == [a["id"]]


def test_no_tool_call_raises(temp_db, app_context, monkeypatch):
    projects_repo.get_or_create_id("P")
    _install(monkeypatch, _FakeClient([SimpleNamespace(type="text", text="Hmm")]))
    with pytest.raises(RuntimeError):
        supervisor_service.draft_researches("P")


# ---- reviewing ----

def _running_card(pid, output="Sources (1):\n- A - https://a.example\n\nReport body.", run_status="completed", **card_fields):
    card_id = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB",
                                                prompt_text="Find the fees.", entities=["Fees"], **card_fields)
    research_work_items_repo.update_fields(card_id, status="RUNNING")
    research_runs_repo.create(f"run_{card_id}", pid, "resp", None, "Find the fees.")
    research_runs_repo.update(f"run_{card_id}", research_work_item_id=card_id, status=run_status,
                              output_text=output, prompt_text="Find the fees.")
    return card_id


def _review(outcome, **extra):
    return _Block("review_outcome", dict({"completeness_score": 0.8, "evidence_score": 0.7,
                                          "identified_gaps": ["No intake dates"], "outcome": outcome,
                                          "reason": "Because"}, **extra))


def test_review_is_forced_to_the_review_tool(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    client = _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    supervisor_service.review_card("P", card_id)
    call = client.calls[0]
    assert [t["name"] for t in call["tools"]] == ["review_outcome"]
    assert call["tool_choice"] == {"type": "tool", "name": "review_outcome"}
    assert "Report body." in call["messages"][0]["content"]


@pytest.mark.parametrize("outcome,status", [
    ("COMPLETE", "COMPLETE"), ("FOLLOW_UP_REQUIRED", "FOLLOW_UP_REQUIRED"),
    ("NEEDS_HUMAN", "WAITING_FOR_HUMAN"), ("FAILED", "FAILED"),
])
def test_review_outcomes(temp_db, app_context, monkeypatch, outcome, status):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    _install(monkeypatch, _FakeClient([_review(outcome)]))
    result = supervisor_service.review_card("P", card_id)
    row = research_work_items_repo.get(card_id)
    assert result["reviewed"] is True
    assert row["status"] == status
    assert row["completeness_score"] == 0.8
    assert json.loads(row["identified_gaps_json"]) == ["No intake dates"]
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "review_outcome"


def test_follow_up_arrives_as_a_new_draft(temp_db, app_context, drafted, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    _install(monkeypatch, _FakeClient([_review("FOLLOW_UP_REQUIRED", followup={
        "title": "Verify intake dates", "focus": ["Intakes"], "research_method": "TARGETED_WEB",
        "rationale": "Intake dates were missing"})]))
    result = supervisor_service.review_card("P", card_id)
    follow = research_work_items_repo.get(result["followup_card_id"])
    assert follow["status"] == "PROPOSED"
    assert follow["suggested_from_work_item_id"] == card_id
    assert follow["phase_key"] == "4"
    assert follow["prompt_text"].startswith("ROLE: analyst.")
    assert research_work_items_repo.list_dependencies(card_id) == []


def test_complete_on_a_card_already_flagged_for_a_person_waits_for_them(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid, human_review_required=True)
    _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    supervisor_service.review_card("P", card_id)
    assert research_work_items_repo.get(card_id)["status"] == "WAITING_FOR_HUMAN"


def test_a_failed_review_call_releases_the_claim(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    _install(monkeypatch, _FakeClient(error=RuntimeError("Anthropic is down")))
    result = supervisor_service.review_card("P", card_id)
    assert result["reviewed"] is False
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"
    assert agent_decisions_repo.list_for_project(pid)[0]["decision_type"] == "review_error"


def test_a_card_already_claimed_is_not_reviewed_twice(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    research_task_service.claim_transition(card_id, "RUNNING", "REVIEWING")
    client = _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    assert supervisor_service.review_card("P", card_id)["reviewed"] is False
    assert client.calls == []


@pytest.mark.parametrize("run_status,output", [("running", None), ("failed", None), ("completed", "")])
def test_cards_whose_run_has_not_produced_a_report_are_not_reviewed(temp_db, app_context, monkeypatch, run_status, output):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid, output=output, run_status=run_status)
    client = _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    assert supervisor_service.review_card("P", card_id)["reviewed"] is False
    assert client.calls == []
    assert research_work_items_repo.get(card_id)["status"] == "RUNNING"


def test_review_sees_the_report_clipped_to_the_existing_limit(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid, output="x" * (supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS + 5000))
    client = _install(monkeypatch, _FakeClient([_review("COMPLETE")]))
    supervisor_service.review_card("P", card_id)
    content = client.calls[0]["messages"][0]["content"]
    assert "x" * supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS in content
    assert "x" * (supervisor_service.MAX_RUN_OUTPUT_REVIEW_CHARS + 1) not in content
```

Move the three new imports (`json`, `SimpleNamespace` and `from services import llm_service, research_task_service`) to the top of the file with the existing imports, and drop the `noqa` comments.

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_supervisor_service.py tests/test_routes_supervisor.py -v`
Expected: the new tests FAIL (`draft_researches` / `review_card` don't exist, the old handlers still do, and the route returns 200/500). The `build_context` tests still PASS.

- [ ] **Step 4: Rewrite `services/supervisor_service.py` below `build_context`**

Keep the module top as it is, through the end of `build_context`: constants, `_clip_run_output`, `_awaiting_review`, `_format_work_item`, `build_context`. Make one change inside `build_context`: replace the phase-definitions block with one that also lists each phase's frameworks:

```python
    blocks.append(
        "# PHASE DEFINITIONS\n\n"
        + "\n".join(
            f"- {key}: {defn['title']} (frameworks: "
            + ", ".join(f"{fk} = {FRAMEWORK_LABELS[fk]}" for fk in frameworks_for_phase(key))
            + ")"
            for key, defn in PHASE_DEFINITIONS.items()
        )
    )
```

Set the imports at the top to:

```python
import json

import anthropic
from flask import current_app

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo

from . import insights_service, research_task_service
from .phases import PHASE_DEFINITIONS
from .project_service import load_project_prompt
from .prompt_drafting_service import create_drafted_card
from .prompt_frameworks import FRAMEWORK_LABELS, frameworks_for_phase, resolve_framework
```

Delete everything after `build_context` and replace it with:

```python
DRAFTABLE_PHASES = ("1", "2", "3", "4", "5", "6")
DRAFTABLE_METHODS = ("TARGETED_WEB", "FILE_ANALYSIS")
REVIEW_OUTCOMES = ("COMPLETE", "FOLLOW_UP_REQUIRED", "NEEDS_HUMAN", "FAILED")
MAX_REVIEW_PROMPT_CHARS = 6000

DRAFTING_SYSTEM_PROMPT = (
    "You are the research supervisor for a competitive-intelligence project in Australian "
    "higher education. Research is organised in 7 fixed phases. Your job here is ONLY to draft "
    "researches for the person to review: you cannot approve or start anything, and nothing "
    "runs until they approve it. Read the objective, the existing researches (including skipped "
    "ones, which the person rejected -- do not propose them again) and any review gaps, then "
    "either propose the most useful next researches for Phases 1-6 or, if nothing useful can be "
    "added, call no_action. Prefer phases with no research yet. Choose TARGETED_WEB for public "
    "web information and FILE_ANALYSIS when the answer is likely in the project's uploaded files. "
    "Pick the framework that fits each research from the phase's list. Phase 7 is never drafted here."
)

PROPOSE_TASKS_TOOL = {
    "name": "propose_tasks",
    "description": "Draft one or more researches for the person to review. Each becomes a card that needs their approval before it can run.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "phase_key": {"type": "string", "enum": list(DRAFTABLE_PHASES)},
                        "title": {"type": "string"},
                        "focus": {"type": "array", "items": {"type": "string"}},
                        "research_method": {"type": "string", "enum": list(DRAFTABLE_METHODS)},
                        "framework_key": {"type": "string", "description": "One of the frameworks listed for this phase in PHASE DEFINITIONS."},
                        "rationale": {"type": "string", "description": "One or two plain sentences on why this research is needed now."},
                        "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                        "depends_on_existing_ids": {"type": "array", "items": {"type": "integer"}},
                        "depends_on_batch_indices": {"type": "array", "items": {"type": "integer"}, "description": "Zero-based indices into this same tasks array."},
                    },
                    "required": ["phase_key", "title", "research_method", "rationale"],
                },
            },
            "reason": {"type": "string"},
        },
        "required": ["tasks", "reason"],
    },
}

NO_ACTION_TOOL = {
    "name": "no_action",
    "description": "Nothing useful can be drafted right now.",
    "input_schema": {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
    },
}

DRAFTING_TOOLS = [PROPOSE_TASKS_TOOL, NO_ACTION_TOOL]

REVIEW_SYSTEM_PROMPT = (
    "You are the research supervisor reviewing ONE finished research for an Australian "
    "higher-education competitive-intelligence project. Judge only what you are shown. Score "
    "completeness (did it answer the prompt?) and evidence (are claims sourced?) from 0 to 1, "
    "list concrete gaps, and choose an outcome: COMPLETE if it is good enough, FOLLOW_UP_REQUIRED "
    "if specific gaps need another research (describe that research in followup), NEEDS_HUMAN if "
    "a person must judge it, FAILED if it produced nothing usable. Never claim the report contains "
    "something you were not shown."
)

REVIEW_TOOL = {
    "name": "review_outcome",
    "description": "Record your review of this finished research.",
    "input_schema": {
        "type": "object",
        "properties": {
            "completeness_score": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_score": {"type": "number", "minimum": 0, "maximum": 1},
            "identified_gaps": {"type": "array", "items": {"type": "string"}},
            "outcome": {"type": "string", "enum": list(REVIEW_OUTCOMES)},
            "reason": {"type": "string"},
            "followup": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "focus": {"type": "array", "items": {"type": "string"}},
                    "research_method": {"type": "string", "enum": list(DRAFTABLE_METHODS)},
                    "rationale": {"type": "string"},
                },
                "required": ["title"],
            },
        },
        "required": ["completeness_score", "evidence_score", "outcome", "reason"],
    },
}


def _client():
    return anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])


def _tool_use(response, name=None):
    return next(
        (b for b in response.content if b.type == "tool_use" and (name is None or b.name == name)), None
    )


def _batch_has_cycle(tasks_input):
    edges = {i: list(t.get("depends_on_batch_indices") or []) for i, t in enumerate(tasks_input)}
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


def _validate_proposals(tasks_input):
    if not tasks_input:
        raise ValueError("No researches were proposed")
    for t in tasks_input:
        if t.get("phase_key") == "7":
            raise ValueError("Phase 7 (the options report) is drafted from its own button, not proposed")
        if t.get("phase_key") not in DRAFTABLE_PHASES:
            raise ValueError(f"Unknown phase: {t.get('phase_key')!r}")
        if not (t.get("title") or "").strip():
            raise ValueError("Every research needs a title")
        if t.get("research_method") not in DRAFTABLE_METHODS:
            raise ValueError(f"Unknown research method: {t.get('research_method')!r}")
        for dep_index in t.get("depends_on_batch_indices") or []:
            if not isinstance(dep_index, int) or dep_index < 0 or dep_index >= len(tasks_input):
                raise ValueError(
                    f"depends_on_batch_indices value {dep_index!r} is out of range for a batch of {len(tasks_input)}"
                )
    if _batch_has_cycle(tasks_input):
        raise ValueError("These researches depend on each other in a loop")


def handle_propose_tasks(project_name, tool_input):
    tasks_input = tool_input.get("tasks") or []
    _validate_proposals(tasks_input)  # everything is checked before anything is created
    new_ids = []
    drafted = 0
    for t in tasks_input:
        created = create_drafted_card(
            project_name, t["phase_key"], t["title"].strip(), research_method=t["research_method"],
            focus=t.get("focus") or None, rationale=t.get("rationale"),
            framework_key=resolve_framework(t["phase_key"], t.get("framework_key")),
            priority=t.get("priority") if t.get("priority") in ("low", "medium", "high") else None,
        )
        new_ids.append(created["card_id"])
        drafted += 1 if created["drafted"] else 0
    for i, t in enumerate(tasks_input):
        for dep_id in t.get("depends_on_existing_ids") or []:
            research_task_service.add_dependency(new_ids[i], dep_id)
        for dep_index in t.get("depends_on_batch_indices") or []:
            research_task_service.add_dependency(new_ids[i], new_ids[dep_index])
    return {"created_task_ids": new_ids, "drafted_from_framework": drafted}


def handle_no_action(project_name, tool_input):
    return {"message": "No action taken."}


DRAFTING_HANDLERS = {"propose_tasks": handle_propose_tasks, "no_action": handle_no_action}


def draft_researches(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    response = _client().messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=4000,
        system=DRAFTING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_context(project_name)}],
        tools=DRAFTING_TOOLS,
        tool_choice={"type": "any"},
    )
    block = _tool_use(response)
    if block is None:
        raise RuntimeError("The Supervisor did not return a decision.")

    handler = DRAFTING_HANDLERS.get(block.name)
    try:
        if handler is None:
            raise ValueError(f"The Supervisor chose a tool it is not allowed to use: {block.name}")
        execution = {"success": True, "result": handler(project_name, block.input)}
    except ValueError as exc:
        execution = {"success": False, "error": str(exc)}

    agent_decisions_repo.record(
        project_id, decision_type=block.name, detail=json.dumps({"input": block.input, "execution": execution}),
    )
    return {"action": block.name, "input": block.input, "execution": execution}


def create_followup_card(project_name, original, followup):
    method = followup.get("research_method")
    if method not in DRAFTABLE_METHODS:
        method = original["research_method"] if original["research_method"] in DRAFTABLE_METHODS else "TARGETED_WEB"
    created = create_drafted_card(
        project_name, original["phase_key"], (followup.get("title") or f"Follow-up: {original['title']}").strip(),
        research_method=method, focus=followup.get("focus") or None,
        rationale=followup.get("rationale") or f"Fills gaps found in \"{original['title']}\".",
        framework_key=original["framework_key"], suggested_from_work_item_id=original["id"],
    )
    return created["card_id"]


def _review_context(project_name, card, run):
    objective = load_project_prompt(project_name, "project_prompt") or "(none set)"
    phase_title = PHASE_DEFINITIONS.get(card["phase_key"], {}).get("title", card["phase_key"])
    prompt = _clip_run_output(run["prompt_text"] or card["prompt_text"] or "", MAX_REVIEW_PROMPT_CHARS)
    item_line = _format_work_item(
        card, research_work_items_repo.list_dependencies(card["id"]), run, MAX_RUN_OUTPUT_REVIEW_CHARS
    )
    return (
        f"# PROJECT OBJECTIVE\n\n{objective}\n\n"
        f"# RESEARCH BEING REVIEWED\n\nPhase {card['phase_key']}: {phase_title}\n{item_line}\n\n"
        f"# PROMPT THAT WAS SENT\n\n{prompt}"
    )


def _apply_review(project_name, card, tool_input):
    outcome = tool_input.get("outcome")
    if outcome not in REVIEW_OUTCOMES:
        raise ValueError(f"Unknown review outcome: {outcome!r}")
    research_task_service.set_review_scores(
        card["id"],
        completeness_score=tool_input.get("completeness_score"),
        evidence_score=tool_input.get("evidence_score"),
        identified_gaps=tool_input.get("identified_gaps"),
    )
    followup_id = None
    if outcome == "FOLLOW_UP_REQUIRED" and tool_input.get("followup"):
        followup_id = create_followup_card(project_name, card, tool_input["followup"])
    if outcome == "NEEDS_HUMAN" or (outcome == "COMPLETE" and card["human_review_required"]):
        research_task_service.flag_for_human_review(card["id"])
        new_status = "WAITING_FOR_HUMAN"
    else:
        new_status = outcome
    research_task_service.transition_task(card["id"], new_status)
    return {"outcome": outcome, "new_status": new_status, "followup_card_id": followup_id}


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
        response = _client().messages.create(
            model=current_app.config["ANTHROPIC_MODEL"],
            max_tokens=2000,
            system=REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _review_context(project_name, card, run)}],
            tools=[REVIEW_TOOL],
            tool_choice={"type": "tool", "name": "review_outcome"},
        )
        block = _tool_use(response, "review_outcome")
        if block is None:
            raise RuntimeError("The Supervisor did not return a review.")
        result = _apply_review(project_name, research_work_items_repo.get(card_id), block.input)
    except Exception as exc:
        research_task_service.claim_transition(card_id, "REVIEWING", "RUNNING")
        agent_decisions_repo.record(
            project_id, decision_type="review_error", detail=json.dumps({"error": str(exc)}),
            research_work_item_id=card_id,
        )
        return {"reviewed": False, "error": str(exc)}

    agent_decisions_repo.record(
        project_id, decision_type="review_outcome",
        detail=json.dumps({"input": block.input, "execution": {"success": True, "result": result}}),
        research_work_item_id=card_id,
    )
    return {"reviewed": True, **result}
```

`_format_work_item` already receives the `MAX_RUN_OUTPUT_REVIEW_CHARS` limit, so the reviewer sees exactly what it saw before. `insights_service` is still used by `build_context`.

- [ ] **Step 5: Retire the run endpoint**

In `routes/supervisor.py`, remove the `run_supervisor_cycle` import and replace `run_supervisor_route`'s body with:

```python
@supervisor_bp.route("/api/supervisor/run", methods=["POST"])
def run_supervisor_route():
    return jsonify({
        "success": False,
        "error": "The Supervisor now runs from the Research tab. It drafts and reviews; you approve and run.",
    }), 410
```

- [ ] **Step 6: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_supervisor_service.py tests/test_routes_supervisor.py -v` (expected: PASS). Then run `python -m ruff check .` and `python -m pytest tests/ -q` (expected: clean, all pass). `grep -n "dispatch\|mark_ready\|trigger_synthesis" services/supervisor_service.py` must return nothing.

- [ ] **Step 7: Commit**

```bash
git add services/supervisor_service.py routes/supervisor.py tests/test_supervisor_service.py tests/test_routes_supervisor.py
git commit -m "Cut the Supervisor down to drafting and automatic review; it can no longer start research"
```

---

### Task 7: Board service: card actions, refresh and board state

**Files:**
- Create: `services/board_service.py`, `tests/test_board_service.py`

**Interfaces:**
- Consumes:
  - from Task 2: `research_task_service.transition_task`, `claim_transition` and `phase7_readiness`;
  - from Task 3: `prompt_drafting_service.create_drafted_card`, `draft_prompt` and `FALLBACK_MARKER`;
  - from Tasks 4–5: `research_execution_service.start_runs`, `sync_web_research_runs`, `save_report`, `link_report`, `METHOD_LABELS`, `RUNNABLE_METHODS`, `PHASE7_LOCKED_MESSAGE` and `ABANDONED_LOCAL_RUN_SECONDS`;
  - from Task 6: `supervisor_service.review_card` and `create_followup_card`.
- Produces in `board_service`:
  - exceptions `CardNotFound` (→404), `BoardStateError` (→409) and `DraftingUnavailable` (→503); a plain `ValueError` maps to 400;
  - `create_card(project_name, data) -> card_id`, `edit_card(project_name, card_id, data) -> card_id`, `ACTIONS: dict[str, callable(project_name, card_id)]` (keys `approve`, `unapprove`, `skip`, `restore`, `retry`, `back-to-draft`, `redraft`, `accept`, `needs-followup`, `mark-failed`), and `draft_options_report(project_name) -> card_id`;
  - `run(project_name, card_ids) -> start_runs result`, `save_objective(project_name, text)` and `get_report(project_name, card_id) -> {"title","text","filename"}`;
  - `refresh(project_name, defer_linking=False) -> state` (`state["linked_reports"]: int`) and `get_board_state(project_name) -> state`.
- State shape:
  - top level: `{"project","objective","phases":[{"key","title","frameworks":[{"key","label"}]}],"cards":[card],"phase7":{"ready_phases","ready_count","unlocked","summaries_generated_at","newest_report_at","summaries_stale"},"activity":[{"at","actor","text"}]}`;
  - each card: `{"id","phase_key","title","status","research_method","method_label","framework_key","framework_label","prompt_text","needs_prompt_edit","focus","rationale","priority","suggested_from","followups","depends_on","completeness_score","evidence_score","gaps","human_review_required","retry_count","max_retries","run","review_error","created_at","updated_at"}`;
  - `card.run`: `{"id","status","error","elapsed_seconds","completed_at","has_report","report_filename"}` or `null`.

- [ ] **Step 1: Write the failing tests**

`tests/test_board_service.py`:

```python
import json
from datetime import datetime, timedelta, timezone

import pytest

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo
from services import (
    board_service, insights_service, llm_service, prompt_drafting_service, research_execution_service,
    research_task_service, supervisor_service,
)

GOOD_PROMPT = "Research the fee structures of every online Psychology postgraduate program."


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: "ROLE: analyst. A complete drafted research prompt for this card.")
    monkeypatch.setattr(research_execution_service, "sync_web_research_runs", lambda project_name: None)
    return projects_repo.get_or_create_id("P")


def _card(pid, status="PROPOSED", prompt=GOOD_PROMPT, method="TARGETED_WEB", phase="4", title="Fees", **kw):
    card_id = research_task_service.create_task(pid, phase, title, research_method=method, prompt_text=prompt, **kw)
    if status != "PROPOSED":
        research_work_items_repo.update_fields(card_id, status=status)
    return card_id


def _status(card_id):
    return research_work_items_repo.get(card_id)["status"]


def _last_decision(pid):
    return agent_decisions_repo.list_for_project(pid)[0]["decision_type"]


# ---- editing and approval ----

def test_editing_an_approved_card_sends_it_back_for_approval(pid):
    card_id = _card(pid, status="READY")
    board_service.edit_card("P", card_id, {"prompt_text": GOOD_PROMPT + " Include FEE-HELP."})
    assert _status(card_id) == "PROPOSED"
    assert research_work_items_repo.get(card_id)["prompt_text"].endswith("Include FEE-HELP.")
    assert _last_decision(pid) == "user_edit_unapproved"


def test_saving_an_unchanged_approved_card_keeps_it_approved(pid):
    card_id = _card(pid, status="READY")
    board_service.edit_card("P", card_id, {"prompt_text": GOOD_PROMPT, "title": "Fees"})
    assert _status(card_id) == "READY"


def test_focus_is_saved_as_a_list(pid):
    card_id = _card(pid)
    board_service.edit_card("P", card_id, {"focus": "Fees, , FEE-HELP "})
    assert json.loads(research_work_items_repo.get(card_id)["entities_json"]) == ["Fees", "FEE-HELP"]


@pytest.mark.parametrize("status", ["RUNNING", "COMPLETE", "SKIPPED"])
def test_cards_outside_draft_or_approved_cannot_be_edited(pid, status):
    card_id = _card(pid, status=status)
    with pytest.raises(board_service.BoardStateError):
        board_service.edit_card("P", card_id, {"title": "New"})


def test_approve_moves_a_valid_draft_to_approved(pid):
    card_id = _card(pid)
    board_service.ACTIONS["approve"]("P", card_id)
    assert _status(card_id) == "READY"
    assert _last_decision(pid) == "user_approve"


@pytest.mark.parametrize("prompt,method", [
    ("Too short", "TARGETED_WEB"),
    (None, "TARGETED_WEB"),
    (prompt_drafting_service.FALLBACK_MARKER + "\n\nResearch question: Fees and everything about them", "TARGETED_WEB"),
    (GOOD_PROMPT, None),
])
def test_approve_refuses_cards_that_are_not_ready_to_send(pid, prompt, method):
    card_id = _card(pid, prompt=prompt, method=method)
    with pytest.raises(ValueError):
        board_service.ACTIONS["approve"]("P", card_id)
    assert _status(card_id) == "PROPOSED"


def test_approve_waits_for_dependencies(pid):
    first = _card(pid, title="First")
    second = _card(pid, title="Second")
    research_task_service.add_dependency(second, first)
    with pytest.raises(board_service.BoardStateError, match="waits for another research"):
        board_service.ACTIONS["approve"]("P", second)


def test_approve_of_options_report_needs_phase_7_unlocked(pid):
    card_id = _card(pid, method="SYNTHESIS", phase="7", title="Options")
    with pytest.raises(board_service.BoardStateError):
        board_service.ACTIONS["approve"]("P", card_id)


def test_a_card_from_another_project_is_not_found(pid):
    other = projects_repo.get_or_create_id("Other")
    card_id = _card(other)
    with pytest.raises(board_service.CardNotFound):
        board_service.ACTIONS["approve"]("P", card_id)


# ---- other card actions ----

@pytest.mark.parametrize("action,start,end", [
    ("unapprove", "READY", "PROPOSED"),
    ("skip", "PROPOSED", "SKIPPED"),
    ("skip", "FAILED", "SKIPPED"),
    ("restore", "SKIPPED", "PROPOSED"),
    ("retry", "FAILED", "READY"),
    ("back-to-draft", "FAILED", "PROPOSED"),
    ("back-to-draft", "READY", "PROPOSED"),
    ("accept", "WAITING_FOR_HUMAN", "COMPLETE"),
    ("mark-failed", "WAITING_FOR_HUMAN", "FAILED"),
])
def test_simple_actions(pid, action, start, end):
    card_id = _card(pid, status=start)
    board_service.ACTIONS[action]("P", card_id)
    assert _status(card_id) == end


@pytest.mark.parametrize("action,start", [("restore", "PROPOSED"), ("accept", "COMPLETE"), ("retry", "READY")])
def test_actions_in_the_wrong_state_are_refused(pid, action, start):
    card_id = _card(pid, status=start)
    with pytest.raises(board_service.BoardStateError):
        board_service.ACTIONS[action]("P", card_id)
    assert _status(card_id) == start


def test_retry_is_refused_at_the_retry_limit(pid):
    card_id = _card(pid, status="FAILED")
    research_work_items_repo.update_fields(card_id, retry_count=3)
    with pytest.raises(board_service.BoardStateError, match="too many times"):
        board_service.ACTIONS["retry"]("P", card_id)


def test_needs_followup_creates_a_drafted_follow_up(pid):
    card_id = _card(pid, status="WAITING_FOR_HUMAN")
    research_work_items_repo.update_fields(card_id, identified_gaps_json=json.dumps(["No intake dates"]))
    board_service.ACTIONS["needs-followup"]("P", card_id)
    assert _status(card_id) == "FOLLOW_UP_REQUIRED"
    follow = [c for c in research_work_items_repo.list_for_project(pid) if c["suggested_from_work_item_id"] == card_id][0]
    assert follow["status"] == "PROPOSED"
    assert "No intake dates" in follow["rationale"]


def test_redraft_replaces_the_prompt_and_unapproves(pid):
    card_id = _card(pid, status="READY", framework_key="oes-product-features")
    board_service.ACTIONS["redraft"]("P", card_id)
    row = research_work_items_repo.get(card_id)
    assert row["prompt_text"].startswith("ROLE: analyst.")
    assert row["status"] == "PROPOSED"


def test_redraft_failure_keeps_the_users_prompt(pid, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(llm_service, "prompt_completion", boom)
    card_id = _card(pid, framework_key="oes-product-features")
    with pytest.raises(board_service.DraftingUnavailable):
        board_service.ACTIONS["redraft"]("P", card_id)
    assert research_work_items_repo.get(card_id)["prompt_text"] == GOOD_PROMPT


def test_create_card_with_my_own_prompt(pid):
    card_id = board_service.create_card("P", {"phase_key": "2", "title": "Personas", "research_method": "FILE_ANALYSIS",
                                              "focus": ["Career changers"], "prompt_text": GOOD_PROMPT, "draft_prompt": False})
    row = research_work_items_repo.get(card_id)
    assert (row["status"], row["phase_key"], row["prompt_text"]) == ("PROPOSED", "2", GOOD_PROMPT)
    assert row["framework_key"] == "oes-student-persona"


def test_create_card_drafted_for_me(pid):
    card_id = board_service.create_card("P", {"phase_key": "3", "title": "Sentiment", "research_method": "TARGETED_WEB",
                                              "framework_key": "oes-marketing-sentiment", "draft_prompt": True})
    row = research_work_items_repo.get(card_id)
    assert row["prompt_text"].startswith("ROLE: analyst.")
    assert row["framework_key"] == "oes-marketing-sentiment"


@pytest.mark.parametrize("data", [
    {"phase_key": "7", "title": "X", "research_method": "TARGETED_WEB"},
    {"phase_key": "4", "title": "  ", "research_method": "TARGETED_WEB"},
    {"phase_key": "4", "title": "X", "research_method": "SYNTHESIS"},
])
def test_create_card_validation(pid, data):
    with pytest.raises(ValueError):
        board_service.create_card("P", data)


def _unlock_phase_7(pid):
    for phase in ("1", "2", "3", "4", "5", "6"):
        _card(pid, status="COMPLETE", phase=phase, title=f"Done {phase}")


def test_options_report_draft_needs_phase_7_unlocked_and_only_one_open(pid):
    with pytest.raises(board_service.BoardStateError):
        board_service.draft_options_report("P")
    _unlock_phase_7(pid)
    card_id = board_service.draft_options_report("P")
    row = research_work_items_repo.get(card_id)
    assert (row["phase_key"], row["research_method"], row["framework_key"]) == ("7", "SYNTHESIS", "oes-options-whitespace")
    with pytest.raises(board_service.BoardStateError):
        board_service.draft_options_report("P")


def test_run_records_the_users_run(pid, monkeypatch):
    monkeypatch.setattr(research_execution_service, "start_runs",
                        lambda project_name, ids: {"started": list(ids), "not_ready": [], "failed": []})
    result = board_service.run("P", ["5", 6])
    assert result["started"] == [5, 6]
    assert _last_decision(pid) == "user_run"


# ---- refresh ----

def _finished_run(pid, card_id, run_id="run_1", prompt_text=GOOD_PROMPT, status="completed", output="Report body."):
    research_runs_repo.create(run_id, pid, "resp", None, "preview")
    research_runs_repo.update(run_id, research_work_item_id=card_id, status=status, output_text=output,
                              prompt_text=prompt_text, completed_at=datetime.now().isoformat() if status != "running" else None)


@pytest.fixture
def reviews(monkeypatch):
    calls = []
    monkeypatch.setattr(supervisor_service, "review_card", lambda project_name, card_id: calls.append(card_id) or {"reviewed": True})
    return calls


def test_refresh_saves_links_and_reviews_a_finished_research(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id)
    state = board_service.refresh("P")
    run = research_runs_repo.get("run_1")
    assert run["report_stable_file_id"] and run["report_linked_at"]
    assert state["linked_reports"] == 1
    assert reviews == [card_id]


def test_refresh_defers_linking_while_insights_are_generating(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id)
    board_service.refresh("P", defer_linking=True)
    assert research_runs_repo.get("run_1")["report_stable_file_id"]
    assert research_runs_repo.get("run_1")["report_linked_at"] is None
    assert board_service.refresh("P")["linked_reports"] == 1


def test_refresh_fails_a_card_whose_run_failed(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id, status="failed", output=None)
    board_service.refresh("P")
    assert _status(card_id) == "FAILED"
    assert reviews == []


def test_runs_from_before_the_board_get_no_report_file(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id, prompt_text=None)
    board_service.refresh("P")
    assert research_runs_repo.get("run_1")["report_stable_file_id"] is None
    assert reviews == [card_id]


def test_refresh_fails_a_my_files_run_cut_off_by_a_restart(pid, reviews):
    card_id = _card(pid, status="RUNNING", method="FILE_ANALYSIS")
    _finished_run(pid, card_id, status="running", output=None)
    old = (datetime.now() - timedelta(seconds=research_execution_service.ABANDONED_LOCAL_RUN_SECONDS + 60)).isoformat()
    research_runs_repo.update("run_1", created_at=old)
    board_service.refresh("P")
    assert research_runs_repo.get("run_1")["status"] == "failed"
    assert _status(card_id) == "FAILED"


def test_refresh_releases_a_stale_review_claim(pid, reviews):
    card_id = _card(pid, status="REVIEWING")
    old = (datetime.now() - timedelta(seconds=board_service.STALE_REVIEW_CLAIM_SECONDS + 60)).isoformat()
    from db.connection import get_connection
    with get_connection() as conn:
        conn.execute("UPDATE research_work_items SET updated_at = ? WHERE id = ?", (old, card_id))
    board_service.refresh("P")
    assert _status(card_id) == "RUNNING"


def test_a_refresh_already_in_progress_is_not_repeated(pid, reviews):
    card_id = _card(pid, status="RUNNING")
    _finished_run(pid, card_id)
    lock = board_service._lock_for("P")
    lock.acquire()
    try:
        state = board_service.refresh("P")
    finally:
        lock.release()
    assert state["linked_reports"] == 0
    assert reviews == []


# ---- state ----

def test_state_describes_cards_for_the_board(pid):
    origin = _card(pid, status="FOLLOW_UP_REQUIRED", title="Origin")
    follow = _card(pid, title="Follow", suggested_from_work_item_id=origin, framework_key="oes-product-features")
    state = board_service.get_board_state("P")
    cards = {c["id"]: c for c in state["cards"]}
    assert cards[follow]["suggested_from"] == {"id": origin, "title": "Origin"}
    assert cards[origin]["followups"][0]["id"] == follow
    assert cards[follow]["framework_label"] == "Phase 4: Product Features"
    assert cards[follow]["method_label"] == "Web research"
    assert [p["key"] for p in state["phases"]] == ["1", "2", "3", "4", "5", "6", "7"]
    assert len(state["phases"][2]["frameworks"]) == 3


def test_state_flags_placeholder_prompts(pid):
    card_id = _card(pid, prompt=prompt_drafting_service.FALLBACK_MARKER + "\n\nResearch question: Fees")
    card = next(c for c in board_service.get_board_state("P")["cards"] if c["id"] == card_id)
    assert card["needs_prompt_edit"] is True


def _local_naive(utc_dt):
    return utc_dt.astimezone().replace(tzinfo=None).isoformat()


@pytest.mark.parametrize("generated_at,stale", [
    ("2026-10-05T01:00:00.000Z", True),   # summaries refreshed 30 minutes BEFORE the newest report
    ("2026-10-05T02:00:00.000Z", False),  # summaries refreshed AFTER it
])
def test_stale_summaries_compare_browser_utc_with_server_local_time(pid, generated_at, stale):
    insights_service.save_insights("P", {"generated_at": generated_at, "competitors": [],
                                         "competitor_landscape_markdown": "", "phases": {}})
    card_id = _card(pid, status="COMPLETE")
    _finished_run(pid, card_id)
    report_time = _local_naive(datetime(2026, 10, 5, 1, 30, tzinfo=timezone.utc))
    research_runs_repo.update("run_1", completed_at=report_time, report_stable_file_id="f_x")
    assert board_service.get_board_state("P")["phase7"]["summaries_stale"] is stale


def test_activity_reads_in_plain_words(pid):
    card_id = _card(pid)
    board_service.ACTIONS["approve"]("P", card_id)
    activity = board_service.get_board_state("P")["activity"]
    assert activity[0] == {"at": activity[0]["at"], "actor": "you", "text": 'You approved "Fees".'}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_board_service.py -v`
Expected: FAIL with `ImportError` (no `board_service`).

- [ ] **Step 3: Implement `services/board_service.py`**

```python
# services/board_service.py
"""The Research Board: the user's card actions, the board's refresh cycle, and the board state.

Only these user actions approve (-> READY) or run (-> RUNNING via start_runs) research.
"""
import json
import threading
from datetime import datetime, timezone

from db.repositories import (
    agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo, sources_repo,
)

from . import insights_service, research_execution_service, research_run_service, research_task_service, supervisor_service
from .phases import PHASE_DEFINITIONS
from .project_service import load_project_prompt, save_project_prompt
from .prompt_drafting_service import FALLBACK_MARKER, create_drafted_card, draft_prompt
from .prompt_frameworks import FRAMEWORK_LABELS, frameworks_for_phase, resolve_framework

MIN_PROMPT_CHARS = 40
STALE_REVIEW_CLAIM_SECONDS = 600
ACTIVITY_LIMIT = 30
USER_PHASES = ("1", "2", "3", "4", "5", "6")
USER_METHODS = ("TARGETED_WEB", "FILE_ANALYSIS")
EDITABLE_STATUSES = ("PROPOSED", "READY")
OPEN_STATUSES = ("PROPOSED", "READY", "RUNNING", "REVIEWING")
OPTIONS_REPORT_TITLE = "Options for OES report"


class CardNotFound(LookupError):
    """No such card in this project (HTTP 404)."""


class BoardStateError(ValueError):
    """The card is not in a state that allows this action (HTTP 409)."""


class DraftingUnavailable(RuntimeError):
    """Claude could not draft a prompt just now (HTTP 503)."""


# ---- helpers ----

def _project_id(project_name):
    return projects_repo.get_or_create_id(project_name)


def _card(project_name, card_id):
    card = research_work_items_repo.get(card_id)
    if card is None or card["project_id"] != projects_repo.get_id(project_name):
        raise CardNotFound(f"No such research: {card_id}")
    return card


def _require_status(card, allowed):
    if card["status"] not in allowed:
        raise BoardStateError("This research has changed since the board was loaded. The board has been refreshed.")


_FRIENDLY_TRANSITION_ERRORS = (
    ("dependency", "This research waits for another research to finish first."),
    ("Retry limit", "This research has failed too many times to retry. Move it back to draft to change it, or skip it."),
)


def _transition(card_id, to_status):
    try:
        research_task_service.transition_task(card_id, to_status)
    except ValueError as exc:
        for needle, message in _FRIENDLY_TRANSITION_ERRORS:
            if needle in str(exc):
                raise BoardStateError(message) from exc
        raise BoardStateError(str(exc)) from exc


def _claim(card_id, from_status, to_status):
    if not research_task_service.claim_transition(card_id, from_status, to_status):
        raise BoardStateError("This research has changed since the board was loaded. The board has been refreshed.")


def _record(project_name, decision_type, card=None, **detail):
    if card is not None:
        detail.setdefault("title", card["title"])
    agent_decisions_repo.record(
        _project_id(project_name), decision_type, json.dumps(detail),
        research_work_item_id=card["id"] if card is not None else None,
    )


def _clean_focus(value):
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _focus(card):
    return json.loads(card["entities_json"]) if card["entities_json"] else []


# ---- card actions ----

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
    focus = _clean_focus(data.get("focus"))
    rationale = (data.get("rationale") or "").strip() or None
    if data.get("draft_prompt"):
        card_id = create_drafted_card(
            project_name, phase_key, title, research_method=method, focus=focus or None,
            rationale=rationale, framework_key=data.get("framework_key"),
        )["card_id"]
    else:
        card_id = research_task_service.create_task(
            _project_id(project_name), phase_key, title, research_method=method, entities=focus or None,
            rationale=rationale, framework_key=resolve_framework(phase_key, data.get("framework_key")),
            prompt_text=(data.get("prompt_text") or "").strip() or None,
        )
    _record(project_name, "user_add", research_work_items_repo.get(card_id))
    return card_id


def edit_card(project_name, card_id, data):
    card = _card(project_name, card_id)
    _require_status(card, EDITABLE_STATUSES)
    fields = {}
    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title:
            raise ValueError("Add a title.")
        fields["title"] = title
    if "prompt_text" in data:
        fields["prompt_text"] = data.get("prompt_text") or ""
    if "rationale" in data:
        fields["rationale"] = (data.get("rationale") or "").strip() or None
    if "focus" in data:
        focus = _clean_focus(data.get("focus"))
        fields["entities_json"] = json.dumps(focus) if focus else None
    if "research_method" in data:
        method = data.get("research_method")
        if card["research_method"] == "SYNTHESIS":
            if method != "SYNTHESIS":
                raise ValueError("The options report always runs as the options report.")
        elif method not in USER_METHODS:
            raise ValueError("Choose how it runs: web research or your files.")
        fields["research_method"] = method

    changed = {k: v for k, v in fields.items() if card[k] != v}
    if not changed:
        return card_id
    if card["status"] == "READY":
        _claim(card_id, "READY", "PROPOSED")  # claim first so a Run racing this edit cannot start it
    research_work_items_repo.update_fields(card_id, **changed)
    _record(project_name, "user_edit_unapproved" if card["status"] == "READY" else "user_edit", card)
    return card_id


def approve(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("PROPOSED",))
    prompt = (card["prompt_text"] or "").strip()
    if not (card["title"] or "").strip():
        raise ValueError("Add a title before approving.")
    if card["research_method"] not in research_execution_service.RUNNABLE_METHODS:
        raise ValueError("Choose how it runs before approving.")
    if FALLBACK_MARKER in prompt:
        raise ValueError("This prompt is a placeholder because drafting failed. Edit it, or re-draft it from the framework, before approving.")
    if len(prompt) < MIN_PROMPT_CHARS:
        raise ValueError("The research prompt is too short to send. Describe what the research should find out.")
    if card["research_method"] == "SYNTHESIS" and not research_task_service.phase7_readiness(card["project_id"])["unlocked"]:
        raise BoardStateError(research_execution_service.PHASE7_LOCKED_MESSAGE)
    _transition(card_id, "READY")
    _record(project_name, "user_approve", card)


def unapprove(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("READY",))
    _claim(card_id, "READY", "PROPOSED")
    _record(project_name, "user_unapprove", card)


def skip(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("PROPOSED", "READY", "FAILED"))
    _transition(card_id, "SKIPPED")
    _record(project_name, "user_skip", card)


def restore(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("SKIPPED",))
    _transition(card_id, "PROPOSED")
    _record(project_name, "user_restore", card)


def retry(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("FAILED",))
    _transition(card_id, "READY")
    _record(project_name, "user_retry", card)


def back_to_draft(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("READY", "FAILED"))
    if card["status"] == "READY":
        _claim(card_id, "READY", "PROPOSED")
    else:
        _transition(card_id, "PROPOSED")
    _record(project_name, "user_back_to_draft", card)


def redraft(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, EDITABLE_STATUSES)
    draft = draft_prompt(
        project_name, card["phase_key"], card["framework_key"], card["title"],
        focus=_focus(card) or None, rationale=card["rationale"],
    )
    if not draft["drafted"]:
        raise DraftingUnavailable("Couldn't draft from the framework just now. Your prompt is unchanged.")
    if card["status"] == "READY":
        _claim(card_id, "READY", "PROPOSED")
    research_work_items_repo.update_fields(card_id, prompt_text=draft["prompt_text"], framework_key=draft["framework_key"])
    _record(project_name, "user_redraft", card)


def accept(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("WAITING_FOR_HUMAN",))
    _transition(card_id, "COMPLETE")
    _record(project_name, "user_accept", card)


def needs_followup(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("WAITING_FOR_HUMAN",))
    _transition(card_id, "REVIEWING")
    _transition(card_id, "FOLLOW_UP_REQUIRED")
    gaps = json.loads(card["identified_gaps_json"]) if card["identified_gaps_json"] else []
    rationale = "You asked for a follow-up." + (f" Gaps found: {'; '.join(gaps)}" if gaps else "")
    supervisor_service.create_followup_card(
        project_name, card, {"title": f"Follow-up: {card['title']}", "focus": _focus(card), "rationale": rationale},
    )
    _record(project_name, "user_needs_followup", card)


def mark_failed(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, ("WAITING_FOR_HUMAN",))
    _transition(card_id, "FAILED")
    _record(project_name, "user_mark_failed", card)


ACTIONS = {
    "approve": approve, "unapprove": unapprove, "skip": skip, "restore": restore, "retry": retry,
    "back-to-draft": back_to_draft, "redraft": redraft, "accept": accept,
    "needs-followup": needs_followup, "mark-failed": mark_failed,
}


def draft_options_report(project_name):
    project_id = _project_id(project_name)
    if not research_task_service.phase7_readiness(project_id)["unlocked"]:
        raise BoardStateError(research_execution_service.PHASE7_LOCKED_MESSAGE)
    if any(c["status"] in OPEN_STATUSES for c in research_work_items_repo.list_for_phase(project_id, "7")):
        raise BoardStateError("An options report is already in your plan.")
    card_id = create_drafted_card(
        project_name, "7", OPTIONS_REPORT_TITLE, research_method="SYNTHESIS",
        rationale="Phases 1 to 6 each have finished research.", framework_key="oes-options-whitespace",
    )["card_id"]
    _record(project_name, "user_draft_options", research_work_items_repo.get(card_id))
    return card_id


def run(project_name, card_ids):
    try:
        ids = [int(i) for i in card_ids]
    except (TypeError, ValueError) as exc:
        raise ValueError("card_ids must be a list of research ids") from exc
    result = research_execution_service.start_runs(project_name, ids)
    if result["started"]:
        _record(project_name, "user_run", count=len(result["started"]))
    return result


def save_objective(project_name, objective):
    save_project_prompt(project_name, "project_prompt", objective or "")
    _record(project_name, "user_objective")


def get_report(project_name, card_id):
    card = _card(project_name, card_id)
    run_row = research_runs_repo.find_latest_for_work_item(card_id)
    if run_row is None or not run_row["output_text"]:
        raise CardNotFound("This research has no report yet.")
    filename = None
    if run_row["report_stable_file_id"]:
        source = sources_repo.get_by_stable_id(card["project_id"], run_row["report_stable_file_id"])
        filename = source["filename"] if source else None
    return {"title": card["title"], "text": run_row["output_text"], "filename": filename}


# ---- refresh ----

_refresh_locks = {}
_refresh_locks_guard = threading.Lock()


def _lock_for(project_name):
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(project_name, threading.Lock())


def _age_seconds(iso_value):
    try:
        return (datetime.now() - datetime.fromisoformat(iso_value)).total_seconds()
    except (TypeError, ValueError):
        return 0


def _release_stale_review_claims(project_id):
    for card in research_work_items_repo.list_for_project(project_id):
        if card["status"] == "REVIEWING" and _age_seconds(card["updated_at"]) > STALE_REVIEW_CLAIM_SECONDS:
            research_task_service.claim_transition(card["id"], "REVIEWING", "RUNNING")


def _fail_abandoned_local_runs(project_name, project_id):
    for card in research_work_items_repo.list_for_project(project_id):
        if card["status"] != "RUNNING" or card["research_method"] not in ("FILE_ANALYSIS", "SYNTHESIS"):
            continue
        run_row = research_runs_repo.find_latest_for_work_item(card["id"])
        if (run_row and run_row["status"] == "running"
                and _age_seconds(run_row["created_at"]) > research_execution_service.ABANDONED_LOCAL_RUN_SECONDS):
            research_run_service.fail_run(
                project_name, run_row["id"], "Stopped before finishing. The app may have restarted while it was running."
            )


def _settle_card(project_name, card, defer_linking):
    run_row = research_runs_repo.find_latest_for_work_item(card["id"])
    if run_row is None:
        return 0
    if card["status"] == "RUNNING" and run_row["status"] in ("failed", "cancelled"):
        research_task_service.transition_task(card["id"], "FAILED")
        return 0
    # The options report writes Insights itself and is never reviewed.
    if card["research_method"] == "SYNTHESIS" or run_row["status"] != "completed" or not run_row["output_text"]:
        return 0
    linked = 0
    if run_row["prompt_text"]:  # only runs the board started produce report files
        research_execution_service.save_report(project_name, card["id"], run_row["id"])
        if not defer_linking and research_execution_service.link_report(project_name, card["id"], run_row["id"]):
            linked = 1
    if card["status"] == "RUNNING":
        supervisor_service.review_card(project_name, card["id"])
    return linked


def refresh(project_name, defer_linking=False):
    project_id = _project_id(project_name)
    linked = 0
    lock = _lock_for(project_name)
    if lock.acquire(blocking=False):  # another tab's refresh is already doing this work
        try:
            research_execution_service.sync_web_research_runs(project_name)
            _release_stale_review_claims(project_id)
            _fail_abandoned_local_runs(project_name, project_id)
            for card in research_work_items_repo.list_for_project(project_id):
                try:
                    linked += _settle_card(project_name, card, defer_linking)
                except Exception as exc:  # one bad card must not stop the others
                    print(f"[board] could not settle research {card['id']}: {exc}")
        finally:
            lock.release()
    state = get_board_state(project_name)
    state["linked_reports"] = linked
    return state


# ---- state ----

def _to_utc(value):
    """Browser timestamps are UTC ('...Z'); server timestamps are naive local time."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _run_state(project_id, run_row):
    if run_row is None:
        return None
    try:
        start = datetime.fromisoformat(run_row["created_at"])
        end = datetime.fromisoformat(run_row["completed_at"]) if run_row["completed_at"] else datetime.now()
        elapsed = max(0, int((end - start).total_seconds()))
    except (TypeError, ValueError):
        elapsed = None
    filename = None
    if run_row["report_stable_file_id"]:
        source = sources_repo.get_by_stable_id(project_id, run_row["report_stable_file_id"])
        filename = source["filename"] if source else None
    return {
        "id": run_row["id"], "status": run_row["status"], "error": run_row["error"],
        "elapsed_seconds": elapsed, "completed_at": run_row["completed_at"],
        "has_report": bool(run_row["output_text"]), "report_filename": filename,
    }


def _brief(card):
    return {"id": card["id"], "title": card["title"], "status": card["status"]}


def _card_state(project_id, card, cards, by_id, run_row, last_review_event):
    prompt = card["prompt_text"] or ""
    review_error = None
    if card["status"] == "RUNNING" and last_review_event and last_review_event["decision_type"] == "review_error":
        review_error = json.loads(last_review_event["detail"] or "{}").get("error") or "Review failed"
    suggested = by_id.get(card["suggested_from_work_item_id"])
    deps = [by_id.get(r["depends_on_work_item_id"]) for r in research_work_items_repo.list_dependencies(card["id"])]
    return {
        "id": card["id"], "phase_key": card["phase_key"], "title": card["title"], "status": card["status"],
        "research_method": card["research_method"],
        "method_label": research_execution_service.METHOD_LABELS.get(card["research_method"]),
        "framework_key": card["framework_key"], "framework_label": FRAMEWORK_LABELS.get(card["framework_key"]),
        "prompt_text": card["prompt_text"],
        "needs_prompt_edit": not prompt.strip() or FALLBACK_MARKER in prompt,
        "focus": _focus(card), "rationale": card["rationale"], "priority": card["priority"],
        "suggested_from": {"id": suggested["id"], "title": suggested["title"]} if suggested else None,
        "followups": [_brief(c) for c in cards if c["suggested_from_work_item_id"] == card["id"]],
        "depends_on": [_brief(d) for d in deps if d is not None],
        "completeness_score": card["completeness_score"], "evidence_score": card["evidence_score"],
        "gaps": json.loads(card["identified_gaps_json"]) if card["identified_gaps_json"] else [],
        "human_review_required": bool(card["human_review_required"]),
        "retry_count": card["retry_count"], "max_retries": card["max_retries"],
        "run": _run_state(project_id, run_row), "review_error": review_error,
        "created_at": card["created_at"], "updated_at": card["updated_at"],
    }


def _phase7_state(project_name, project_id, run_rows):
    readiness = research_task_service.phase7_readiness(project_id)
    generated_at = insights_service.load_current_insights(project_name).get("generated_at") or ""
    report_times = [
        (r["completed_at"], _to_utc(r["completed_at"])) for r in run_rows
        if r is not None and r["report_stable_file_id"] and r["completed_at"]
    ]
    report_times = [(raw, parsed) for raw, parsed in report_times if parsed is not None]
    newest = max(report_times, key=lambda pair: pair[1]) if report_times else None
    generated = _to_utc(generated_at) if generated_at else None
    stale = newest is not None and (generated is None or generated < newest[1])
    return {
        **readiness, "summaries_generated_at": generated_at,
        "newest_report_at": newest[0] if newest else None, "summaries_stale": stale,
    }


_USER_TEXT = {
    "user_add": 'You added "{title}".',
    "user_edit": 'You edited "{title}".',
    "user_edit_unapproved": 'You edited "{title}". It needs your approval again.',
    "user_approve": 'You approved "{title}".',
    "user_unapprove": 'You moved "{title}" back to draft.',
    "user_back_to_draft": 'You moved "{title}" back to draft.',
    "user_skip": 'You skipped "{title}".',
    "user_restore": 'You restored "{title}" to the plan.',
    "user_retry": 'You approved "{title}" to run again.',
    "user_redraft": 'You re-drafted the prompt for "{title}".',
    "user_accept": 'You accepted "{title}" as finished.',
    "user_needs_followup": 'You asked for a follow-up to "{title}".',
    "user_mark_failed": 'You marked "{title}" as failed.',
    "user_draft_options": "You drafted the options report.",
    "user_objective": "You updated the objective.",
}

_OUTCOME_TEXT = {
    "COMPLETE": "finished",
    "FOLLOW_UP_REQUIRED": "finished, with a follow-up suggested for your approval",
    "NEEDS_HUMAN": "it needs your review",
    "FAILED": "it did not produce a usable result",
}


def _activity_item(decision, by_id):
    try:
        detail = json.loads(decision["detail"] or "{}")
    except ValueError:
        detail = {}
    card = by_id.get(decision["research_work_item_id"])
    title = card["title"] if card else detail.get("title", "a research")
    dtype = decision["decision_type"]
    actor = "supervisor"
    if dtype == "user_run":
        actor, count = "you", detail.get("count", 0)
        text = f"You started {count} {'research' if count == 1 else 'researches'}."
    elif dtype in _USER_TEXT:
        actor, text = "you", _USER_TEXT[dtype].format(title=title)
    elif dtype == "propose_tasks":
        execution = detail.get("execution", {})
        if execution.get("success"):
            n = len(execution.get("result", {}).get("created_task_ids", []))
            text = f"Supervisor drafted {n} new {'research' if n == 1 else 'researches'}. Nothing runs until you approve."
        else:
            text = f"Supervisor's draft was rejected: {execution.get('error', 'unknown error')}"
    elif dtype == "no_action":
        reason = detail.get("input", {}).get("reason")
        text = f"Supervisor had nothing new to propose: {reason}" if reason else "Supervisor had nothing new to propose."
    elif dtype == "review_outcome":
        outcome = detail.get("input", {}).get("outcome")
        text = f'Supervisor reviewed "{title}": {_OUTCOME_TEXT.get(outcome, outcome)}.'
    elif dtype == "review_error":
        text = f'The review of "{title}" didn\'t complete. It will be retried.'
    else:
        text = f"Supervisor: {dtype.replace('_', ' ')}"
    return {"at": decision["created_at"], "actor": actor, "text": text}


def get_board_state(project_name):
    project_id = _project_id(project_name)
    cards = research_work_items_repo.list_for_project(project_id)
    by_id = {c["id"]: c for c in cards}
    runs = {c["id"]: research_runs_repo.find_latest_for_work_item(c["id"]) for c in cards}
    decisions = agent_decisions_repo.list_for_project(project_id, limit=200)
    last_review_event = {}
    for d in decisions:  # newest first
        wid = d["research_work_item_id"]
        if wid is not None and wid not in last_review_event and d["decision_type"] in ("review_outcome", "review_error"):
            last_review_event[wid] = d
    return {
        "project": project_name,
        "objective": load_project_prompt(project_name, "project_prompt") or "",
        "phases": [
            {"key": key, "title": definition["title"],
             "frameworks": [{"key": fk, "label": FRAMEWORK_LABELS[fk]} for fk in frameworks_for_phase(key)]}
            for key, definition in PHASE_DEFINITIONS.items()
        ],
        "cards": [_card_state(project_id, c, cards, by_id, runs[c["id"]], last_review_event.get(c["id"])) for c in cards],
        "phase7": _phase7_state(project_name, project_id, runs.values()),
        "activity": [_activity_item(d, by_id) for d in decisions[:ACTIVITY_LIMIT]],
    }
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_board_service.py -v` (expected: PASS). Then run `python -m ruff check .` and `python -m pytest tests/ -q` (expected: clean, all pass).

- [ ] **Step 5: Commit**

```bash
git add services/board_service.py tests/test_board_service.py
git commit -m "Add board service: user card actions, refresh cycle and board state"
```

---

### Task 8: Board API routes

**Files:**
- Create: `routes/board.py`, `tests/test_routes_board.py`
- Modify: `app.py` (register `board_bp`)

**Interfaces:**
- Consumes: everything `board_service` produces (Task 7), plus `supervisor_service.draft_researches` (Task 6).
- Produces (all JSON). Every success returns `{"success": true, "board": <state>, ...}`. Every error returns `{"success": false, "error": str}` with 400, 404, 409, 503 or 500.
  - `GET /api/board?project=`
  - `POST /api/board/refresh` `{project, defer_linking}` (adds `linked_reports` inside `board`)
  - `POST /api/board/draft` `{project}` (adds `result`, `note`)
  - `POST /api/board/cards` `{project, phase_key, title, research_method, focus, rationale, prompt_text, draft_prompt, framework_key}` (adds `card_id`)
  - `PATCH /api/board/cards/<id>` `{project, title?, prompt_text?, focus?, research_method?, rationale?}`
  - `POST /api/board/cards/<id>/<action>` `{project}`
  - `POST /api/board/run` `{project, card_ids: [int]}` (adds `result`)
  - `GET /api/board/cards/<id>/report?project=` (returns `{"success": true, "report": {"title","text","filename"}}`; no `board`)
  - `POST /api/board/phase7/draft` `{project}` (adds `card_id`)
  - `PUT /api/board/objective` `{project, objective}`

- [ ] **Step 1: Write the failing tests**

`tests/test_routes_board.py`:

```python
from types import SimpleNamespace

import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
from services import llm_service, research_execution_service, research_task_service, supervisor_service

GOOD_PROMPT = "Research the fee structures of every online Psychology postgraduate program."


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: "ROLE: analyst. A complete drafted research prompt for this card.")
    monkeypatch.setattr(research_execution_service, "sync_web_research_runs", lambda project_name: None)
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            c.post("/api/projects", json={"name": "P"})
            yield c
    set_database_path(None)


def _card(status="PROPOSED", prompt=GOOD_PROMPT, project="P"):
    pid = projects_repo.get_or_create_id(project)
    card_id = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB", prompt_text=prompt)
    if status != "PROPOSED":
        research_work_items_repo.update_fields(card_id, status=status)
    return card_id


def _status(card_id):
    return research_work_items_repo.get(card_id)["status"]


def test_board_needs_an_existing_project(client):
    assert client.get("/api/board?project=Nope").status_code == 400


def test_get_board(client):
    _card()
    body = client.get("/api/board?project=P").get_json()
    assert body["success"] is True
    assert [p["key"] for p in body["board"]["phases"]] == ["1", "2", "3", "4", "5", "6", "7"]
    assert body["board"]["cards"][0]["title"] == "Fees"


def test_add_edit_and_approve(client):
    resp = client.post("/api/board/cards", json={"project": "P", "phase_key": "4", "title": "Fees",
                                                 "research_method": "TARGETED_WEB", "draft_prompt": True})
    card_id = resp.get_json()["card_id"]
    assert resp.status_code == 200
    assert client.patch(f"/api/board/cards/{card_id}", json={"project": "P", "title": "Fee structures"}).status_code == 200
    resp = client.post(f"/api/board/cards/{card_id}/approve", json={"project": "P"})
    assert resp.status_code == 200
    card = next(c for c in resp.get_json()["board"]["cards"] if c["id"] == card_id)
    assert (card["title"], card["status"]) == ("Fee structures", "READY")


def test_approve_validation_is_a_400_with_a_readable_message(client):
    card_id = _card(prompt="Too short")
    resp = client.post(f"/api/board/cards/{card_id}/approve", json={"project": "P"})
    assert resp.status_code == 400
    assert "too short" in resp.get_json()["error"]


def test_wrong_state_is_a_409(client):
    card_id = _card(status="COMPLETE")
    assert client.post(f"/api/board/cards/{card_id}/restore", json={"project": "P"}).status_code == 409


def test_unknown_action_and_foreign_card_are_404(client):
    card_id = _card()
    assert client.post(f"/api/board/cards/{card_id}/launch", json={"project": "P"}).status_code == 404
    client.post("/api/projects", json={"name": "Other"})
    foreign = _card(project="Other")
    assert client.post(f"/api/board/cards/{foreign}/approve", json={"project": "P"}).status_code == 404


def test_redraft_unavailable_is_a_503(client, monkeypatch):
    card_id = _card()

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(llm_service, "prompt_completion", boom)
    assert client.post(f"/api/board/cards/{card_id}/redraft", json={"project": "P"}).status_code == 503


def test_run_starts_only_approved_cards(client, monkeypatch):
    monkeypatch.setattr(research_execution_service.openai_service, "start_deep_research",
                        lambda prompt: SimpleNamespace(id="resp_1", status="queued"))
    ready = _card(status="READY")
    draft = _card()
    resp = client.post("/api/board/run", json={"project": "P", "card_ids": [ready, draft]})
    body = resp.get_json()
    assert body["result"]["started"] == [ready]
    assert body["result"]["not_ready"] == [draft]
    assert _status(ready) == "RUNNING"


def test_run_needs_a_list_of_ids(client):
    assert client.post("/api/board/run", json={"project": "P", "card_ids": "all"}).status_code == 400


def test_report(client):
    card_id = _card(status="COMPLETE")
    assert client.get(f"/api/board/cards/{card_id}/report?project=P").status_code == 404
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create("run_1", pid, None, None, "p")
    research_runs_repo.update("run_1", research_work_item_id=card_id, status="completed", output_text="The report.")
    body = client.get(f"/api/board/cards/{card_id}/report?project=P").get_json()
    assert body["report"]["text"] == "The report."


class _Block:
    def __init__(self, name, input):
        self.type, self.name, self.input = "tool_use", name, input


def test_draft_creates_cards_and_explains_itself(client, monkeypatch):
    block = _Block("propose_tasks", {"reason": "Empty", "tasks": [
        {"phase_key": "1", "title": "Landscape", "research_method": "TARGETED_WEB", "rationale": "Nothing yet"}]})
    fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[block])))
    monkeypatch.setattr(supervisor_service.anthropic, "Anthropic", lambda api_key: fake)
    body = client.post("/api/board/draft", json={"project": "P"}).get_json()
    assert body["success"] is True
    assert body["note"].startswith("Drafted 1 new research")
    assert body["board"]["cards"][0]["status"] == "PROPOSED"


def test_options_report_draft_is_409_while_locked(client):
    assert client.post("/api/board/phase7/draft", json={"project": "P"}).status_code == 409


def test_objective_save(client):
    body = client.put("/api/board/objective", json={"project": "P", "objective": "Assess the MBA market."}).get_json()
    assert body["board"]["objective"] == "Assess the MBA market."


def test_refresh(client):
    body = client.post("/api/board/refresh", json={"project": "P", "defer_linking": True}).get_json()
    assert body["success"] is True
    assert body["board"]["linked_reports"] == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_routes_board.py -v`
Expected: FAIL with 404s (no routes).

- [ ] **Step 3: Implement `routes/board.py`**

```python
# routes/board.py
from flask import Blueprint, jsonify, request, session

from services import board_service, supervisor_service
from services.project_service import normalize_project_name, project_exists

board_bp = Blueprint("board", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


def _request_project():
    data = request.get_json(silent=True) or {}
    raw = data.get("project") or request.args.get("project") or session.get("current_project")
    return _existing_project_name(raw), data


def _no_project():
    return jsonify({"success": False, "error": "No project selected."}), 400


def _error(exc):
    if isinstance(exc, board_service.CardNotFound):
        code = 404
    elif isinstance(exc, board_service.BoardStateError):
        code = 409
    elif isinstance(exc, board_service.DraftingUnavailable):
        code = 503
    elif isinstance(exc, ValueError):
        code = 400
    else:
        code = 500
    message = exc.args[0] if exc.args else str(exc)
    return jsonify({"success": False, "error": str(message)}), code


def _ok(project, **extra):
    return jsonify({"success": True, "board": board_service.get_board_state(project), **extra})


def _draft_note(result):
    execution = result["execution"]
    if result["action"] == "no_action":
        reason = (result["input"] or {}).get("reason")
        return f"Nothing new to propose: {reason}" if reason else "Nothing new to propose right now."
    if not execution["success"]:
        return f"The Supervisor's draft was rejected: {execution['error']}"
    n = len(execution["result"]["created_task_ids"])
    return f"Drafted {n} new {'research' if n == 1 else 'researches'}. Nothing runs until you approve."


@board_bp.route("/api/board", methods=["GET"])
def get_board():
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        return _ok(project)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/refresh", methods=["POST"])
def refresh_board():
    project, data = _request_project()
    if not project:
        return _no_project()
    try:
        state = board_service.refresh(project, defer_linking=bool(data.get("defer_linking")))
        return jsonify({"success": True, "board": state})
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/draft", methods=["POST"])
def draft_researches():
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        result = supervisor_service.draft_researches(project)
        return _ok(project, result=result, note=_draft_note(result))
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards", methods=["POST"])
def create_card():
    project, data = _request_project()
    if not project:
        return _no_project()
    try:
        card_id = board_service.create_card(project, data)
        return _ok(project, card_id=card_id)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards/<int:card_id>", methods=["PATCH"])
def edit_card(card_id):
    project, data = _request_project()
    if not project:
        return _no_project()
    editable = {k: v for k, v in data.items() if k in ("title", "prompt_text", "focus", "research_method", "rationale")}
    try:
        board_service.edit_card(project, card_id, editable)
        return _ok(project)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards/<int:card_id>/<action>", methods=["POST"])
def card_action(card_id, action):
    project, _ = _request_project()
    if not project:
        return _no_project()
    handler = board_service.ACTIONS.get(action)
    if handler is None:
        return jsonify({"success": False, "error": f"Unknown action: {action}"}), 404
    try:
        handler(project, card_id)
        return _ok(project)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/run", methods=["POST"])
def run_cards():
    project, data = _request_project()
    if not project:
        return _no_project()
    card_ids = data.get("card_ids")
    if not isinstance(card_ids, list):
        return jsonify({"success": False, "error": "card_ids must be a list of research ids"}), 400
    try:
        result = board_service.run(project, card_ids)
        return _ok(project, result=result)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards/<int:card_id>/report", methods=["GET"])
def card_report(card_id):
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        return jsonify({"success": True, "report": board_service.get_report(project, card_id)})
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/phase7/draft", methods=["POST"])
def draft_options_report():
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        card_id = board_service.draft_options_report(project)
        return _ok(project, card_id=card_id)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/objective", methods=["PUT"])
def save_objective():
    project, data = _request_project()
    if not project:
        return _no_project()
    try:
        board_service.save_objective(project, data.get("objective") or "")
        return _ok(project)
    except Exception as exc:
        return _error(exc)
```

In `app.py`, add `from routes.board import board_bp` beside the other route imports and `app.register_blueprint(board_bp)` after `supervisor_bp`.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_routes_board.py -v` (expected: PASS). Then run `python -m ruff check .` and `python -m pytest tests/ -q` (expected: clean, all pass).

- [ ] **Step 5: Commit**

```bash
git add routes/board.py app.py tests/test_routes_board.py
git commit -m "Add Research Board API routes"
```

---

### Task 9: Research tab: structure, rendering and styles

**Files:**
- Create: `static/board.js`, `tests/js/test_board_view.js`, `tests/test_board_js.py`
- Modify: `templates/index.html` (Research tab button, tab container, script tag), `static/app.js` (`showTab` and `loadProject` hooks), `static/style.css` (a delimited `rb-` block)

**Interfaces:**
- Consumes: `GET /api/board` state shape (Task 7/8); globals from `app.js`: `currentProject`, `renderMarkdown`.
- Produces:
  - In `static/board.js`, pure functions exported under Node (`module.exports`): `STATUS`, `esc`, `newView`, `filterMatches`, `formatElapsed`, `pluralise`, `cardHtml(card, state, view)`, `phaseHtml(phase, state, view)`, `runBoxHtml(state, view)`, `chipsHtml(cards, view)`, `renderBoard(state, view)`, `editPayload(draft)` and `addPayload(project, adding)`.
  - Browser globals: `boardOnTabShown()` and `boardOnProjectLoaded()`. Task 10 adds `boardOpenAddResearch(title)` and `boardMaybeReloadInsights()`.
  - Every interactive element carries `data-act="<action>"` and `data-id`/`data-phase`/`data-val`. Edit fields carry `data-field` + `data-id`, and add-form fields carry `data-add`. Task 10 wires the handlers.

The Research tab sits **next to** the Prompt Developer tab until Task 11 removes the old one.

- [ ] **Step 1: Write the failing Node test and its pytest wrapper**

`tests/js/test_board_view.js`:

```js
// Run: node tests/js/test_board_view.js  (also run by tests/test_board_js.py)
const assert = require('assert');
const B = require('../../static/board.js');

function card(over) {
    return Object.assign({
        id: 7, phase_key: '4', title: 'Fees', status: 'PROPOSED', research_method: 'TARGETED_WEB',
        method_label: 'Web research', framework_key: 'oes-product-features', framework_label: 'Phase 4: Product Features',
        prompt_text: 'Research the fees of every provider.', needs_prompt_edit: false, focus: ['Fees'],
        rationale: 'Price drives choice', priority: null, suggested_from: null, followups: [], depends_on: [],
        completeness_score: null, evidence_score: null, gaps: [], human_review_required: false,
        retry_count: 0, max_retries: 3, run: null, review_error: null,
    }, over || {});
}
function state(cards, over) {
    return Object.assign({
        project: 'P', objective: 'Assess the market.',
        phases: ['The Landscape', 'The Student', 'Review of Marketing', 'Product Features', 'Academic Content',
                 'Industry Engagement', 'Options for OES'].map((t, i) => ({ key: String(i + 1), title: t, frameworks: [] })),
        cards, phase7: { ready_count: 2, unlocked: false, summaries_stale: false }, activity: [],
    }, over || {});
}
const view = () => B.newView();
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('escapes titles', () => {
    const html = B.cardHtml(card({ title: '<script>x</script>' }), state([]), view());
    assert(!html.includes('<script>x'));
    assert(html.includes('&lt;script&gt;'));
});
test('draft card offers edit-and-approve and skip', () => {
    const html = B.cardHtml(card(), state([]), view());
    assert(html.includes('Needs your approval'));
    assert(html.includes('data-act="edit"') && html.includes('Edit and approve'));
    assert(html.includes('data-act="skip"'));
});
test('placeholder prompt is flagged', () => {
    assert(B.cardHtml(card({ needs_prompt_edit: true }), state([]), view()).includes('Edit the prompt before approving'));
});
test('editing shows the form with the exact prompt', () => {
    const v = view(); v.editing[7] = true;
    const html = B.cardHtml(card(), state([]), v);
    assert(html.includes('data-field="prompt_text"'));
    assert(html.includes('This text is exactly what gets sent'));
    assert(html.includes('Drafted from the Phase 4: Product Features framework'));
    assert(html.includes('data-act="approve"'));
});
test('approved card can go back to draft', () => {
    const html = B.cardHtml(card({ status: 'READY' }), state([]), view());
    assert(html.includes('Approved, waiting for Run') && html.includes('data-act="unapprove"'));
});
test('running card shows elapsed time, not a percentage', () => {
    const html = B.cardHtml(card({ status: 'RUNNING', run: { elapsed_seconds: 360 } }), state([]), view());
    assert(html.includes('6 min'));
    assert(!html.includes('%'));
});
test('finished card shows the review', () => {
    const html = B.cardHtml(card({ status: 'COMPLETE', completeness_score: 0.8, evidence_score: 0.7, gaps: ['No dates'],
                                  run: { has_report: true, report_filename: 'Research P4 - Fees (2026-10-05).md' } }), state([]), view());
    assert(html.includes('0.80') && html.includes('0.70') && html.includes('No dates'));
    assert(html.includes('data-act="report"'));
    assert(html.includes('Research P4 - Fees (2026-10-05).md'));
});
test('needs-your-review card offers three choices', () => {
    const html = B.cardHtml(card({ status: 'WAITING_FOR_HUMAN' }), state([]), view());
    ['accept', 'needs-followup', 'mark-failed'].forEach(a => assert(html.includes(`data-act="${a}"`)));
});
test('retry is disabled at the retry limit', () => {
    const html = B.cardHtml(card({ status: 'FAILED', retry_count: 3, max_retries: 3, run: { error: 'OpenAI is down' } }), state([]), view());
    assert(/data-act="retry"[^>]*disabled/.test(html));
    assert(html.includes('OpenAI is down'));
});
test('follow-up says where it came from', () => {
    assert(B.cardHtml(card({ suggested_from: { id: 1, title: 'Overview' } }), state([]), view()).includes('Suggested after reviewing'));
});
test('chips hide review and failed when empty', () => {
    const html = B.chipsHtml([card()], view());
    assert(!html.includes('Needs your review') && !html.includes('>Failed'));
    assert(B.chipsHtml([card({ status: 'FAILED' })], view()).includes('Failed'));
});
test('filters', () => {
    assert(B.filterMatches(card({ status: 'REVIEWING' }), 'running'));
    assert(B.filterMatches(card({ status: 'FOLLOW_UP_REQUIRED' }), 'finished'));
    assert(!B.filterMatches(card({ status: 'SKIPPED' }), 'all'));
});
test('phase 7 lock shows progress', () => {
    const s = state([]);
    assert(B.phaseHtml(s.phases[6], s, view()).includes('2 of 6'));
});
test('phase 7 unlocked offers the options report and warns when stale', () => {
    const s = state([], { phase7: { ready_count: 6, unlocked: true, summaries_stale: true } });
    const html = B.phaseHtml(s.phases[6], s, view());
    assert(html.includes('data-act="draft-options"'));
    assert(html.includes('Refresh Insights first'));
});
test('run box is disabled with nothing approved', () => {
    assert(/data-act="run-open"[^>]*disabled/.test(B.runBoxHtml(state([card()]), view())));
    assert(B.runBoxHtml(state([card({ status: 'READY' })]), view()).includes('Run 1 approved research'));
});
test('confirm warns about OpenAI billing for web research', () => {
    const v = view(); v.confirming = true;
    const html = B.runBoxHtml(state([card({ status: 'READY' })]), v);
    assert(html.includes('bills your OpenAI account') && html.includes('data-act="run-start"'));
});
test('payload helpers', () => {
    assert.deepStrictEqual(B.editPayload({ title: 'T', focus: 'a, , b ' }), { title: 'T', focus: ['a', 'b'] });
    const add = B.addPayload('P', { phase_key: '2', title: ' Personas ', focus: 'x' });
    assert.deepStrictEqual(add, { project: 'P', phase_key: '2', title: 'Personas', research_method: 'TARGETED_WEB',
        focus: ['x'], rationale: '', draft_prompt: true, framework_key: null, prompt_text: '' });
});
test('whole board renders', () => {
    const html = B.renderBoard(state([card(), card({ id: 8, status: 'SKIPPED', title: 'Old' })]), view());
    assert(html.includes('Research plan') && html.includes('Draft next researches') && html.includes('Skipped (1)'));
});

let failed = 0;
for (const [name, fn] of tests) {
    try { fn(); console.log('ok -', name); } catch (e) { failed++; console.log('FAIL -', name, '\n ', e.message); }
}
if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log(`all ${tests.length} passed`);
```

`tests/test_board_js.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_board_view_js():
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "test_board_view.js")],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `node tests/js/test_board_view.js`
Expected: FAIL with `Cannot find module '../../static/board.js'`.

- [ ] **Step 3: Write `static/board.js` (render part)**

```js
// static/board.js
// Research tab ("Research Board"). Renders GET /api/board and sends the user's actions to /api/board/*.
// The render functions are pure (state -> HTML string) and are exported for the Node tests in tests/js/.
(function () {
    'use strict';

    const STATUS = {
        PROPOSED: { label: 'Needs your approval', cls: 'rb-s-needs' },
        READY: { label: 'Approved, waiting for Run', cls: 'rb-s-appr' },
        RUNNING: { label: 'Running', cls: 'rb-s-run' },
        REVIEWING: { label: 'Being reviewed', cls: 'rb-s-run' },
        COMPLETE: { label: 'Finished', cls: 'rb-s-done' },
        FOLLOW_UP_REQUIRED: { label: 'Finished, follow-up suggested', cls: 'rb-s-fu' },
        WAITING_FOR_HUMAN: { label: 'Needs your review', cls: 'rb-s-needs' },
        FAILED: { label: 'Failed', cls: 'rb-s-fail' },
        SKIPPED: { label: 'Skipped', cls: 'rb-s-skip' },
    };
    const METHOD_LABEL = { TARGETED_WEB: 'Web research', FILE_ANALYSIS: 'My files', SYNTHESIS: 'Options report' };
    const USER_METHODS = ['TARGETED_WEB', 'FILE_ANALYSIS'];
    const ACTIVE = ['RUNNING', 'REVIEWING'];
    const FILTERS = [
        { key: 'all', label: 'All', test: c => c.status !== 'SKIPPED' },
        { key: 'draft', label: 'Needs approval', test: c => c.status === 'PROPOSED' },
        { key: 'approved', label: 'Approved', test: c => c.status === 'READY' },
        { key: 'running', label: 'Running', test: c => ACTIVE.includes(c.status) },
        { key: 'finished', label: 'Finished', test: c => c.status === 'COMPLETE' || c.status === 'FOLLOW_UP_REQUIRED' },
        { key: 'review', label: 'Needs your review', test: c => c.status === 'WAITING_FOR_HUMAN', onlyWhenAny: true },
        { key: 'failed', label: 'Failed', test: c => c.status === 'FAILED', onlyWhenAny: true },
    ];

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g,
            ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }
    function newView() {
        return { filter: 'all', closedPhases: {}, editing: {}, drafts: {}, cardErrors: {}, adding: null,
                 confirming: false, busy: false, supervisorNote: '', message: '', objEdit: false };
    }
    function pluralise(n, one, many) { return `${n} ${n === 1 ? one : many}`; }
    function filterMatches(card, key) { return (FILTERS.find(f => f.key === key) || FILTERS[0]).test(card); }
    function formatElapsed(seconds) {
        if (seconds == null) return '';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 1) return 'under a minute';
        if (minutes < 60) return `${minutes} min`;
        return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
    }
    function formatClock(iso) { const m = /T(\d{2}):(\d{2})/.exec(iso || ''); return m ? `${m[1]}:${m[2]}` : ''; }
    function splitFocus(text) { return String(text || '').split(',').map(s => s.trim()).filter(Boolean); }

    function editPayload(draft) {
        const out = {};
        ['title', 'prompt_text', 'research_method', 'rationale'].forEach(k => { if (draft && draft[k] !== undefined) out[k] = draft[k]; });
        if (draft && draft.focus !== undefined) out.focus = splitFocus(draft.focus);
        return out;
    }
    function addPayload(project, a) {
        return {
            project, phase_key: a.phase_key, title: (a.title || '').trim(),
            research_method: a.research_method || 'TARGETED_WEB', focus: splitFocus(a.focus),
            rationale: (a.rationale || '').trim(), draft_prompt: a.draft_prompt !== false,
            framework_key: a.framework_key || null, prompt_text: a.prompt_text || '',
        };
    }

    function stepsHtml(state) {
        const live = state.cards.filter(c => c.status !== 'SKIPPED');
        const any = list => live.some(c => list.includes(c.status));
        const steps = [
            ['Objective', !!(state.objective || '').trim()],
            ['Plan', any(['READY', 'RUNNING', 'REVIEWING', 'COMPLETE', 'FOLLOW_UP_REQUIRED', 'WAITING_FOR_HUMAN', 'FAILED'])],
            ['Run', any(['RUNNING', 'REVIEWING', 'COMPLETE', 'FOLLOW_UP_REQUIRED', 'WAITING_FOR_HUMAN', 'FAILED'])],
            ['Findings', any(['COMPLETE', 'FOLLOW_UP_REQUIRED'])],
        ];
        let now = steps.findIndex(s => !s[1]);
        if (now < 0) now = steps.length - 1;
        return '<ol class="rb-steps" aria-label="Progress">' + steps.map((s, i) =>
            `<li class="${s[1] ? 'done' : (i === now ? 'now' : '')}"><span class="n">${i + 1}</span>${s[0]}</li>`).join('') + '</ol>';
    }

    function objectiveHtml(state, view) {
        const body = view.objEdit
            ? `<label class="rb-lbl" for="rb-obj-input">What should this research achieve?</label><textarea id="rb-obj-input" class="rb-obj-input">${esc(state.objective)}</textarea><div class="rb-row"><button type="button" class="rb-btn primary" data-act="obj-save">Save objective</button><button type="button" class="rb-btn" data-act="obj-cancel">Cancel</button></div>`
            : `<p class="rb-obj-text">${state.objective ? esc(state.objective) : '<span class="rb-muted">No objective yet. The Supervisor needs one before it can draft researches.</span>'}</p><div class="rb-row"><button type="button" class="rb-btn" data-act="obj-edit">Edit objective</button></div>`;
        const draftBtn = view.busy
            ? '<button type="button" class="rb-btn" disabled>Drafting…</button>'
            : `<button type="button" class="rb-btn" data-act="draft"${state.objective ? '' : ' disabled'}>Draft next researches</button>`;
        const note = view.supervisorNote ? `<br><span class="rb-note">${esc(view.supervisorNote)}</span>` : '';
        return `<section class="rb-panel rb-objective" aria-label="Objective"><h3>Objective</h3>${body}<div class="rb-sup"><p class="rb-small rb-muted"><b>Supervisor.</b> It reads the objective, drafts researches and reviews finished ones. It never starts research by itself.${note}</p>${draftBtn}</div></section>`;
    }

    function chipsHtml(cards, view) {
        return '<div class="rb-chips" role="group" aria-label="Filter researches">' + FILTERS.map(f => {
            const n = cards.filter(f.test).length;
            if (f.onlyWhenAny && n === 0) return '';
            return `<button type="button" class="rb-chip" data-act="filter" data-val="${f.key}" aria-pressed="${view.filter === f.key}">${f.label} <span class="c">${n}</span></button>`;
        }).join('') + '</div>';
    }

    function metaHtml(card) {
        const method = card.method_label || METHOD_LABEL[card.research_method] || 'No method yet';
        const focus = (card.focus || []).map(f => `<span class="rb-tag">${esc(f)}</span>`).join('');
        return `<div class="rb-meta"><span class="rb-tag method">${esc(method)}</span>${focus}</div>`;
    }
    function previewHtml(card) {
        if (!card.prompt_text) return '<p class="rb-preview rb-muted">No prompt yet. Edit this research to write one.</p>';
        return `<p class="rb-preview">${esc(card.prompt_text)}</p>`;
    }
    function linksHtml(card) {
        let html = '';
        if (card.suggested_from) html += `<p class="rb-from">Suggested after reviewing “${esc(card.suggested_from.title)}”.</p>`;
        if ((card.followups || []).length) {
            html += '<p class="rb-from">Follow-up: ' + card.followups.map(f =>
                `“${esc(f.title)}” (${esc((STATUS[f.status] || {}).label || f.status)})`).join(', ') + '</p>';
        }
        const waiting = (card.depends_on || []).filter(d => d.status !== 'COMPLETE' && d.status !== 'SKIPPED');
        if (waiting.length) html += '<p class="rb-from">Waits for: ' + waiting.map(d => `“${esc(d.title)}”`).join(', ') + '</p>';
        return html;
    }

    function formHtml(card, view) {
        const id = card.id;
        const d = view.drafts[id] || {};
        const val = k => (d[k] !== undefined ? d[k] : card[k]);
        const focus = d.focus !== undefined ? d.focus : (card.focus || []).join(', ');
        const method = val('research_method');
        const methodField = card.research_method === 'SYNTHESIS'
            ? '<p class="rb-hint">Runs as the options report for Phase 7.</p>'
            : '<div class="rb-seg" role="radiogroup" aria-label="How it runs">' + USER_METHODS.map(m =>
                `<label><input type="radio" name="rb-method-${id}" value="${m}" data-field="research_method" data-id="${id}"${method === m ? ' checked' : ''}> ${METHOD_LABEL[m]}</label>`).join('') + '</div>';
        const from = card.framework_label ? `Drafted from the ${esc(card.framework_label)} framework. ` : '';
        return `<div class="rb-form">`
            + `<div><label class="rb-lbl" for="rb-title-${id}">Title</label><input type="text" id="rb-title-${id}" data-field="title" data-id="${id}" value="${esc(val('title'))}"></div>`
            + `<div><span class="rb-lbl">How it runs</span>${methodField}</div>`
            + `<div><label class="rb-lbl" for="rb-prompt-${id}">Research prompt</label><textarea id="rb-prompt-${id}" data-field="prompt_text" data-id="${id}" spellcheck="false">${esc(val('prompt_text') || '')}</textarea><p class="rb-hint">${from}Change anything. This text is exactly what gets sent.</p></div>`
            + `<div><label class="rb-lbl" for="rb-focus-${id}">Focus (separate with commas)</label><input type="text" id="rb-focus-${id}" data-field="focus" data-id="${id}" value="${esc(focus)}"></div>`
            + `<div><label class="rb-lbl" for="rb-why-${id}">Why this research</label><input type="text" id="rb-why-${id}" data-field="rationale" data-id="${id}" value="${esc(val('rationale') || '')}"></div>`
            + `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="approve" data-id="${id}">Approve</button>`
            + `<button type="button" class="rb-btn" data-act="save" data-id="${id}">Save as draft</button>`
            + `<button type="button" class="rb-btn" data-act="redraft" data-id="${id}"${card.framework_key ? '' : ' disabled'}>Re-draft from framework</button>`
            + `<button type="button" class="rb-btn quiet" data-act="close" data-id="${id}">Cancel</button></div>`
            + `</div>`;
    }

    function meter(label, score) {
        if (score == null) return '';
        const pct = Math.round(Math.max(0, Math.min(1, Number(score))) * 100);
        return `<div class="rb-meter"><div class="top"><span>${label}</span><span class="rb-mono">${Number(score).toFixed(2)}</span></div><div class="track"><span style="width:${pct}%"></span></div></div>`;
    }
    function reportButton(card) {
        return card.run && card.run.has_report ? `<button type="button" class="rb-btn" data-act="report" data-id="${card.id}">Read report</button>` : '';
    }
    function reviewHtml(card) {
        const meters = meter('Completeness', card.completeness_score) + meter('Evidence', card.evidence_score);
        const gaps = (card.gaps || []).length ? `<div><h5>Gaps the Supervisor found</h5><ul>${card.gaps.map(g => `<li>${esc(g)}</li>`).join('')}</ul></div>` : '';
        const file = card.run && card.run.report_filename ? `<p class="rb-hint">Saved to your sources as “${esc(card.run.report_filename)}” and linked to this phase.</p>` : '';
        return `<div class="rb-review"><h5>Supervisor review</h5>${meters ? `<div class="rb-meters">${meters}</div>` : ''}${gaps}${file}<div class="rb-row">${reportButton(card)}</div></div>`;
    }
    function runningText(card) {
        if (card.research_method === 'TARGETED_WEB') return 'Searching and reading sources';
        if (card.research_method === 'SYNTHESIS') return 'Writing the options report';
        return 'Reading your files';
    }

    function cardBody(card, state, view) {
        const id = card.id;
        const btn = (act, label, cls, disabled) => `<button type="button" class="rb-btn${cls ? ' ' + cls : ''}" data-act="${act}" data-id="${id}"${disabled ? ' disabled' : ''}>${label}</button>`;
        switch (card.status) {
            case 'PROPOSED':
                return previewHtml(card) + (card.needs_prompt_edit ? '<p class="rb-warn">Edit the prompt before approving.</p>' : '')
                    + linksHtml(card) + `<div class="rb-row">${btn('edit', 'Edit and approve', 'primary')}${btn('skip', 'Skip', 'quiet')}</div>`;
            case 'READY':
                return previewHtml(card) + linksHtml(card)
                    + `<div class="rb-row">${btn('edit', 'Edit')}${btn('unapprove', 'Back to draft', 'quiet')}</div><p class="rb-hint">Editing sends it back for approval.</p>`;
            case 'RUNNING':
                return `<p class="rb-progress"><span class="rb-pulse" aria-hidden="true"></span>${runningText(card)} · <span class="rb-mono">${esc(formatElapsed(card.run ? card.run.elapsed_seconds : null))}</span></p>`
                    + (card.review_error ? '<p class="rb-warn">The review didn\'t complete. It will be retried.</p>' : '');
            case 'REVIEWING':
                return '<p class="rb-progress"><span class="rb-pulse" aria-hidden="true"></span>The Supervisor is reviewing this research.</p>';
            case 'COMPLETE':
            case 'FOLLOW_UP_REQUIRED':
                if (card.research_method === 'SYNTHESIS') {
                    return `<p class="rb-hint">Saved to Insights, Phase 7.</p><div class="rb-row">${reportButton(card)}</div>`;
                }
                return linksHtml(card) + reviewHtml(card);
            case 'WAITING_FOR_HUMAN':
                return reviewHtml(card) + '<p class="rb-hint">The Supervisor wasn\'t sure about this one. What do you think?</p>'
                    + `<div class="rb-row">${btn('accept', 'Accept as finished', 'primary')}${btn('needs-followup', 'Needs follow-up')}${btn('mark-failed', 'Mark failed', 'quiet')}</div>`;
            case 'FAILED': {
                const atLimit = card.retry_count >= card.max_retries;
                const error = (card.run && card.run.error) || 'This research did not produce a usable result.';
                return `<p class="rb-err">${esc(error)}</p>${(card.gaps || []).length ? reviewHtml(card) : ''}`
                    + `<div class="rb-row">${btn('retry', 'Retry', 'primary', atLimit)}${btn('back-to-draft', 'Back to draft')}${btn('skip', 'Skip', 'quiet')}</div>`
                    + (atLimit ? `<p class="rb-hint">It has failed ${card.retry_count} times. Move it back to draft to change it, or skip it.</p>` : '');
            }
            default:
                return '';
        }
    }

    function cardHtml(card, state, view) {
        const st = STATUS[card.status] || { label: card.status, cls: '' };
        const editing = !!view.editing[card.id] && (card.status === 'PROPOSED' || card.status === 'READY');
        const top = `<div class="rb-card-top"><h4>${esc(card.title)}</h4><span class="rb-pill ${st.cls}"><i></i>${esc(st.label)}</span></div>${metaHtml(card)}`;
        const error = view.cardErrors && view.cardErrors[card.id] ? `<p class="rb-err" role="alert">${esc(view.cardErrors[card.id])}</p>` : '';
        const stale = card.phase_key === '7' && state.phase7 && state.phase7.summaries_stale && (card.status === 'PROPOSED' || card.status === 'READY')
            ? '<p class="rb-warn">Your Insights summaries are older than your newest research. Refresh Insights first to include it.</p>' : '';
        const body = editing ? formHtml(card, view) : cardBody(card, state, view);
        return `<article class="rb-card${editing ? ' open' : ''}" id="rb-card-${card.id}">${top}${stale}${error}${body}</article>`;
    }

    function addFormHtml(state, view) {
        const a = view.adding;
        const phases = state.phases.filter(p => p.key !== '7');
        const phase = phases.find(p => p.key === a.phase_key) || phases[0];
        const frameworks = phase.frameworks || [];
        const method = a.research_method || 'TARGETED_WEB';
        const drafting = a.draft_prompt !== false;
        const frameworkPick = drafting && frameworks.length > 1
            ? `<select data-add="framework_key" aria-label="Framework">${frameworks.map(f => `<option value="${f.key}"${a.framework_key === f.key ? ' selected' : ''}>${esc(f.label)}</option>`).join('')}</select>` : '';
        const promptField = drafting ? ''
            : `<div><label class="rb-lbl" for="rb-add-prompt">Research prompt</label><textarea id="rb-add-prompt" data-add="prompt_text" spellcheck="false">${esc(a.prompt_text || '')}</textarea></div>`;
        return `<div class="rb-card open rb-add"><h4>Add research</h4><div class="rb-form">`
            + `<div><label class="rb-lbl" for="rb-add-phase">Phase</label><select id="rb-add-phase" data-add="phase_key">${phases.map(p => `<option value="${p.key}"${p.key === phase.key ? ' selected' : ''}>Phase ${p.key}: ${esc(p.title)}</option>`).join('')}</select></div>`
            + `<div><label class="rb-lbl" for="rb-add-title">Title</label><input type="text" id="rb-add-title" data-add="title" value="${esc(a.title || '')}"></div>`
            + `<div><span class="rb-lbl">How it runs</span><div class="rb-seg">${USER_METHODS.map(m => `<label><input type="radio" name="rb-add-method" value="${m}" data-add="research_method"${method === m ? ' checked' : ''}> ${METHOD_LABEL[m]}</label>`).join('')}</div></div>`
            + `<div><label class="rb-lbl" for="rb-add-focus">Focus (separate with commas)</label><input type="text" id="rb-add-focus" data-add="focus" value="${esc(a.focus || '')}"></div>`
            + `<div><label class="rb-lbl" for="rb-add-why">Why this research</label><input type="text" id="rb-add-why" data-add="rationale" value="${esc(a.rationale || '')}"></div>`
            + `<div class="rb-row"><label class="rb-check"><input type="checkbox" data-add="draft_prompt"${drafting ? ' checked' : ''}> Draft the prompt for me from the framework</label>${frameworkPick}</div>`
            + promptField
            + `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="add-save"${view.busy ? ' disabled' : ''}>${view.busy ? 'Adding…' : 'Add research'}</button><button type="button" class="rb-btn quiet" data-act="add-cancel">Cancel</button></div>`
            + `</div></div>`;
    }

    function phaseSummary(cards) {
        if (!cards.length) return 'None drafted yet';
        const need = cards.filter(c => c.status === 'PROPOSED').length;
        return pluralise(cards.length, 'research', 'researches') + (need ? ` · ${need} need${need === 1 ? 's' : ''} approval` : '');
    }
    function phase7Html(state, view, cards) {
        const p7 = state.phase7 || {};
        if (!p7.unlocked) {
            return `<div class="rb-locked"><b>Opens when Phases 1 to 6 each have finished research.</b><br>${p7.ready_count || 0} of 6 are ready. Then you can draft the options report.</div>`;
        }
        let html = '';
        if (p7.summaries_stale) html += '<p class="rb-warn">Your Insights summaries are older than your newest research. Refresh Insights first to include it.</p>';
        const open = cards.some(c => ['PROPOSED', 'READY', 'RUNNING', 'REVIEWING'].includes(c.status));
        if (!open) html += `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="draft-options"${view.busy ? ' disabled' : ''}>Draft options report</button></div>`;
        return html;
    }
    function phaseHtml(phase, state, view) {
        const isOpen = !view.closedPhases[phase.key];
        const all = state.cards.filter(c => c.phase_key === phase.key && c.status !== 'SKIPPED');
        const shown = all.filter(c => filterMatches(c, view.filter));
        let body = '';
        if (isOpen) {
            if (phase.key === '7') body += phase7Html(state, view, all);
            if (view.adding && view.adding.phase_key === phase.key) body += addFormHtml(state, view);
            if (shown.length) body += shown.map(c => cardHtml(c, state, view)).join('');
            else if (all.length) body += '<div class="rb-empty">Nothing here for this filter.</div>';
            else if (phase.key !== '7') body += '<div class="rb-empty">No research drafted for this phase yet. Ask the Supervisor, or add one yourself.</div>';
            if (phase.key !== '7') body += `<div class="rb-row"><button type="button" class="rb-btn quiet" data-act="add" data-phase="${phase.key}">+ Add research</button></div>`;
        }
        const summary = phase.key === '7' ? '' : phaseSummary(all);
        return `<div class="rb-phase"><button type="button" class="rb-phase-head" data-act="phase" data-phase="${phase.key}" aria-expanded="${isOpen}"><span class="rb-chev" aria-hidden="true">▾</span><span class="rb-phase-num">PHASE ${phase.key}</span><span class="rb-phase-name">${esc(phase.title)}</span><span class="rb-phase-sum">${summary}</span></button>${isOpen ? `<div class="rb-phase-body">${body}</div>` : ''}</div>`;
    }

    function skippedHtml(cards) {
        const skipped = cards.filter(c => c.status === 'SKIPPED');
        if (!skipped.length) return '';
        return `<div class="rb-panel rb-skipped"><h3>Skipped (${skipped.length})</h3><ul>${skipped.map(c =>
            `<li><span>${esc(c.title)}</span><button type="button" class="rb-btn quiet" data-act="restore" data-id="${c.id}">Restore</button></li>`).join('')}</ul></div>`;
    }

    function runBoxHtml(state, view) {
        const approved = state.cards.filter(c => c.status === 'READY');
        const running = state.cards.filter(c => ACTIVE.includes(c.status)).length;
        const n = approved.length;
        const list = n ? `<ul class="rb-runlist">${approved.map(c => `<li><span>${esc(c.title)}</span><span class="rb-muted rb-small">${esc(c.method_label || METHOD_LABEL[c.research_method] || '')}</span></li>`).join('')}</ul>`
            : '<p class="rb-muted rb-small">Approve a research to add it here.</p>';
        const web = approved.filter(c => c.research_method === 'TARGETED_WEB').length;
        let action;
        if (view.confirming && n) {
            action = `<div class="rb-confirm" role="group" aria-label="Confirm run"><p><b>Start ${pluralise(n, 'research', 'researches')}?</b></p>`
                + `<p class="rb-small rb-muted">They run in the background, so you can leave this page.${web ? ` Web research bills your OpenAI account (${pluralise(web, 'research', 'researches')}).` : ''}</p>`
                + `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="run-start">Start ${n}</button><button type="button" class="rb-btn" data-act="run-cancel">Not yet</button></div></div>`;
        } else {
            action = `<button type="button" class="rb-btn primary wide" data-act="run-open"${n ? '' : ' disabled'}>${n ? `Run ${n} approved ${n === 1 ? 'research' : 'researches'}` : 'Nothing approved to run'}</button>`;
        }
        return `<section class="rb-panel rb-runbox" aria-label="Run"><h3>Run</h3><p class="rb-small rb-muted">Only approved researches run, and only when you press Run.${running ? ` ${running} running now.` : ''}</p>${list}${action}</section>`;
    }

    function activityHtml(state) {
        const items = state.activity || [];
        const list = items.length
            ? `<ol>${items.map(a => `<li><time>${esc(formatClock(a.at))}</time><span>${esc(a.text)}</span></li>`).join('')}</ol>`
            : '<p class="rb-muted rb-small">Nothing yet.</p>';
        return `<section class="rb-panel rb-activity" aria-label="Activity"><h3>Activity</h3>${list}</section>`;
    }

    function renderBoard(state, view) {
        const message = view.message ? `<div class="rb-message" role="status"><span>${esc(view.message)}</span><button type="button" class="rb-btn quiet" data-act="dismiss">Dismiss</button></div>` : '';
        const plan = chipsHtml(state.cards, view) + state.phases.map(p => phaseHtml(p, state, view)).join('') + skippedHtml(state.cards);
        return `<header class="rb-head"><div><h2>Research plan</h2><p class="rb-proj">Project <b>${esc(state.project)}</b></p></div>${stepsHtml(state)}</header>${message}`
            + `<div class="rb-grid">${objectiveHtml(state, view)}<section class="rb-plan" aria-label="Plan">${plan}</section>${runBoxHtml(state, view)}${activityHtml(state)}</div>`;
    }

    const pure = { STATUS, esc, newView, filterMatches, formatElapsed, pluralise, cardHtml, phaseHtml,
                   runBoxHtml, chipsHtml, renderBoard, editPayload, addPayload };
    if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }

    // ---------------- browser glue ----------------
    const board = { project: null, state: null, view: newView(), timer: null, loadSeq: 0, insightsStale: false };

    function activeProject() { return typeof currentProject !== 'undefined' ? currentProject : null; }
    function root() { return document.getElementById('research-board'); }
    function tabVisible() { const tab = document.getElementById('research-tab'); return !!(tab && tab.classList.contains('active')); }

    async function call(method, url, body) {
        const options = { method };
        if (body !== undefined) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(body); }
        const res = await fetch(url, options);
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!res.ok || !data || data.success === false) {
            const err = new Error((data && data.error) || `Something went wrong (${res.status}).`);
            err.status = res.status;
            throw err;
        }
        return data;
    }

    function render() {
        const el = root();
        if (!el) return;
        if (!activeProject()) { el.innerHTML = '<div class="rb-empty">Select a project to see its research plan.</div>'; return; }
        if (!board.state) { el.innerHTML = '<div class="rb-empty">Loading the research plan…</div>'; return; }
        el.innerHTML = renderBoard(board.state, board.view);
    }

    function syncProject() {
        const project = activeProject();
        if (project !== board.project) {
            board.project = project;
            board.state = null;
            board.view = newView();
            clearTimeout(board.timer);
            board.timer = null;
        }
        return project;
    }

    async function loadBoard() {
        const project = syncProject();
        render();
        if (!project) return;
        const seq = ++board.loadSeq;
        try {
            const data = await call('GET', `/api/board?project=${encodeURIComponent(project)}`);
            if (seq !== board.loadSeq || project !== activeProject()) return;
            board.state = data.board;
        } catch (e) {
            if (project !== activeProject()) return;
            board.view.message = e.message;
        }
        render();
    }

    // ---- interactions (Task 10) ----

    window.boardOnTabShown = function () { loadBoard(); };
    window.boardOnProjectLoaded = function () {
        if (activeProject() === board.project) return;
        if (tabVisible()) loadBoard(); else syncProject();
    };
})();
```

- [ ] **Step 4: Add the tab, container and script**

In `templates/index.html`:
- After the Prompt Developer tab button (line ~67), add: `<button class="tab" onclick="showTab('research')">Research</button>`
- Before `<div id="prompt-dev-tab" class="tab-content">` (line ~148), add:

```html
            <div id="research-tab" class="tab-content">
                <div class="rb-scroll">
                    <div id="research-board" class="rb-wrap">
                        <div class="rb-empty">Select a project to see its research plan.</div>
                    </div>
                </div>
            </div>
```

- After `<script src="/static/app.js?v=13.1"></script>`, add `<script src="/static/board.js?v=1"></script>`, and bump app.js to `?v=13.2`.

In `static/app.js` `showTab(name)` (line ~4967), after the `renderArtifactsTab()` line, add:

```js
    if (name === 'research' && typeof boardOnTabShown === 'function') boardOnTabShown();
```

At the end of `loadProject()` (after `syncInsightsControlButtons();`, line ~4694), add:

```js
    if (typeof boardOnProjectLoaded === 'function') boardOnProjectLoaded();
```

- [ ] **Step 5: Add the styles**

Append to `static/style.css`:

```css
/* ===== Research Board (static/board.js) — all classes prefixed rb- ===== */
.rb-scroll { flex: 1; overflow-y: auto; background: #F3F5F9; }
.rb-wrap { max-width: 1240px; margin: 0 auto; padding: 0 16px 56px; color: #0B1B33; font-size: 15px; line-height: 1.45; }
.rb-wrap h2, .rb-wrap h3, .rb-wrap h4, .rb-wrap h5, .rb-wrap p, .rb-wrap ol, .rb-wrap ul { margin: 0; }
.rb-wrap ol, .rb-wrap ul { list-style: none; padding: 0; }
.rb-wrap button:focus-visible, .rb-wrap input:focus-visible, .rb-wrap select:focus-visible, .rb-wrap textarea:focus-visible { outline: 3px solid #0B5FD6; outline-offset: 2px; }
.rb-muted { color: #53617A; }
.rb-small { font-size: 13px; }
.rb-mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; }
.rb-empty { padding: 14px; border: 1px dashed #AEBBCC; border-radius: 10px; color: #53617A; font-size: 14px; background: #fff; }
.rb-head { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px 24px; padding: 24px 0 8px; }
.rb-head h2 { font-size: 26px; font-weight: 700; color: var(--oes-ink); }
.rb-proj { color: #53617A; font-size: 14px; margin-top: 2px; }
.rb-steps { display: flex; flex-wrap: wrap; gap: 6px 8px; }
.rb-steps li { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border: 1px solid #D3DBE6; border-radius: 999px; background: #fff; font-size: 13px; font-weight: 500; color: #53617A; }
.rb-steps .n { display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; background: #E9EEF4; font-size: 11px; }
.rb-steps li.done { color: #0B1B33; }
.rb-steps li.done .n { background: #D8F1DF; color: #145F36; }
.rb-steps li.now { border-color: var(--oes-valencia); color: #0B1B33; }
.rb-steps li.now .n { background: var(--oes-valencia); color: var(--oes-ink); font-weight: 600; }
.rb-message { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px; padding: 10px 14px; border-radius: 10px; background: #FFEFCB; color: #6F4300; font-size: 14px; }
.rb-grid { display: grid; gap: 20px; margin-top: 16px; grid-template-columns: minmax(0, 1fr); grid-template-areas: "run" "obj" "plan" "act"; }
@media (min-width: 1000px) {
    .rb-grid { grid-template-columns: minmax(0, 1fr) 340px; grid-template-areas: "obj run" "plan run" "plan act"; align-items: start; }
    .rb-runbox { position: sticky; top: 12px; }
}
.rb-objective { grid-area: obj; } .rb-plan { grid-area: plan; } .rb-runbox { grid-area: run; } .rb-activity { grid-area: act; }
.rb-panel { background: #fff; border: 1px solid #D3DBE6; border-radius: 12px; padding: 16px; }
.rb-panel h3 { font-size: 13px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #53617A; }
.rb-obj-text { margin-top: 8px; font-size: 16px; max-width: 70ch; }
.rb-obj-input { width: 100%; min-height: 84px; margin-top: 8px; }
.rb-sup { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-top: 16px; padding-top: 16px; border-top: 1px solid #D3DBE6; }
.rb-sup p { max-width: 60ch; }
.rb-sup b, .rb-note { color: #0B1B33; }
.rb-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; align-items: center; }
.rb-btn { border: 1px solid #AEBBCC; background: #fff; color: #0B1B33; border-radius: 8px; padding: 7px 14px; font-weight: 600; font-size: 14px; min-height: 36px; cursor: pointer; }
.rb-btn:hover { background: #E9EEF4; }
.rb-btn.primary { background: var(--oes-valencia); border-color: var(--oes-valencia); color: var(--oes-ink); }
.rb-btn.primary:hover { background: #FF9D2E; }
.rb-btn.quiet { border-color: transparent; background: transparent; color: #A04E00; }
.rb-btn.quiet:hover { background: #E9EEF4; }
.rb-btn[disabled] { opacity: .5; cursor: not-allowed; }
.rb-btn.wide { width: 100%; }
.rb-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }
.rb-chip { display: flex; align-items: center; gap: 8px; border: 1px solid #D3DBE6; background: #fff; border-radius: 999px; padding: 5px 12px; font-size: 13px; font-weight: 600; cursor: pointer; color: #0B1B33; }
.rb-chip .c { font-size: 12px; color: #53617A; font-weight: 500; }
.rb-chip[aria-pressed="true"] { background: var(--oes-ink); border-color: var(--oes-ink); color: #fff; }
.rb-chip[aria-pressed="true"] .c { color: #C9D6EA; }
.rb-phase { margin-bottom: 12px; }
.rb-phase-head { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; background: transparent; border: 0; padding: 8px 2px; cursor: pointer; color: #0B1B33; }
.rb-phase-head[aria-expanded="false"] .rb-chev { transform: rotate(-90deg); }
.rb-chev { color: #53617A; transition: transform .15s; }
.rb-phase-num { font-size: 12px; font-weight: 500; color: #53617A; }
.rb-phase-name { font-size: 17px; font-weight: 700; }
.rb-phase-sum { margin-left: auto; font-size: 13px; color: #53617A; text-align: right; }
.rb-phase-body { display: flex; flex-direction: column; gap: 10px; }
.rb-locked { padding: 14px; border: 1px dashed #AEBBCC; border-radius: 10px; background: #fff; color: #53617A; font-size: 14px; }
.rb-locked b { color: #0B1B33; }
.rb-card { background: #fff; border: 1px solid #D3DBE6; border-radius: 10px; padding: 14px 16px; }
.rb-card.open { border-color: #AEBBCC; box-shadow: 0 2px 10px rgba(0, 23, 56, .08); }
.rb-card-top { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 8px 12px; }
.rb-card h4 { font-size: 16px; font-weight: 600; flex: 1 1 260px; min-width: 0; color: #0B1B33; }
.rb-pill { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 3px 10px; font-size: 12.5px; font-weight: 600; white-space: nowrap; }
.rb-pill i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.rb-s-needs { background: #FFEFCB; color: #6F4300; }
.rb-s-appr { background: #D6EFF3; color: #09505B; }
.rb-s-run { background: #FFE0BA; color: #7A3A00; }
.rb-s-run i, .rb-pulse { animation: rb-pulse 1.2s ease-in-out infinite; }
.rb-s-done { background: #D8F1DF; color: #145F36; }
.rb-s-fu { background: #E5E0FA; color: #3C2C86; }
.rb-s-fail { background: #FBDADA; color: #A31F1F; }
.rb-s-skip { background: #E4E9F0; color: #46536A; }
@keyframes rb-pulse { 50% { opacity: .25; } }
.rb-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.rb-tag { font-size: 12.5px; padding: 2px 8px; border-radius: 6px; background: #E9EEF4; color: #53617A; font-weight: 500; }
.rb-tag.method { color: #0B1B33; font-weight: 600; }
.rb-preview { margin-top: 8px; color: #53617A; font-size: 14px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; white-space: pre-line; }
.rb-from, .rb-hint { margin-top: 8px; font-size: 13px; color: #53617A; }
.rb-warn { margin-top: 8px; font-size: 13px; font-weight: 600; color: #6F4300; }
.rb-err { margin-top: 8px; font-size: 13px; font-weight: 600; color: #A31F1F; }
.rb-progress { display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 14px; color: #53617A; }
.rb-pulse { width: 9px; height: 9px; border-radius: 50%; background: var(--oes-valencia); }
.rb-form { display: grid; gap: 12px; margin-top: 12px; }
.rb-lbl { display: block; font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.rb-form input[type="text"], .rb-form textarea, .rb-form select, .rb-obj-input { width: 100%; border: 1px solid #AEBBCC; border-radius: 8px; background: #F3F5F9; padding: 8px 10px; font: inherit; }
.rb-form textarea { min-height: 190px; resize: vertical; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; line-height: 1.55; }
.rb-seg { display: flex; flex-wrap: wrap; gap: 8px; }
.rb-seg label, .rb-check { display: flex; align-items: center; gap: 8px; font-weight: 500; font-size: 14px; }
.rb-seg label { border: 1px solid #AEBBCC; border-radius: 8px; padding: 7px 12px; cursor: pointer; background: #F3F5F9; }
.rb-seg input, .rb-check input { width: auto; accent-color: var(--oes-valencia); }
.rb-row select { width: auto; }
.rb-review { margin-top: 12px; padding: 12px; border-radius: 8px; background: #E9EEF4; display: grid; gap: 10px; }
.rb-review h5 { font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #53617A; }
.rb-review ul { list-style: disc; padding-left: 20px; font-size: 14px; }
.rb-meters { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.rb-meter .top { display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; }
.rb-meter .track { height: 6px; border-radius: 99px; background: #DCE3EC; margin-top: 4px; overflow: hidden; }
.rb-meter .track span { display: block; height: 100%; background: var(--oes-sky); border-radius: 99px; }
.rb-runbox { display: grid; gap: 12px; }
.rb-runlist { display: grid; gap: 6px; font-size: 14px; }
.rb-runlist li { display: flex; justify-content: space-between; gap: 10px; padding: 6px 0; border-bottom: 1px solid #D3DBE6; }
.rb-confirm { border: 1px solid var(--oes-valencia); border-radius: 10px; padding: 12px; display: grid; gap: 10px; background: #FFF6EB; }
.rb-confirm .rb-row { margin-top: 0; }
.rb-activity ol { display: grid; gap: 10px; margin-top: 10px; }
.rb-activity li { display: grid; grid-template-columns: 48px 1fr; gap: 8px; font-size: 14px; }
.rb-activity time { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; color: #53617A; padding-top: 2px; }
.rb-skipped { margin-top: 4px; }
.rb-skipped li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: center; padding: 8px 0; border-bottom: 1px solid #D3DBE6; font-size: 14px; color: #53617A; }
.rb-reader { position: fixed; inset: 0; background: rgba(0, 23, 56, .45); display: grid; place-items: center; z-index: 500; padding: 16px; }
.rb-reader-box { background: #fff; border-radius: 12px; width: min(900px, 100%); max-height: 90vh; display: flex; flex-direction: column; overflow: hidden; }
.rb-reader-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 18px; border-bottom: 1px solid #D3DBE6; }
.rb-reader-body { padding: 18px; overflow-y: auto; }
@media (prefers-reduced-motion: reduce) { .rb-s-run i, .rb-pulse { animation: none; } .rb-chev { transition: none; } }
/* ===== end Research Board ===== */
```

- [ ] **Step 6: Run the tests and a syntax check**

Run: `node --check static/board.js && node tests/js/test_board_view.js` (expected: `all 18 passed`). Then run `python -m pytest tests/test_board_js.py -v && python -m pytest tests/ -q` (expected: all pass).

- [ ] **Step 7: Commit**

```bash
git add static/board.js static/style.css static/app.js templates/index.html tests/js/test_board_view.js tests/test_board_js.py
git commit -m "Add the Research tab: board rendering, styles and Node view tests"
```

---

### Task 10: Research tab: actions, polling, report reader and app hooks

**Files:**
- Modify: `static/board.js` (browser glue only), `static/app.js` (`resumeActiveRuns`, `triggerDeepResearch`, `showTab`)

**Interfaces:**
- Consumes: the Task 8 API, and the Task 9 render functions and `data-*` attributes.
- Produces: browser globals `boardOpenAddResearch(title)` and `boardMaybeReloadInsights()`. `boardOnTabShown` now refreshes and starts polling.

There's no automated browser test for this glue. Verification is the Node view tests (unchanged), `node --check`, and the browser check in Task 12. Keep the logic in small named functions so it can be read and reviewed.

- [ ] **Step 1: Replace the Task 9 placeholder glue**

In `static/board.js`, replace everything from `// ---- interactions (Task 10) ----` to the closing `})();` with:

```js
    // ---- interactions ----

    function isEditing() {
        const v = board.view;
        return v.objEdit || !!v.adding || Object.keys(v.editing).some(k => v.editing[k]);
    }

    function schedulePoll() {
        clearTimeout(board.timer);
        board.timer = null;
        if (!board.state || !tabVisible()) return;
        if (!board.state.cards.some(c => ACTIVE.includes(c.status))) return;
        board.timer = setTimeout(() => { if (tabVisible()) refreshBoard(); }, 15000);
    }

    function generationRunning(project) {
        return typeof getActiveGenerationForProject === 'function' && !!getActiveGenerationForProject(project);
    }

    async function refreshBoard() {
        const project = syncProject();
        if (!project) { render(); return; }
        const firstLoad = !board.state;
        if (firstLoad) render();
        const seq = ++board.loadSeq;
        try {
            const data = await call('POST', '/api/board/refresh', { project, defer_linking: generationRunning(project) });
            if (seq !== board.loadSeq || project !== activeProject()) return;
            board.state = data.board;
            if (data.board.linked_reports > 0) board.insightsStale = true;
        } catch (e) {
            if (project !== activeProject()) return;
            board.view.message = e.message;
        }
        // Don't redraw under someone who is typing; the next action or refresh will.
        if (firstLoad || !isEditing()) render();
        schedulePoll();
    }

    function takeState(data) {
        board.loadSeq++;  // anything still in flight is now stale
        if (data && data.board) board.state = data.board;
        render();
        schedulePoll();
    }

    function handleError(err, cardId) {
        if (cardId != null) board.view.cardErrors[cardId] = err.message;
        else board.view.message = err.message;
        if (err.status === 404 || err.status === 409) loadBoard(); else render();
    }

    async function attempt(cardId, fn) {
        if (cardId != null) delete board.view.cardErrors[cardId];
        try { await fn(); } catch (err) { handleError(err, cardId); }
    }

    function closeEditor(id) {
        delete board.view.editing[id];
        delete board.view.drafts[id];
    }

    function cardAction(id, action) {
        return call('POST', `/api/board/cards/${id}/${action}`, { project: board.project });
    }

    async function saveDraftEdits(id) {
        const payload = editPayload(board.view.drafts[id]);
        if (Object.keys(payload).length) {
            await call('PATCH', `/api/board/cards/${id}`, Object.assign({ project: board.project }, payload));
        }
    }

    function simple(action) {
        return el => { const id = Number(el.dataset.id); attempt(id, async () => takeState(await cardAction(id, action))); };
    }

    function focusLater(elementId) {
        const el = document.getElementById(elementId);
        if (el) el.focus();
    }

    const ACTIONS = {
        dismiss() { board.view.message = ''; render(); },
        filter(el) { board.view.filter = el.dataset.val; render(); },
        phase(el) { const k = el.dataset.phase; board.view.closedPhases[k] = !board.view.closedPhases[k]; render(); },
        edit(el) { const id = Number(el.dataset.id); board.view.editing[id] = true; render(); focusLater(`rb-prompt-${id}`); },
        close(el) { closeEditor(Number(el.dataset.id)); render(); },
        save(el) {
            const id = Number(el.dataset.id);
            attempt(id, async () => {
                await saveDraftEdits(id);
                closeEditor(id);
                takeState(await call('GET', `/api/board?project=${encodeURIComponent(board.project)}`));
            });
        },
        approve(el) {
            const id = Number(el.dataset.id);
            attempt(id, async () => {
                await saveDraftEdits(id);
                const data = await cardAction(id, 'approve');
                closeEditor(id);
                takeState(data);
            });
        },
        redraft(el) {
            const id = Number(el.dataset.id);
            el.disabled = true;
            el.textContent = 'Drafting…';
            attempt(id, async () => {
                await saveDraftEdits(id);
                const data = await cardAction(id, 'redraft');
                if (board.view.drafts[id]) delete board.view.drafts[id].prompt_text;
                takeState(data);
            });
        },
        unapprove: simple('unapprove'),
        skip: simple('skip'),
        restore: simple('restore'),
        retry: simple('retry'),
        'back-to-draft': simple('back-to-draft'),
        accept: simple('accept'),
        'needs-followup': simple('needs-followup'),
        'mark-failed': simple('mark-failed'),
        add(el) {
            board.view.adding = { phase_key: el.dataset.phase, draft_prompt: true, research_method: 'TARGETED_WEB' };
            render();
            focusLater('rb-add-title');
        },
        'add-cancel'() { board.view.adding = null; render(); },
        'add-save'() {
            const adding = board.view.adding;
            if (!adding) return;
            attempt(null, async () => {
                board.view.busy = true;
                render();
                let data;
                try { data = await call('POST', '/api/board/cards', addPayload(board.project, adding)); }
                finally { board.view.busy = false; }
                board.view.adding = null;
                takeState(data);
            });
        },
        draft() {
            attempt(null, async () => {
                board.view.busy = true;
                board.view.supervisorNote = '';
                render();
                let data;
                try { data = await call('POST', '/api/board/draft', { project: board.project }); }
                finally { board.view.busy = false; }
                board.view.supervisorNote = data.note || '';
                takeState(data);
            });
        },
        'draft-options'() {
            attempt(null, async () => {
                board.view.busy = true;
                render();
                let data;
                try { data = await call('POST', '/api/board/phase7/draft', { project: board.project }); }
                finally { board.view.busy = false; }
                takeState(data);
            });
        },
        'run-open'() { board.view.confirming = true; render(); },
        'run-cancel'() { board.view.confirming = false; render(); },
        'run-start'() {
            const ids = board.state.cards.filter(c => c.status === 'READY').map(c => c.id);
            board.view.confirming = false;
            attempt(null, async () => {
                const data = await call('POST', '/api/board/run', { project: board.project, card_ids: ids });
                const result = data.result || {};
                const notes = [];
                if ((result.not_ready || []).length) {
                    notes.push(`${pluralise(result.not_ready.length, 'research was', 'researches were')} no longer approved and did not start.`);
                }
                if ((result.failed || []).length) {
                    notes.push(`${pluralise(result.failed.length, 'research', 'researches')} could not start: ${result.failed.map(f => f.error).join(' ')}`);
                }
                board.view.message = notes.join(' ');
                takeState(data);
            });
        },
        'obj-edit'() { board.view.objEdit = true; render(); focusLater('rb-obj-input'); },
        'obj-cancel'() { board.view.objEdit = false; render(); },
        'obj-save'() {
            const input = document.getElementById('rb-obj-input');
            const value = input ? input.value : '';
            attempt(null, async () => {
                const data = await call('PUT', '/api/board/objective', { project: board.project, objective: value });
                board.view.objEdit = false;
                const objectiveTab = document.getElementById('project-prompt');
                if (objectiveTab) objectiveTab.value = value;
                if (typeof projectData === 'object' && projectData) projectData.project_prompt = value;
                takeState(data);
            });
        },
        report(el) {
            const id = Number(el.dataset.id);
            attempt(id, async () => {
                const data = await call('GET', `/api/board/cards/${id}/report?project=${encodeURIComponent(board.project)}`);
                showReader(data.report);
            });
        },
    };

    function showReader(report) {
        closeReader();
        const overlay = document.createElement('div');
        overlay.id = 'rb-reader';
        overlay.className = 'rb-reader';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', report.title);
        const body = typeof renderMarkdown === 'function' ? renderMarkdown(report.text) : `<pre>${esc(report.text)}</pre>`;
        const file = report.filename ? `<p class="rb-hint">Saved in your sources as “${esc(report.filename)}”.</p>` : '';
        overlay.innerHTML = `<div class="rb-reader-box"><div class="rb-reader-head"><h3>${esc(report.title)}</h3><button type="button" class="rb-btn" data-reader-close>Close</button></div><div class="rb-reader-body markdown-body">${file}${body}</div></div>`;
        overlay.addEventListener('click', e => { if (e.target === overlay || e.target.closest('[data-reader-close]')) closeReader(); });
        document.body.appendChild(overlay);
        overlay.querySelector('[data-reader-close]').focus();
    }
    function closeReader() { const el = document.getElementById('rb-reader'); if (el) el.remove(); }

    function onField(ev) {
        const el = ev.target;
        if (!el.closest || !el.closest('#research-board')) return;
        if (el.dataset.field && el.dataset.id) {
            const id = Number(el.dataset.id);
            board.view.drafts[id] = board.view.drafts[id] || {};
            if (el.type !== 'radio' || el.checked) board.view.drafts[id][el.dataset.field] = el.value;
        } else if (el.dataset.add && board.view.adding) {
            if (el.type === 'radio' && !el.checked) return;
            board.view.adding[el.dataset.add] = el.type === 'checkbox' ? el.checked : el.value;
            if (ev.type === 'change' && (el.dataset.add === 'draft_prompt' || el.dataset.add === 'phase_key')) render();
        }
    }

    document.addEventListener('click', ev => {
        const el = ev.target.closest && ev.target.closest('#research-board [data-act]');
        if (!el || el.disabled) return;
        const handler = ACTIONS[el.dataset.act];
        if (handler) handler(el);
    });
    document.addEventListener('input', onField);
    document.addEventListener('change', onField);
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape') closeReader(); });

    window.boardOnTabShown = function () { refreshBoard(); };
    window.boardOnProjectLoaded = function () {
        if (activeProject() === board.project) return;
        if (tabVisible()) refreshBoard(); else syncProject();
    };
    window.boardOpenAddResearch = function (title) {
        syncProject();
        board.view.adding = { phase_key: '1', title: title || '', draft_prompt: true, research_method: 'TARGETED_WEB' };
        if (board.state) { render(); focusLater('rb-add-title'); }
    };
    window.boardMaybeReloadInsights = function () {
        const project = activeProject();
        if (!board.insightsStale || !project || generationRunning(project)) return;
        board.insightsStale = false;
        if (typeof loadProject === 'function') loadProject({ reloadInsights: true });
    };
})();
```

Note: `window.boardOpenAddResearch` runs right after `showTab('research')` has started `refreshBoard()`. That refresh renders on its first load even while the add form is open (the `firstLoad` check). That's how the prefilled form appears.

- [ ] **Step 2: Hook `app.js`**

In `static/app.js`:

1. In `resumeActiveRuns()` (line ~3887), change the condition to skip board runs. Board runs are collected by the server, so the old poller must never finalise them:

```js
        if (!TERMINAL.includes(run.status) && !activeResearchRuns[run.id] && !run.research_work_item_id) {
```

2. Replace `triggerDeepResearch(topic)` (line ~2179) with:

```js
function triggerDeepResearch(topic) {
    showTab('research');
    if (typeof boardOpenAddResearch === 'function') boardOpenAddResearch(topic);
}
```

3. In `showTab(name)`, next to the `research` hook added in Task 9, add:

```js
    if (name === 'insights' && typeof boardMaybeReloadInsights === 'function') boardMaybeReloadInsights();
```

Bump the script versions in `templates/index.html` to `app.js?v=13.3` and `board.js?v=2`.

- [ ] **Step 3: Verify**

Run: `node --check static/board.js && node --check static/app.js && node tests/js/test_board_view.js` (expected: no syntax errors, `all 18 passed`). Then run `python -m pytest tests/ -q` (expected: all pass).

Then do a quick manual smoke test with the real app (`python app.py`, open http://localhost:5000, select a project, open **Research**):
- the board loads;
- "+ Add research" opens the form;
- Cancel closes it;
- a filter chip toggles.

Do **not** press Draft, Run or Re-draft against the real app. Those call Anthropic and OpenAI. Task 12 covers them with stubbed services.

- [ ] **Step 4: Commit**

```bash
git add static/board.js static/app.js templates/index.html
git commit -m "Wire Research tab actions, polling, report reader and app hooks"
```

---

### Task 11: Remove the Prompt Developer tab, the Insights panels and the retired endpoints

**Files:**
- Modify: `templates/index.html`, `static/app.js`, `static/style.css`, `routes/ai.py`, `routes/research_tasks.py`, `app.py`, `CLAUDE.md`
- Delete: `routes/supervisor.py`, `tests/test_routes_supervisor.py`
- Test: `tests/test_routes_research_tasks.py`, `tests/test_release_hardening.py` (only if it references removed routes)

**Interfaces:**
- Removes:
  - `/api/prompt-dev` and `/api/supervisor/run|decisions`;
  - `POST /api/research-tasks/<id>/transition`;
  - the `#prompt-dev-tab` markup;
  - `PROMPT_TEMPLATES`, `updatePromptForm`, `generateStructuredPrompt`, `copyPromptDevPrompt`, `savePromptDevAsArtifact`, `runDeepResearchFromPrompt` and `refreshResearchRuns`;
  - the Insights task/Supervisor panel functions and their CSS.
- Keeps: `startRunPolling`, `resumeActiveRuns`, `finalizeRun` and `renderResearchRuns`. They still finish any old manual run already in flight, and `renderResearchRuns` returns early without its container. Also kept: the `task_summary` field in the insights payload, which is harmless.

- [ ] **Step 1: Find every reference before deleting**

Run:

```bash
grep -n "prompt-dev\|PROMPT_TEMPLATES\|updatePromptForm\|generateStructuredPrompt\|copyPromptDevPrompt\|savePromptDevAsArtifact\|runDeepResearchFromPrompt\|refreshResearchRuns\|research-runs-section\|renderTaskList\|refreshPhaseTaskPanels\|setTaskSummaryLine\|renderTaskSummaryLine\|promptCreateResearchTask\|createResearchTask\|transitionResearchTask\|ensureSupervisorPanel\|hideSupervisorPanel\|renderSupervisorDecisionRow\|refreshSupervisorDecisions\|runSupervisor\|showTasks" static/app.js templates/index.html
```

Keep this list. Step 4 re-runs the same grep and expects no output.

- [ ] **Step 2: Remove the markup**

In `templates/index.html`:
- delete the Prompt Developer tab button;
- delete the whole `<div id="prompt-dev-tab" class="tab-content">…</div>` block, including the "Research Runs" section inside it;
- change the `research-runs-indicator` `onclick` to `showTab('research')`;
- bump `app.js?v=13.4`.

- [ ] **Step 3: Remove the JS**

In `static/app.js`:
- Delete from the `// STANDARD LOGIC` comment above `const PROMPT_TEMPLATES` through the end of `runDeepResearchFromPrompt()`. Keep the `// RESEARCH RUN BACKGROUND POLLING` section and everything after it.
- Delete `refreshResearchRuns()`.
- Delete the `updatePromptForm();` call in the init code (line ~337).
- In `renderInsights`, delete the two `${showTasks ? … : ''}` template lines and the three `if (showTasks) … / if (!showTasks) …` panel calls at the end. Remove the `showTasks` parameter and variable only if nothing else uses it.
- Delete the panel functions: `renderTaskSummaryLine`, `renderTaskList`, `refreshPhaseTaskPanels`, `setTaskSummaryLine`, `promptCreateResearchTask`, `createResearchTask`, `transitionResearchTask`, `ensureSupervisorPanel`, `hideSupervisorPanel`, `renderSupervisorDecisionRow`, `refreshSupervisorDecisions` and `runSupervisor`.
- Do **not** touch `viewingHistoricalVersion`. Other code uses it.

In `static/style.css`, delete the rules for `.task-summary-line`, `.phase-task-panel`, `.task-list-empty`, `.task-list`, `.task-row`, `.task-status-*`, `.task-entities`, `.task-transition-select` and `#supervisor-panel` (and its descendants). Find them with `grep -n "task-\|supervisor" static/style.css`. Don't touch the `rb-` block.

- [ ] **Step 4: Check nothing still points at removed code**

Re-run the Step 1 grep (expected: no output). Run `node --check static/app.js`.

- [ ] **Step 5: Remove the retired endpoints**

- `routes/ai.py`: delete the `prompt_dev()` route and the `_strip_prompt_budget_sections` import alias. `prompt_drafting_service.strip_prompt_budget_sections` stays.
- `routes/research_tasks.py`: delete the `POST /api/research-tasks/<int:task_id>/transition` route. In `tests/test_routes_research_tasks.py`, delete the tests that call it, and add:

```python
def test_generic_transition_endpoint_is_gone(client):
    client.post("/api/projects", json={"name": "P"})
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Task")
    resp = client.post(f"/api/research-tasks/{task_id}/transition", json={"project": "P", "status": "RUNNING"})
    assert resp.status_code in (404, 405)
    assert research_work_items_repo.get(task_id)["status"] == "PROPOSED"
```

(Use that file's existing `client` fixture and imports. Add `projects_repo` / `research_work_items_repo` imports if they're missing.)
- Delete `routes/supervisor.py` and `tests/test_routes_supervisor.py`. In `app.py`, remove the `supervisor_bp` import and registration.
- Run `grep -rn "prompt-dev\|prompt_dev\|supervisor/run\|supervisor/decisions" routes/ services/ static/ templates/ tests/ app.py` (expected: no output).

- [ ] **Step 6: Update `CLAUDE.md`**

- Under **Frontend**, change "6 tabs: Builder, Prompt Developer, Insights, …" to `6 tabs: Builder, Research, Insights, Methodology, Objective, Artifacts`.
- Add a **Research** bullet: "Research tab (`static/board.js`, `routes/board.py`, `services/board_service.py`): the Supervisor drafts research cards with prompts built from the phase frameworks (`services/prompt_frameworks.json`); the user edits, approves and runs them; finished reports are reviewed automatically and saved as phase-linked sources. Nothing runs without the user's approval."
- In **Key Conventions**, replace the "7 research phases … hard-coded client-side only" bullet with: "7 research phases: definitions are in `services/phases.py` (server) and `PHASE_DEFINITIONS` in `static/app.js` (client, for Insights). The phase research frameworks live server-side in `services/prompt_frameworks.json`."

- [ ] **Step 7: Run everything**

Run: `python -m ruff check . && python -m pytest tests/ -q && node tests/js/test_board_view.js` (expected: clean, all pass).

- [ ] **Step 8: Commit**

```bash
git add -A templates/index.html static/app.js static/style.css routes/ app.py tests/ CLAUDE.md
git commit -m "Remove the Prompt Developer tab, the Insights task panels and retired endpoints"
```

---

### Task 12: End-to-end check in a real browser with stubbed services (controller task)

The controller (not a subagent) runs this, using Claude in Chrome. It needs a small dev-only launcher that runs the real app against a throwaway database and folder, with Anthropic and OpenAI replaced by deterministic fakes.

**Files:**
- Create: `scripts/run_board_demo.py`

- [ ] **Step 1: Write the launcher**

```python
# scripts/run_board_demo.py
"""Dev only: run the real app on http://127.0.0.1:5055 against a throwaway database and projects
folder, with Anthropic and OpenAI replaced by fakes, to exercise the Research tab end to end.

    python scripts/run_board_demo.py

Nothing touches your real database, projects or API accounts. Stop with Ctrl+C.
"""
import itertools
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = Path(tempfile.mkdtemp(prefix="board-demo-"))
os.environ["DATABASE_PATH"] = str(WORK / "demo.db")
os.environ["BACKUP_DIR"] = str(WORK / "backups")
os.environ.setdefault("ANTHROPIC_API_KEY", "demo")
os.environ.setdefault("OPENAI_API_KEY", "demo")
os.chdir(WORK)

from app import create_app  # noqa: E402
from services import llm_service, research_execution_service, supervisor_service  # noqa: E402

DRAFTED = ("ROLE: Higher-education market analyst working for OES.\nQUESTION: {title}\n"
           "COVER: providers, pricing, delivery, accreditation.\nOUTPUT: a comparison table and five insights. Cite every claim.")


def fake_completion(system_prompt, user_message, max_tokens=4000):
    if "RESEARCH TITLE:" in user_message:
        title = user_message.split("RESEARCH TITLE:", 1)[1].splitlines()[0].strip()
        return DRAFTED.format(title=title)
    return "## Findings\n\n- A finding from your files.\n- Another finding.\n"


class Block:
    def __init__(self, name, payload):
        self.type, self.name, self.input = "tool_use", name, payload


review_outcomes = itertools.cycle(["COMPLETE", "FOLLOW_UP_REQUIRED", "NEEDS_HUMAN"])


class FakeAnthropic:
    def __init__(self, api_key=None):
        self.messages = self

    def create(self, **kwargs):
        names = {t["name"] for t in kwargs["tools"]}
        if "propose_tasks" in names:
            return SimpleNamespace(content=[Block("propose_tasks", {"reason": "Phases with no research yet", "tasks": [
                {"phase_key": "1", "title": "Overview of online Psychology postgraduate programs", "research_method": "TARGETED_WEB",
                 "focus": ["Programs", "Providers"], "rationale": "Phase 1 has no findings yet."},
                {"phase_key": "2", "title": "Who enrols in online Psychology postgraduate study", "research_method": "TARGETED_WEB",
                 "focus": ["Career changers"], "rationale": "Phase 2 explains who enrols and why."},
                {"phase_key": "5", "title": "Curriculum structure from uploaded handbooks", "research_method": "FILE_ANALYSIS",
                 "focus": ["Units"], "rationale": "Curriculum detail is usually in your files."},
            ]})])
        outcome = next(review_outcomes)
        payload = {"completeness_score": 0.62, "evidence_score": 0.55, "identified_gaps": ["Intake dates not on an official page."],
                   "outcome": outcome, "reason": "Demo review."}
        if outcome == "FOLLOW_UP_REQUIRED":
            payload["followup"] = {"title": "Verify intake dates on official pages", "focus": ["Intakes"],
                                   "research_method": "TARGETED_WEB", "rationale": "Intake dates had no official source."}
        return SimpleNamespace(content=[Block("review_outcome", payload)])


started = {}


def fake_start(prompt):
    response_id = f"resp_{len(started) + 1}"
    started[response_id] = time.time()
    return SimpleNamespace(id=response_id, status="queued")


def fake_retrieve(response_id):
    if time.time() - started.get(response_id, 0) < 20:
        return SimpleNamespace(id=response_id, status="in_progress", output=[], output_text=None, error=None, last_error=None)
    text = "# Demo report\n\nProviders offer graduate certificates and masters.\n\nSee the provider page."
    annotation = SimpleNamespace(type="url_citation", start_index=0, end_index=0, title="Provider page", url="https://example.edu/psych")
    content = [SimpleNamespace(type="output_text", text=text, annotations=[annotation])]
    return SimpleNamespace(id=response_id, status="completed", output_text=text,
                           output=[SimpleNamespace(type="message", content=content)], error=None, last_error=None)


llm_service.prompt_completion = fake_completion
supervisor_service.anthropic.Anthropic = FakeAnthropic
research_execution_service.openai_service.start_deep_research = fake_start
research_execution_service.openai_service.retrieve_deep_research = fake_retrieve

if __name__ == "__main__":
    print(f"Board demo running from {WORK}")
    create_app().run(host="127.0.0.1", port=5055, debug=False)
```

Run it as `python scripts/run_board_demo.py` (background). Check first that `config.py` reads `DATABASE_PATH` and `BACKUP_DIR` from the environment at import, as `CLAUDE.md` says. If it doesn't, set them on the app config after `create_app()` instead.

- [ ] **Step 2: Drive the flow in Chrome and capture screenshots**

On http://127.0.0.1:5055, create a project "Demo" and upload a small `.txt` file. Then, on the Research tab:
1. Edit the objective and save it. The Objective tab shows the same text.
2. Press **Draft next researches**. Three cards appear, each "Needs your approval", each with a framework-drafted prompt.
3. Open one card and change its prompt. Make it too short and press Approve: you see the error. Restore a proper prompt and approve it. Approve the "My files" card as well.
4. Edit an approved card and save it. It goes back to "Needs your approval".
5. Run → Confirm. The cards show "Running · …". The web card finishes after about 20 seconds, on the next 15-second poll. Its review appears (scores, gaps, Read report). The report opens in the reader. The Insights tab shows the report linked to Phase 1.
6. A follow-up card appears with "Suggested after reviewing …". A "Needs your review" card offers Accept, Needs follow-up and Mark failed.
7. Skip a card, then Restore it from the Skipped list.
8. Confirm Phase 7 shows "N of 6 are ready".
9. Confirm the Prompt Developer tab is gone, and that Insights phase cards no longer show task panels.

Fix anything broken with the normal task-review loop. Save the screenshots in the scratchpad and show the user the key ones.

- [ ] **Step 3: Commit the launcher**

```bash
git add scripts/run_board_demo.py
git commit -m "Add a dev-only launcher to try the Research tab with stubbed AI services"
```

- [ ] **Step 4: Offer the real end-to-end run**

Ask the user before running one real research on a small test project, because it costs money on OpenAI. Only proceed with their go-ahead.
