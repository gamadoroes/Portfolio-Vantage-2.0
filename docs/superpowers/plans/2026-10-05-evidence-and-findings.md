# Phase 4b-1: Evidence and Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn finished research reports into individual cited facts and a few cited conclusions per phase. Facts are extracted automatically right after each review, in a second call that reads the full report (or later, with an "Extract facts" button). They are shown and rejectable on the Insights tab, searchable, and listed for the Supervisor when it drafts, so it aims at the gaps.

**Architecture:**
- **Storage.** Migration `0007` adds four tables (`cited_sources`, `facts`, `conclusions`, `conclusion_facts`) and one column (`research_runs.facts_extracted_at`). All their SQL lives in `db/repositories/evidence_repo.py`.
- **Tools.** Seven new tools go through the 4a door (`run_tool`):
  - five small ones in `services/tools/evidence.py`;
  - the batch tool `record_research_facts` and the extraction tool `extract_research_facts` in `services/tools/research_facts.py`.
- **The review is unchanged.** Same tool, same prompt, `max_tokens` 2000, and it still sees only the first 8,000 characters.
- **Extraction after the review.** After a successful, non-`FAILED` review, `review_card` calls `extract_research_facts` as `system`. That makes one Claude call which reads up to 60,000 characters and answers with `record_research_facts` (the only tool on `EXTRACT_MENU`). The "Extract facts" button calls the same tool as `user`. A failed extraction never changes the review.
- **`supervisor_service`** cleans the extraction answer (garbled `<parameter>` markup, strings, cut-short lists) before any tool sees it, and adds a capped `# KNOWN FACTS` section to the drafting briefing.
- **Insights.** A new `static/evidence.js` builds DOM nodes (text only, safe links), fed by a new `routes/evidence.py`.

**Tech Stack:** Python 3.13, Flask, SQLite 3.50 (upsert), pydantic 2.12.5, pytest, Anthropic SDK, vanilla JS (Node for view tests).

**Spec:** `docs/superpowers/specs/2026-10-05-evidence-and-findings-design.md` (amended: facts come from a separate call after the review, §4.1).

**Refinements to the spec made while planning (deliberate):**
- The search tool's text field is called `words`, not `query`. The 4a acceptance test forbids any input field whose name contains `query` (`FORBIDDEN_FIELDS` in `tests/test_tools_structure.py`). The HTTP route still takes `?q=`.
- Each `skipped` entry is `{item, reason, saved}`. `saved` is `True` only for "saved without its source", so the Activity feed's skipped count counts only items that were really not saved.
- A run counts as "facts extracted" (`facts_extracted_at` set) only once at least one fact from it has been saved. `extract_research_facts` claims the run first, whether it was called by the system after a review or by the button. If nothing is saved, or the call fails, it releases the claim, so the "Extract facts" button stays available.
- A duplicate claim that matches a fact the person **rejected** is returned as that fact but treated as skipped ("It matches a fact you rejected.") and is never cited by a new conclusion. Rejected stays rejected.
- The automatic step runs at the end of `review_card`, after the `review_outcome` decision is recorded. `review_card`'s success return gains an `extraction` key (`None` / `{"ok": True, "facts", "conclusions", "skipped"}` / `{"ok": False, "error"}`); nothing else in it changes.
- The extraction runs inline in the board's refresh, like the review does, so a refresh that reviews a card now takes one more Claude call.
- Existing review tests whose fake client answers every call with the review block are expected to see the automatic extraction fail quietly. They keep passing unchanged.
- `GET /api/evidence` without `phase` returns every phase in one response, so an Insights render makes one request. `?phase=` filters as in the spec.
- On screen, facts are labelled by their stable id (`#12`), and "Based on facts 12, 15" links to `#ev-fact-12`, so numbers don't shift when a fact is rejected.
- A separate decision type, `facts_recorded`, produces the Activity line "Supervisor saved 18 facts and 2 conclusions from "X"." It is written by the extraction call, after `review_outcome` on the automatic path, and before `user_extract_facts` on the button path. The automatic path writes no `user_extract_facts`. The `_USER_TEXT` lines for reject, restore and extract are added in the task that first records them.
- `record_research_facts` clips each item's text fields to their limits (a `Clipped` input type). `save_source`, `save_evidence` and `create_finding` stay strict. List sizes stay strict at the tool (25 facts, 3 conclusions); the Supervisor's clean-up cuts to those limits before calling.
- The Extract button has its own route, `POST /api/board/cards/<id>/extract-facts`, on the board blueprint. It returns a `note`. Werkzeug matches it ahead of the generic `/<action>` route (checked).
- Each Insights phase gets a sibling slot `<div class="ev-slot" data-evidence-phase="N">` **after** its `.insight-phase-card`, not inside it. The card has `onclick="openPhaseEditor(...)"`, so clicks on Reject inside it would open the editor.
- `extract_research_facts` imports `supervisor_service` inside its handler. `supervisor_service` imports `services.tools`, so a top-level import would be circular.

## Global Constraints

- Migrations are additive only. Never edit `0001`–`0006`. The new file is `db/migrations/0007_evidence_and_findings.sql`. It adds four new tables and one nullable column, `research_runs.facts_extracted_at`. The existing `findings` / `evidence` / `finding_evidence` tables and the Insights write-ups, their version history and linked files are not touched.
- `services/review_limits.py` is not changed: `MAX_CONTEXT_CHARS = 60000`, `MAX_RUN_OUTPUT_PREVIEW_CHARS = 300`, `MAX_RUN_OUTPUT_REVIEW_CHARS = 8000`, `MAX_REVIEW_OUTPUT_TOTAL_CHARS = 24000`. The new constants `MAX_KNOWN_FACTS_CHARS = 4000` and `MAX_EXTRACT_REPORT_CHARS = 60000` live in `services/supervisor_service.py`, not in `review_limits`.
- **The review is unchanged.** `evaluate_research_output` gets no new fields, its `max_tokens` stays 2000, and `REVIEW_SYSTEM_PROMPT` and `services/tools/review.py` are untouched. The extraction call uses `max_tokens` 6000. Expect roughly 10–15 cents more per reviewed report.
- An extraction (automatic or button) never changes a card's status, scores, gaps or follow-up. A `FAILED` review triggers no extraction.
- Limits (verbatim from spec §3.1):
  - `url` is http/https only, ≤2000. `title` ≤300, `publisher` ≤200, `published_date` ≤40.
  - `claim` 1–500, `quote` ≤500, `as_of` ≤40. Conclusion `text` 1–600, `fact_ids` 1–10.
  - A batch holds ≤25 facts and ≤3 conclusions.
  - Search: text ≤200, `limit` 1–20, default 10.
- Callers:
  - `save_source`, `save_evidence`, `create_finding`: supervisor, system.
  - `record_research_facts`: supervisor.
  - `search_existing_evidence`: supervisor, user, system.
  - `extract_research_facts`: user, system (never the Supervisor).
  - `update_evidence_status`: user only.
- The Supervisor saves facts only through `record_research_facts`, the single tool on `EXTRACT_MENU`. No Supervisor menu holds `save_source`, `save_evidence`, `create_finding`, `update_evidence_status` or `extract_research_facts`. `REVIEW_MENU` and `DRAFTING_MENU` are unchanged.
- No tool input field name contains `path`, `file`, `filename`, `sql`, `query` or `table`. Modules in `services/tools/` must not import `os`, `pathlib`, `shutil`, `sqlite3`, `subprocess`, `io`, `builtins`, `importlib` or `db.connection`, and must not call `open(`.
- Nothing is ever deleted. Reject is a status (`active` | `rejected`).
- Synthesis (Phase 7 options report) cards are never mined for facts.
- Tests never call Anthropic or OpenAI: stub `supervisor_service.anthropic.Anthropic` and `llm_service.prompt_completion`. Any test that creates a project, reads `project_prompt` or renders the board calls `monkeypatch.chdir(tmp_path)` first.
- Board routes, existing on-screen messages and HTTP status codes stay as they are today. The only board change is one new route (`/extract-facts`), plus two new fields: `can_extract_facts` on board cards and `id`/`can_extract_facts` in the report response.
- User-facing text is plain English, with no tool names or codes.
- All AI and web text goes into the DOM as text: `textContent` in `evidence.js`, `esc()` in `board.js`'s HTML strings. Links are made only for `http`/`https` addresses, with `target="_blank"` and `rel="noopener noreferrer"`. `evidence.js` never uses `innerHTML`, `outerHTML` or `insertAdjacentHTML`.
- Ruff: every touched Python file must be clean (`python -m ruff check <files>`). Add no dependency.
- Full suite: `python -m pytest tests/ -q`. The baseline on `vantage-fresh` is 661 passed. Also run `node tests/js/test_board_view.js` (baseline: all 20 passed).
- Bash on Windows: the repo path contains `!!`, so start bash commands with `set +H;` or use PowerShell.

## Review Focus

1. **The live model garbles the extraction answer.** Facts can arrive as one JSON string, wrapped in a stray `<parameter name="facts">`, cut short mid-list, or as one fact whose own fields spill to the top level, and a garbled string can swallow the conclusions. What can be recovered must be saved, and the review that came before must stand untouched (Task 4 `_clean_facts` tests and jumbled-answer tests).
2. **Two extractions race for the same report.** This happens with a double-click, two tabs, or a click while the automatic step runs. Exactly one Claude call should be made. A failed call or an empty answer should leave the button available (Task 4 claim tests, including "a review of a report already being mined makes no second call"; Task 5 route test).
3. **Hostile or broken web text.** A claim containing `<img onerror>`, a `javascript:` or `data:` source address, or a page title with markup must show as plain text, with no link (Task 2 `save_source` validation, Task 3 bad-address test, Task 7 Node tests).
4. **A claim the person rejected turns up again in a later report.** It must stay rejected, not be re-activated, and not be cited by a new conclusion (Task 3 test).
5. **The drafting briefing gets flooded.** Many known facts must stay within their own 4,000-character budget (Task 6 budget test). The `review_outcome` decision no longer carries any facts, and `facts_recorded` stores only counts, so the decision log the briefing reads stays short (Task 4 decision-order test).

---

### Task 1: Migration 0007 and the evidence repository

**Files:**
- Create: `db/migrations/0007_evidence_and_findings.sql`, `db/repositories/evidence_repo.py`, `tests/test_repositories_evidence.py`
- Modify: `db/repositories/research_runs_repo.py` (two functions), `tests/test_db_migrations.py`

**Interfaces:**
- Produces (`db/repositories/evidence_repo.py`):
  - `get_or_create_source(project_id, url, title, publisher=None, published_date=None) -> (int, bool)`: returns `(id, created)`.
  - `get_source(id) -> Row | None`.
  - `get_or_create_fact(project_id, phase_key, research_work_item_id, claim, claim_key, quote=None, cited_source_id=None, as_of=None, run_id=None) -> (int, bool)`: returns `(id, created)`. An existing `claim_key` returns that fact whatever its status.
  - `get_fact(id)` and `set_fact_status(id, status)`.
  - `create_conclusion(project_id, phase_key, research_work_item_id, text, fact_ids) -> int`.
  - `get_conclusion(id)` and `set_conclusion_status(id, status)`.
  - `list_facts(project_id, phase_key=None)`: all statuses, oldest first. Each row is a `facts` row plus `source_url`, `source_title`, `source_publisher`, `source_published_date` and `card_title`.
  - `list_conclusions(project_id, phase_key=None)`: all statuses, oldest first, plus `card_title`.
  - `conclusion_fact_links(project_id) -> {conclusion_id: [(fact_id, fact_status), ...]}`, with fact ids ascending.
  - `search_facts(project_id, words, phase_key=None, limit=10)`: active facts only, newest first, with the same columns as `list_facts`. Every word must appear in the claim, the quote or the page title, case-insensitively. `%` and `_` are literal.
  - `known_facts(project_id, per_phase=10) -> {phase_key: {"count": int, "recent": [claim, ...]}}`: active facts only, newest first.
- Produces (`db/repositories/research_runs_repo.py`):
  - `claim_facts_extraction(id) -> bool`: True only for the call that set `facts_extracted_at`.
  - `release_facts_extraction(id)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_db_migrations.py` change `assert count == 6` to `assert count == 7` and append:

```python
def test_evidence_tables(temp_db):
    with get_connection() as conn:
        def cols(table):
            return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

        assert {"id", "project_id", "url", "title", "publisher", "published_date", "created_at"} <= cols("cited_sources")
        assert {"id", "project_id", "phase_key", "claim", "claim_key", "quote", "cited_source_id", "as_of",
                "research_work_item_id", "run_id", "status", "created_at", "updated_at"} <= cols("facts")
        assert {"id", "project_id", "phase_key", "text", "research_work_item_id", "status",
                "created_at", "updated_at"} <= cols("conclusions")
        assert {"conclusion_id", "fact_id"} <= cols("conclusion_facts")
        assert "facts_extracted_at" in cols("research_runs")
```

`tests/test_repositories_evidence.py`:

```python
import pytest

from db.repositories import evidence_repo, projects_repo, research_runs_repo, research_work_items_repo


@pytest.fixture
def pid(temp_db):
    return projects_repo.get_or_create_id("P")


def _card(pid, phase="4", title="Fees"):
    return research_work_items_repo.create(pid, phase, title)


def _fact(pid, card, claim, phase="4", **kw):
    return evidence_repo.get_or_create_fact(pid, phase, card, claim, claim.lower(), **kw)[0]


def test_a_page_is_stored_once_per_project(pid):
    first = evidence_repo.get_or_create_source(pid, "https://a.example/fees", "Fees", publisher="A Uni")
    again = evidence_repo.get_or_create_source(pid, "https://a.example/fees", "Other title")
    other = evidence_repo.get_or_create_source(projects_repo.get_or_create_id("Other"), "https://a.example/fees", "Fees")
    assert first[1] is True and again == (first[0], False) and other[0] != first[0]
    row = evidence_repo.get_source(first[0])
    assert (row["title"], row["publisher"], row["project_id"]) == ("Fees", "A Uni", pid)


def test_a_claim_is_stored_once_per_project(pid):
    card = _card(pid)
    first = evidence_repo.get_or_create_fact(pid, "4", card, "Deakin charges $3,000.", "deakin charges $3,000")
    again = evidence_repo.get_or_create_fact(pid, "4", card, "DEAKIN charges $3,000", "deakin charges $3,000")
    assert first[1] is True and again == (first[0], False)
    row = evidence_repo.get_fact(first[0])
    assert (row["status"], row["phase_key"], row["research_work_item_id"], row["claim"]) == (
        "active", "4", card, "Deakin charges $3,000.")


def test_conclusions_link_their_facts(pid):
    card = _card(pid)
    a, b = _fact(pid, card, "A"), _fact(pid, card, "B")
    conclusion_id = evidence_repo.create_conclusion(pid, "4", card, "Both matter", [a, b])
    assert evidence_repo.get_conclusion(conclusion_id)["status"] == "active"
    evidence_repo.set_fact_status(b, "rejected")
    assert evidence_repo.conclusion_fact_links(pid) == {conclusion_id: [(a, "active"), (b, "rejected")]}


def test_status_changes_keep_the_row(pid):
    card = _card(pid)
    fact = _fact(pid, card, "A")
    conclusion_id = evidence_repo.create_conclusion(pid, "4", card, "C", [fact])
    evidence_repo.set_fact_status(fact, "rejected")
    evidence_repo.set_conclusion_status(conclusion_id, "rejected")
    assert evidence_repo.get_fact(fact)["status"] == "rejected"
    assert evidence_repo.get_conclusion(conclusion_id)["status"] == "rejected"


def test_list_facts_joins_page_and_research_oldest_first(pid):
    card = _card(pid, title="Fees")
    source, _ = evidence_repo.get_or_create_source(pid, "https://a.example", "A page")
    first = _fact(pid, card, "First", cited_source_id=source, as_of="2026")
    second = _fact(pid, _card(pid, phase="2", title="Students"), "Second", phase="2")
    rows = evidence_repo.list_facts(pid)
    assert [r["id"] for r in rows] == [first, second]
    assert (rows[0]["source_url"], rows[0]["source_title"], rows[0]["card_title"], rows[0]["as_of"]) == (
        "https://a.example", "A page", "Fees", "2026")
    assert rows[1]["source_url"] is None
    assert [r["id"] for r in evidence_repo.list_facts(pid, "2")] == [second]


def test_list_conclusions_by_phase(pid):
    card = _card(pid)
    fact = _fact(pid, card, "A")
    four = evidence_repo.create_conclusion(pid, "4", card, "Four", [fact])
    two = evidence_repo.create_conclusion(pid, "2", _card(pid, phase="2"), "Two", [fact])
    assert [r["id"] for r in evidence_repo.list_conclusions(pid)] == [four, two]
    assert [r["text"] for r in evidence_repo.list_conclusions(pid, "4")] == ["Four"]
    assert evidence_repo.list_conclusions(pid, "4")[0]["card_title"] == "Fees"


def test_search_matches_every_word_in_claim_quote_or_page_title(pid):
    card = _card(pid)
    source, _ = evidence_repo.get_or_create_source(pid, "https://deakin.example", "Deakin fees 2026")
    by_title = _fact(pid, card, "Units cost $3,000", cited_source_id=source)
    by_quote = _fact(pid, card, "Census dates vary", quote="Deakin census in March")
    rejected = _fact(pid, card, "Deakin is cheap")
    evidence_repo.set_fact_status(rejected, "rejected")
    assert [r["id"] for r in evidence_repo.search_facts(pid, ["deakin"])] == [by_quote, by_title]
    assert [r["id"] for r in evidence_repo.search_facts(pid, ["DEAKIN", "census"])] == [by_quote]
    assert evidence_repo.search_facts(pid, ["deakin"], phase_key="2") == []
    assert len(evidence_repo.search_facts(pid, [], limit=1)) == 1


def test_search_treats_percent_and_underscore_as_plain_characters(pid):
    card = _card(pid)
    _fact(pid, card, "Fees rose 5% in 2026")
    _fact(pid, card, "Fees rose 5 points")
    assert [r["claim"] for r in evidence_repo.search_facts(pid, ["5%"])] == ["Fees rose 5% in 2026"]
    assert evidence_repo.search_facts(pid, ["_"]) == []


def test_known_facts_counts_active_facts_and_lists_the_newest(pid):
    card = _card(pid)
    for i in range(12):
        _fact(pid, card, f"Fact {i}")
    gone = _fact(pid, card, "Rejected one")
    evidence_repo.set_fact_status(gone, "rejected")
    known = evidence_repo.known_facts(pid, per_phase=10)
    assert set(known) == {"4"}
    assert known["4"]["count"] == 12
    assert known["4"]["recent"][:2] == ["Fact 11", "Fact 10"] and len(known["4"]["recent"]) == 10
    assert "Rejected one" not in known["4"]["recent"]


def test_a_run_can_be_claimed_for_fact_extraction_once(pid):
    research_runs_repo.create("run_1", pid, None, None, "p")
    assert research_runs_repo.claim_facts_extraction("run_1") is True
    assert research_runs_repo.claim_facts_extraction("run_1") is False
    assert research_runs_repo.get("run_1")["facts_extracted_at"]
    research_runs_repo.release_facts_extraction("run_1")
    assert research_runs_repo.get("run_1")["facts_extracted_at"] is None
    assert research_runs_repo.claim_facts_extraction("run_1") is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_db_migrations.py tests/test_repositories_evidence.py -v`
Expected: FAIL. The migration count is 6, and `evidence_repo` cannot be imported.

- [ ] **Step 3: Implement**

`db/migrations/0007_evidence_and_findings.sql`:

```sql
-- Phase 4b-1 (evidence and findings). Fully additive: four new tables and one nullable column.
-- The older findings / evidence / finding_evidence tables (the Insights phase write-ups and their
-- linked files) are not touched. See docs/superpowers/specs/2026-10-05-evidence-and-findings-design.md
-- section 2.

CREATE TABLE cited_sources (
    id             INTEGER PRIMARY KEY,
    project_id     INTEGER NOT NULL REFERENCES projects(id),
    url            TEXT NOT NULL,
    title          TEXT NOT NULL,
    publisher      TEXT,
    published_date TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(project_id, url)
);

CREATE TABLE facts (
    id                    INTEGER PRIMARY KEY,
    project_id            INTEGER NOT NULL REFERENCES projects(id),
    phase_key             TEXT NOT NULL,
    claim                 TEXT NOT NULL,
    claim_key             TEXT NOT NULL,
    quote                 TEXT,
    cited_source_id       INTEGER REFERENCES cited_sources(id),
    as_of                 TEXT,
    research_work_item_id INTEGER NOT NULL REFERENCES research_work_items(id),
    run_id                TEXT REFERENCES research_runs(id),
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    UNIQUE(project_id, claim_key)
);

CREATE INDEX idx_facts_project_phase ON facts(project_id, phase_key, status);

CREATE TABLE conclusions (
    id                    INTEGER PRIMARY KEY,
    project_id            INTEGER NOT NULL REFERENCES projects(id),
    phase_key             TEXT NOT NULL,
    text                  TEXT NOT NULL,
    research_work_item_id INTEGER NOT NULL REFERENCES research_work_items(id),
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX idx_conclusions_project_phase ON conclusions(project_id, phase_key);

CREATE TABLE conclusion_facts (
    conclusion_id INTEGER NOT NULL REFERENCES conclusions(id),
    fact_id       INTEGER NOT NULL REFERENCES facts(id),
    PRIMARY KEY (conclusion_id, fact_id)
);

-- Set when facts have been taken from this run's report (by the extraction after a review, or the "Extract facts" button),
-- and used as an atomic claim so a double-click cannot pay for the same report twice.
ALTER TABLE research_runs ADD COLUMN facts_extracted_at TEXT;
```

`db/repositories/evidence_repo.py`:

```python
# db/repositories/evidence_repo.py
"""Facts, the web pages they cite, and conclusions (Phase 4b). All SQL for these tables lives here.

Not to be confused with the older `evidence` / `findings` tables, which hold the Insights phase write-ups.
"""
from datetime import datetime

from ..connection import get_connection

_FACT_SELECT = (
    "SELECT f.*, s.url AS source_url, s.title AS source_title, s.publisher AS source_publisher, "
    "s.published_date AS source_published_date, w.title AS card_title "
    "FROM facts f "
    "LEFT JOIN cited_sources s ON s.id = f.cited_source_id "
    "LEFT JOIN research_work_items w ON w.id = f.research_work_item_id "
)


def _now():
    return datetime.now().isoformat()


def _like(word):
    """A LIKE pattern that matches the word anywhere, with % and _ taken literally."""
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def get_or_create_source(project_id, url, title, publisher=None, published_date=None):
    """(id, created). The same address in the same project returns the page already saved."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO cited_sources (project_id, url, title, publisher, published_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, url) DO NOTHING",
            (project_id, url, title, publisher, published_date, _now()),
        )
        created = cur.rowcount == 1
        row = conn.execute(
            "SELECT id FROM cited_sources WHERE project_id = ? AND url = ?", (project_id, url)
        ).fetchone()
    return row["id"], created


def get_source(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM cited_sources WHERE id = ?", (id,)).fetchone()


def get_or_create_fact(project_id, phase_key, research_work_item_id, claim, claim_key,
                       quote=None, cited_source_id=None, as_of=None, run_id=None):
    """(id, created). The same claim_key in the same project returns the fact already saved, whatever its status."""
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO facts (project_id, phase_key, claim, claim_key, quote, cited_source_id, as_of, "
            " research_work_item_id, run_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?) ON CONFLICT(project_id, claim_key) DO NOTHING",
            (project_id, phase_key, claim, claim_key, quote, cited_source_id, as_of,
             research_work_item_id, run_id, now, now),
        )
        created = cur.rowcount == 1
        row = conn.execute(
            "SELECT id FROM facts WHERE project_id = ? AND claim_key = ?", (project_id, claim_key)
        ).fetchone()
    return row["id"], created


def get_fact(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM facts WHERE id = ?", (id,)).fetchone()


def set_fact_status(id, status):
    with get_connection() as conn:
        conn.execute("UPDATE facts SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), id))


def create_conclusion(project_id, phase_key, research_work_item_id, text, fact_ids):
    """One conclusion and its links to the facts behind it, written together."""
    now = _now()
    with get_connection() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                "INSERT INTO conclusions (project_id, phase_key, text, research_work_item_id, status, "
                " created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (project_id, phase_key, text, research_work_item_id, now, now),
            )
            conclusion_id = cur.lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO conclusion_facts (conclusion_id, fact_id) VALUES (?, ?)",
                [(conclusion_id, fact_id) for fact_id in fact_ids],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return conclusion_id


def get_conclusion(id):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM conclusions WHERE id = ?", (id,)).fetchone()


def set_conclusion_status(id, status):
    with get_connection() as conn:
        conn.execute("UPDATE conclusions SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), id))


def list_facts(project_id, phase_key=None):
    """Every fact of the project (rejected ones too), oldest first, with its page and research title."""
    sql, params = _FACT_SELECT + "WHERE f.project_id = ?", [project_id]
    if phase_key is not None:
        sql += " AND f.phase_key = ?"
        params.append(phase_key)
    with get_connection() as conn:
        return conn.execute(sql + " ORDER BY f.id", params).fetchall()


def list_conclusions(project_id, phase_key=None):
    """Every conclusion of the project (rejected ones too), oldest first, with its research title."""
    sql = ("SELECT c.*, w.title AS card_title FROM conclusions c "
           "LEFT JOIN research_work_items w ON w.id = c.research_work_item_id WHERE c.project_id = ?")
    params = [project_id]
    if phase_key is not None:
        sql += " AND c.phase_key = ?"
        params.append(phase_key)
    with get_connection() as conn:
        return conn.execute(sql + " ORDER BY c.id", params).fetchall()


def conclusion_fact_links(project_id):
    """{conclusion_id: [(fact_id, fact_status), ...]} for every conclusion of the project."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT cf.conclusion_id, cf.fact_id, f.status FROM conclusion_facts cf "
            "JOIN conclusions c ON c.id = cf.conclusion_id JOIN facts f ON f.id = cf.fact_id "
            "WHERE c.project_id = ? ORDER BY cf.conclusion_id, cf.fact_id",
            (project_id,),
        ).fetchall()
    links = {}
    for row in rows:
        links.setdefault(row["conclusion_id"], []).append((row["fact_id"], row["status"]))
    return links


def search_facts(project_id, words, phase_key=None, limit=10):
    """Active facts containing every word (in the claim, the quote or the page title), newest first."""
    sql, params = _FACT_SELECT + "WHERE f.project_id = ? AND f.status = 'active'", [project_id]
    if phase_key is not None:
        sql += " AND f.phase_key = ?"
        params.append(phase_key)
    for word in words:
        sql += (" AND (f.claim LIKE ? ESCAPE '\\' OR COALESCE(f.quote, '') LIKE ? ESCAPE '\\'"
                " OR COALESCE(s.title, '') LIKE ? ESCAPE '\\')")
        params += [_like(word)] * 3
    sql += " ORDER BY f.id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        return conn.execute(sql, params).fetchall()


def known_facts(project_id, per_phase=10):
    """Per phase: how many active facts there are, and the newest claims."""
    with get_connection() as conn:
        counts = conn.execute(
            "SELECT phase_key, COUNT(*) AS n FROM facts WHERE project_id = ? AND status = 'active' "
            "GROUP BY phase_key ORDER BY phase_key",
            (project_id,),
        ).fetchall()
        known = {}
        for row in counts:
            recent = conn.execute(
                "SELECT claim FROM facts WHERE project_id = ? AND phase_key = ? AND status = 'active' "
                "ORDER BY id DESC LIMIT ?",
                (project_id, row["phase_key"], per_phase),
            ).fetchall()
            known[row["phase_key"]] = {"count": row["n"], "recent": [r["claim"] for r in recent]}
    return known
```

Append to `db/repositories/research_runs_repo.py`:

```python
def claim_facts_extraction(id):
    """Mark this run's report as mined for facts. True only for the call that set it, so a
    double-click (or two tabs) cannot pay for the same report twice."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE research_runs SET facts_extracted_at = ? WHERE id = ? AND facts_extracted_at IS NULL",
            (now, id),
        )
        return cur.rowcount == 1


def release_facts_extraction(id):
    """Undo a claim when nothing was taken from the report, so the person can try again."""
    with get_connection() as conn:
        conn.execute("UPDATE research_runs SET facts_extracted_at = NULL WHERE id = ?", (id,))
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_db_migrations.py tests/test_repositories_evidence.py -v` (expected: all pass). Then run `python -m ruff check db/repositories/evidence_repo.py db/repositories/research_runs_repo.py tests/test_repositories_evidence.py tests/test_db_migrations.py` and `python -m pytest tests/ -q`.
Expected: all pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0007_evidence_and_findings.sql db/repositories/evidence_repo.py db/repositories/research_runs_repo.py tests/test_repositories_evidence.py tests/test_db_migrations.py
git commit -m "Add evidence tables (migration 0007) and the evidence repository"
```

---

### Task 2: Tools to save, search, reject and restore facts, pages and conclusions

**Files:**
- Create: `services/evidence_service.py`, `services/tools/evidence.py`, `tests/test_tools_evidence.py`
- Modify: `services/tools/types.py` (add `RowId`), `services/tools/__init__.py` (register), `services/board_service.py` (`_USER_TEXT`), `tests/test_tools_structure.py` (`EXPECTED_CALLERS`)

**Interfaces:**
- Consumes: `evidence_repo` and `research_runs_repo` (Task 1). From 4a: `run_tool`, `register`, `Tool`, `ToolError`, `ToolInput`, `CardId`, `Text` and `activity.record_user_action(ctx, decision_type, card_id=None, **detail)`.
- Produces:
  - `services.tools.types.RowId`: an int from 1 to 2**63-1.
  - `services.evidence_service.fact_dict(row) -> {"id", "phase_key", "claim", "quote", "as_of", "status", "source": None | {"url", "title", "publisher", "published_date"}, "card_id", "card_title", "created_at"}`. It takes a row from `evidence_repo.list_facts` or `search_facts`, and empty values become `""`.
  - Constants in `services.tools.evidence`: `URL_CHARS=2000`, `SOURCE_TITLE_CHARS=300`, `PUBLISHER_CHARS=200`, `DATE_CHARS=40`, `CLAIM_CHARS=500`, `QUOTE_CHARS=500`, `AS_OF_CHARS=40`, `CONCLUSION_CHARS=600`, `MAX_FACTS_PER_CONCLUSION=10`. Functions: `is_web_address(url) -> bool` and `claim_key(claim) -> str`.
  - Tool `save_source` (callers: supervisor, system). Inputs `{url, title, publisher?, published_date?}`; returns `{"source_id", "created"}`.
  - Tool `save_evidence` (callers: supervisor, system). Inputs `{task_id, claim, quote?, source_id?, as_of?, run_id?}`; returns `{"fact_id", "created", "status"}`.
  - Tool `create_finding` (callers: supervisor, system). Inputs `{task_id, text, fact_ids}`; returns `{"conclusion_id"}`.
  - Tool `search_existing_evidence` (callers: supervisor, user, system). Inputs `{words?, phase_key?, limit=10}`; returns `{"facts": [fact_dict, ...]}`.
  - Tool `update_evidence_status` (callers: user). Inputs `{kind: fact|conclusion, id, action: reject|restore}`; returns `{"id", "status"}`. It records the Activity decision `user_{action}_{kind}` only when the status really changes.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools_evidence.py`:

```python
import pytest

from db.repositories import agent_decisions_repo, evidence_repo, projects_repo, research_runs_repo
from services import board_service, research_task_service, tools
from services.tools import evidence


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return projects_repo.get_or_create_id("P")


def _card(pid, phase="4", title="Fees"):
    return research_task_service.create_task(pid, phase, title, research_method="TARGETED_WEB")


def _source(url="https://deakin.edu.au/fees", title="Deakin fees", project="P", **extra):
    return tools.run_tool("save_source", "system", project, dict({"url": url, "title": title}, **extra))


def _fact(card, claim="Deakin charges $3,000 per unit.", project="P", caller="system", **extra):
    return tools.run_tool("save_evidence", caller, project, dict({"task_id": card, "claim": claim}, **extra))


def _status(kind, item_id, action, caller="user", project="P"):
    return tools.run_tool("update_evidence_status", caller, project, {"kind": kind, "id": item_id, "action": action})


# ---- save_source ----

def test_a_page_is_saved_once(pid):
    first = _source(publisher="Deakin University", published_date="2026-02-01")
    again = _source(title="Another title")
    assert first.data["created"] is True
    assert again.data == {"source_id": first.data["source_id"], "created": False}
    row = evidence_repo.get_source(first.data["source_id"])
    assert (row["title"], row["publisher"], row["published_date"]) == ("Deakin fees", "Deakin University", "2026-02-01")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://deakin.edu.au/x", "deakin.edu.au/fees", "https://", ""])
def test_a_page_needs_a_web_address(pid, url):
    assert _source(url=url).error["code"] == "invalid_input"


def test_each_project_has_its_own_pages(pid):
    projects_repo.get_or_create_id("Other")
    assert _source().data["source_id"] != _source(project="Other").data["source_id"]


# ---- save_evidence ----

def test_a_fact_takes_its_phase_from_the_research(pid):
    card = _card(pid, phase="2")
    source_id = _source().data["source_id"]
    result = _fact(card, quote="$3,000 per unit", source_id=source_id, as_of="2026")
    assert result.data["created"] is True and result.data["status"] == "active"
    row = evidence_repo.get_fact(result.data["fact_id"])
    assert (row["phase_key"], row["research_work_item_id"], row["cited_source_id"], row["as_of"]) == (
        "2", card, source_id, "2026")
    assert row["claim_key"] == "deakin charges $3,000 per unit"


def test_the_same_claim_returns_the_fact_already_saved(pid):
    card = _card(pid)
    first = _fact(card).data
    again = _fact(card, claim="  DEAKIN charges   $3,000 per unit!! ").data
    assert again == {"fact_id": first["fact_id"], "created": False, "status": "active"}


def test_claim_key():
    assert evidence.claim_key("  Deakin  Charges $3,000.\n") == "deakin charges $3,000"
    assert evidence.claim_key("...") == ""


def test_a_fact_links_only_to_this_projects_page_and_this_researchs_run(pid):
    card = _card(pid)
    projects_repo.get_or_create_id("Other")
    theirs = _source(project="Other").data["source_id"]
    assert _fact(card, source_id=theirs).error["code"] == "not_found"
    research_runs_repo.create("run_other", pid, None, None, "p")
    research_runs_repo.update("run_other", research_work_item_id=_card(pid, title="Other card"))
    assert _fact(card, run_id="run_other").error["code"] == "not_found"
    research_runs_repo.create("run_mine", pid, None, None, "p")
    research_runs_repo.update("run_mine", research_work_item_id=card)
    assert evidence_repo.get_fact(_fact(card, run_id="run_mine").data["fact_id"])["run_id"] == "run_mine"


@pytest.mark.parametrize("claim", ["", "   ", "x" * 501, "?!"])
def test_a_fact_needs_a_claim_of_a_sensible_length(pid, claim):
    assert _fact(_card(pid), claim=claim).error["code"] == "invalid_input"


def test_a_fact_for_another_projects_research_is_not_found(pid):
    theirs = _card(projects_repo.get_or_create_id("Other"))
    assert _fact(theirs).error["code"] == "not_found"


# ---- create_finding ----

def test_a_conclusion_cites_its_facts(pid):
    card = _card(pid)
    a = _fact(card).data["fact_id"]
    b = _fact(card, claim="Monash runs three intakes").data["fact_id"]
    result = tools.run_tool("create_finding", "system", "P",
                            {"task_id": card, "text": "Fees and intakes differ", "fact_ids": [a, b, a]})
    conclusion_id = result.data["conclusion_id"]
    row = evidence_repo.get_conclusion(conclusion_id)
    assert (row["phase_key"], row["text"], row["status"]) == ("4", "Fees and intakes differ", "active")
    assert evidence_repo.conclusion_fact_links(pid) == {conclusion_id: [(a, "active"), (b, "active")]}


def test_a_conclusion_cannot_cite_a_rejected_or_foreign_fact(pid):
    card = _card(pid)
    fact_id = _fact(card).data["fact_id"]
    _status("fact", fact_id, "reject")
    finding = {"task_id": card, "text": "Pricey", "fact_ids": [fact_id]}
    assert tools.run_tool("create_finding", "system", "P", finding).error["code"] == "conflict"
    other_card = _card(projects_repo.get_or_create_id("Other"))
    theirs = _fact(other_card, project="Other").data["fact_id"]
    assert tools.run_tool("create_finding", "system", "P", dict(finding, fact_ids=[theirs])).error["code"] == "not_found"


@pytest.mark.parametrize("fact_ids", [[], list(range(1, 12))])
def test_a_conclusion_cites_one_to_ten_facts(pid, fact_ids):
    result = tools.run_tool("create_finding", "system", "P", {"task_id": _card(pid), "text": "x", "fact_ids": fact_ids})
    assert result.error["code"] == "invalid_input"


# ---- search_existing_evidence ----

def test_search_finds_active_facts_newest_first(pid):
    card = _card(pid)
    older = _fact(card, claim="Deakin charges $3,000 per unit").data["fact_id"]
    newer = _fact(card, claim="Deakin has three intakes").data["fact_id"]
    gone = _fact(card, claim="Deakin is the cheapest").data["fact_id"]
    _fact(_card(pid, phase="2", title="Students"), claim="Most Deakin students work full time")
    _status("fact", gone, "reject")
    found = tools.run_tool("search_existing_evidence", "user", "P", {"words": "deakin", "phase_key": "4"}).data["facts"]
    assert [f["id"] for f in found] == [newer, older]
    assert found[0]["card_title"] == "Fees" and found[0]["source"] is None


def test_search_has_a_limit_and_lists_the_newest_without_words(pid):
    card = _card(pid)
    for i in range(25):
        _fact(card, claim=f"Fact number {i}")
    found = tools.run_tool("search_existing_evidence", "supervisor", "P", {}).data["facts"]
    assert len(found) == 10 and found[0]["claim"] == "Fact number 24"
    assert len(tools.run_tool("search_existing_evidence", "system", "P", {"limit": 20}).data["facts"]) == 20
    assert tools.run_tool("search_existing_evidence", "user", "P", {"limit": 21}).error["code"] == "invalid_input"


def test_search_shows_the_page_a_fact_cites(pid):
    source_id = _source().data["source_id"]
    _fact(_card(pid), source_id=source_id)
    (found,) = tools.run_tool("search_existing_evidence", "user", "P", {"words": "deakin fees"}).data["facts"]
    assert found["source"] == {"url": "https://deakin.edu.au/fees", "title": "Deakin fees", "publisher": "",
                               "published_date": ""}


# ---- update_evidence_status ----

def test_reject_and_restore_keep_the_row_and_show_in_the_activity_feed(pid):
    card = _card(pid)
    fact_id = _fact(card).data["fact_id"]
    conclusion_id = tools.run_tool("create_finding", "system", "P",
                                   {"task_id": card, "text": "Pricey", "fact_ids": [fact_id]}).data["conclusion_id"]
    assert _status("fact", fact_id, "reject").data == {"id": fact_id, "status": "rejected"}
    assert _status("fact", fact_id, "reject").data["status"] == "rejected"  # again: no change, no new Activity line
    assert _status("conclusion", conclusion_id, "reject").data["status"] == "rejected"
    assert _status("conclusion", conclusion_id, "restore").data["status"] == "active"
    assert evidence_repo.get_fact(fact_id)["status"] == "rejected"
    texts = [a["text"] for a in board_service.get_board_state("P")["activity"]]
    assert texts == ["You restored a conclusion.", "You rejected a conclusion.", "You rejected a fact."]


@pytest.mark.parametrize("caller", ["supervisor", "system"])
def test_only_the_person_rejects_or_restores(pid, caller):
    fact_id = _fact(_card(pid)).data["fact_id"]
    assert _status("fact", fact_id, "reject", caller=caller).error["code"] == "not_allowed"
    assert evidence_repo.get_fact(fact_id)["status"] == "active"


def test_reject_is_scoped_to_the_project(pid):
    other = projects_repo.get_or_create_id("Other")
    theirs = _fact(_card(other), project="Other").data["fact_id"]
    assert _status("fact", theirs, "reject").error["code"] == "not_found"
    assert _status("conclusion", 99999, "reject").error["code"] == "not_found"
    assert agent_decisions_repo.list_for_project(pid) == []


def test_the_person_cannot_save_facts_directly(pid):
    card = _card(pid)
    assert _source().ok and _fact(card, caller="supervisor").ok
    for name, inputs in (("save_source", {"url": "https://a.example", "title": "A"}),
                         ("save_evidence", {"task_id": card, "claim": "A claim"}),
                         ("create_finding", {"task_id": card, "text": "x", "fact_ids": [1]})):
        assert tools.run_tool(name, "user", "P", inputs).error["code"] == "not_allowed"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_evidence.py -v`
Expected: FAIL. `services.tools.evidence` cannot be imported.

- [ ] **Step 3: Implement**

Append to `services/tools/types.py`:

```python
# A row id for facts, pages and conclusions: bounded like CardId.
RowId = Annotated[int, Field(ge=1, le=2**63 - 1)]
```

`services/evidence_service.py`:

```python
# services/evidence_service.py
"""Facts and conclusions as the screens and tools show them. All SQL is in db/repositories/evidence_repo.py."""


def fact_dict(row):
    """One fact from evidence_repo.list_facts / search_facts, as the Insights tab and the search tool show it."""
    source = None
    if row["source_url"]:
        source = {"url": row["source_url"], "title": row["source_title"] or "",
                  "publisher": row["source_publisher"] or "", "published_date": row["source_published_date"] or ""}
    return {
        "id": row["id"], "phase_key": row["phase_key"], "claim": row["claim"], "quote": row["quote"] or "",
        "as_of": row["as_of"] or "", "status": row["status"], "source": source,
        "card_id": row["research_work_item_id"], "card_title": row["card_title"] or "", "created_at": row["created_at"],
    }
```

`services/tools/evidence.py`:

```python
# services/tools/evidence.py
"""Facts, the web pages they cite, and conclusions: saving them (Supervisor and system), searching them
(anyone) and rejecting or restoring them (the person only). Nothing is ever deleted; reject is a status."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from db.repositories import evidence_repo, research_runs_repo, research_work_items_repo

from .. import evidence_service
from .activity import record_user_action
from .registry import Tool, ToolError, register
from .types import CardId, RowId, Text, ToolInput

URL_CHARS, SOURCE_TITLE_CHARS, PUBLISHER_CHARS, DATE_CHARS = 2000, 300, 200, 40
CLAIM_CHARS, QUOTE_CHARS, AS_OF_CHARS = 500, 500, 40
CONCLUSION_CHARS, MAX_FACTS_PER_CONCLUSION = 600, 10
SEARCH_CHARS, MAX_SEARCH_WORDS, MAX_SEARCH_RESULTS = 200, 10, 20
RUN_ID_CHARS = 100
PhaseKey = Literal["1", "2", "3", "4", "5", "6", "7"]
SAVERS = frozenset({"supervisor", "system"})
ALL_CALLERS = frozenset({"supervisor", "user", "system"})


def is_web_address(url):
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def claim_key(claim):
    """A claim as compared for duplicates: lower case, single spaces, no trailing punctuation."""
    return " ".join(str(claim or "").lower().split()).rstrip(" .,;:!?")


class SaveSourceInput(ToolInput):
    url: Text(URL_CHARS, min_chars=1) = Field(description="The page's address, starting with http:// or https://.")
    title: Text(SOURCE_TITLE_CHARS, min_chars=1)
    publisher: Text(PUBLISHER_CHARS) = ""
    published_date: Text(DATE_CHARS) = ""

    @field_validator("url")
    @classmethod
    def _web_address(cls, value):
        if not is_web_address(value):
            raise ValueError("use a web address that starts with http:// or https://")
        return value


def save_source(ctx, inputs):
    source_id, created = evidence_repo.get_or_create_source(
        ctx.project_id, inputs.url, inputs.title,
        publisher=inputs.publisher or None, published_date=inputs.published_date or None,
    )
    return {"source_id": source_id, "created": created}


class SaveEvidenceInput(ToolInput):
    task_id: CardId
    claim: Text(CLAIM_CHARS, min_chars=1)
    quote: Text(QUOTE_CHARS) = ""
    source_id: RowId | None = None
    as_of: Text(AS_OF_CHARS) = ""
    run_id: Text(RUN_ID_CHARS) | None = None


def save_evidence(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    if not card["phase_key"]:
        raise ToolError("conflict", "This research has no phase, so its facts have nowhere to go.")
    if inputs.source_id is not None:
        source = evidence_repo.get_source(inputs.source_id)
        if source is None or source["project_id"] != ctx.project_id:
            raise ToolError("not_found", f"No such page: {inputs.source_id}", ["source_id"])
    if inputs.run_id is not None:
        run = research_runs_repo.get(inputs.run_id)
        if run is None or run["project_id"] != ctx.project_id or run["research_work_item_id"] != inputs.task_id:
            raise ToolError("not_found", f"No such run for this research: {inputs.run_id}", ["run_id"])
    key = claim_key(inputs.claim)
    if not key:
        raise ToolError("invalid_input", "The claim has no words.", ["claim"])
    fact_id, created = evidence_repo.get_or_create_fact(
        ctx.project_id, card["phase_key"], inputs.task_id, inputs.claim, key,
        quote=inputs.quote or None, cited_source_id=inputs.source_id, as_of=inputs.as_of or None,
        run_id=inputs.run_id,
    )
    return {"fact_id": fact_id, "created": created, "status": evidence_repo.get_fact(fact_id)["status"]}


class CreateFindingInput(ToolInput):
    task_id: CardId
    text: Text(CONCLUSION_CHARS, min_chars=1)
    fact_ids: list[RowId] = Field(min_length=1, max_length=MAX_FACTS_PER_CONCLUSION)


def create_finding(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    fact_ids = list(dict.fromkeys(inputs.fact_ids))
    for fact_id in fact_ids:
        fact = evidence_repo.get_fact(fact_id)
        if fact is None or fact["project_id"] != ctx.project_id:
            raise ToolError("not_found", f"No such fact: {fact_id}", ["fact_ids"])
        if fact["status"] != "active":
            raise ToolError("conflict", f"Fact {fact_id} was rejected, so it can't support a conclusion.", ["fact_ids"])
    conclusion_id = evidence_repo.create_conclusion(ctx.project_id, card["phase_key"], inputs.task_id, inputs.text, fact_ids)
    return {"conclusion_id": conclusion_id}


class SearchInput(ToolInput):
    words: Text(SEARCH_CHARS) = Field(default="", description="Words to look for. Leave empty for the newest facts.")
    phase_key: PhaseKey | None = None
    limit: int = Field(default=10, ge=1, le=MAX_SEARCH_RESULTS)


def search_existing_evidence(ctx, inputs):
    words = inputs.words.split()[:MAX_SEARCH_WORDS]
    rows = evidence_repo.search_facts(ctx.project_id, words, inputs.phase_key, inputs.limit)
    return {"facts": [evidence_service.fact_dict(row) for row in rows]}


class StatusInput(ToolInput):
    kind: Literal["fact", "conclusion"]
    id: RowId
    action: Literal["reject", "restore"]


_KINDS = {
    "fact": (evidence_repo.get_fact, evidence_repo.set_fact_status),
    "conclusion": (evidence_repo.get_conclusion, evidence_repo.set_conclusion_status),
}


def update_evidence_status(ctx, inputs):
    get_row, set_status = _KINDS[inputs.kind]
    row = get_row(inputs.id)
    if row is None or row["project_id"] != ctx.project_id:
        raise ToolError("not_found", f"No such {inputs.kind}: {inputs.id}")
    status = "rejected" if inputs.action == "reject" else "active"
    if row["status"] != status:
        set_status(inputs.id, status)
        record_user_action(ctx, f"user_{inputs.action}_{inputs.kind}")
    return {"id": inputs.id, "status": status}


register(Tool(
    name="save_source",
    description="Record a web page that facts cite. The same address again returns the page already saved.",
    input_model=SaveSourceInput, handler=save_source, callers=SAVERS,
))
register(Tool(
    name="save_evidence",
    description="Save one cited fact from a research. Its phase comes from the research. "
                "The same claim again returns the fact already saved.",
    input_model=SaveEvidenceInput, handler=save_evidence, callers=SAVERS, id_fields=("task_id",),
))
register(Tool(
    name="create_finding",
    description="Save a short conclusion for a research's phase, citing 1 to 10 of the project's active facts.",
    input_model=CreateFindingInput, handler=create_finding, callers=SAVERS, id_fields=("task_id",),
))
register(Tool(
    name="search_existing_evidence",
    description="Search the project's active facts by words in the claim, quote or page title. Newest first.",
    input_model=SearchInput, handler=search_existing_evidence, callers=ALL_CALLERS,
))
register(Tool(
    name="update_evidence_status",
    description="Reject or restore a fact or a conclusion. Only the person can do this.",
    input_model=StatusInput, handler=update_evidence_status, callers=frozenset({"user"}),
))
```

In `services/tools/__init__.py` replace the docstring line and the module import with:

```python
Each tool module registers its tools when imported; the `cards`, `evidence`, `review`, `runs`, `state` and `status`
imports below do that.
"""
from . import cards, evidence, review, runs, state, status  # noqa: F401  (register every tool)
```

In `services/board_service.py`, add to `_USER_TEXT`:

```python
    "user_reject_fact": "You rejected a fact.",
    "user_restore_fact": "You restored a fact.",
    "user_reject_conclusion": "You rejected a conclusion.",
    "user_restore_conclusion": "You restored a conclusion.",
```

In `tests/test_tools_structure.py`, add to `EXPECTED_CALLERS`:

```python
    "save_source": {"supervisor", "system"},
    "save_evidence": {"supervisor", "system"},
    "create_finding": {"supervisor", "system"},
    "search_existing_evidence": {"supervisor", "user", "system"},
    "update_evidence_status": {"user"},
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_evidence.py tests/test_tools_structure.py -v` (expected: all pass). The structure tests now also check the new tools' field names and that `update_evidence_status` refuses the Supervisor. Then run `python -m ruff check services/evidence_service.py services/tools tests/test_tools_evidence.py tests/test_tools_structure.py services/board_service.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add services/evidence_service.py services/tools/evidence.py services/tools/types.py services/tools/__init__.py services/board_service.py tests/test_tools_evidence.py tests/test_tools_structure.py
git commit -m "Add tools to save, search, reject and restore facts, sources and conclusions"
```

---

### Task 3: record_research_facts — one report's facts in one batch, each item checked on its own

**Files:**
- Create: `services/tools/research_facts.py`, `tests/test_tools_research_facts.py`
- Modify: `services/tools/types.py` (add `Clipped`), `services/tools/__init__.py` (register), `tests/test_tools_structure.py` (`EXPECTED_CALLERS`)

**Interfaces:**
- Consumes: from Task 2, the `evidence.is_web_address` function, the limit constants and the tools `save_source`, `save_evidence` and `create_finding`, all called as child calls through `run_tool`.
- Produces:
  - `services.tools.types.Clipped(max_chars)`: a str type that turns `None` into `""` and anything else into a stripped string cut to `max_chars`. It never fails.
  - In `services.tools.research_facts`:
    - `MAX_FACTS = 25` and `MAX_CONCLUSIONS = 3`.
    - `FactItem`, with fields `claim`, `quote`, `source_url`, `source_title`, `publisher`, `published_date` and `as_of`, all `Clipped`. Anything that is not an object becomes an empty item.
    - `ConclusionItem`, with `text` (`Clipped`) and `fact_numbers: list[int]` (accepts `"1, 2"`, `["1", 2.0]`). Anything that is not an object becomes an empty item.
    - `FactList` and `ConclusionList`: lists of those items. One object becomes a list of one; anything else that is not a list becomes `[]`. `max_length` is 25 and 3.
    - `OPTIONS_REPORT`, the message used when the research is the options report.
  - Tool `record_research_facts` (callers: supervisor; `id_fields=("task_id",)`). Inputs `{task_id, facts: FactList, conclusions: ConclusionList}`. Returns `{"fact_ids": [int], "conclusion_ids": [int], "skipped": [{"item": "fact 2" | "conclusion 1", "reason": str, "saved": bool}]}`. It refuses a SYNTHESIS card with `conflict`. Facts are tagged with the card's latest run id when there is one.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools_research_facts.py`:

```python
import pytest

from db.repositories import evidence_repo, projects_repo, research_runs_repo, tool_calls_repo
from services import research_task_service, tools

FACT = {"claim": "Deakin charges $3,000 per unit", "quote": "$3,000 per unit", "source_url": "https://deakin.edu.au/fees",
        "source_title": "Deakin fees", "publisher": "Deakin University", "published_date": "2026-02-01", "as_of": "2026"}
FACT2 = {"claim": "Monash runs three intakes", "source_url": "https://monash.edu/intakes", "source_title": "Monash intakes"}


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return projects_repo.get_or_create_id("P")


def _card(pid, phase="4", method="TARGETED_WEB", title="Fees"):
    return research_task_service.create_task(pid, phase, title, research_method=method)


def _record(card, facts=(), conclusions=(), caller="supervisor"):
    inputs = {"task_id": card, "facts": facts if isinstance(facts, dict) else list(facts),
              "conclusions": list(conclusions)}
    return tools.run_tool("record_research_facts", caller, "P", inputs)


def test_a_batch_saves_facts_pages_and_conclusions_as_logged_child_calls(pid):
    card = _card(pid)
    research_runs_repo.create("run_1", pid, None, None, "p")
    research_runs_repo.update("run_1", research_work_item_id=card, status="completed", output_text="Report")
    result = _record(card, [FACT, FACT2], [{"text": "Fees and intakes differ", "fact_numbers": [1, 2]}])
    assert result.ok and result.data["skipped"] == []
    assert len(result.data["fact_ids"]) == 2 and len(result.data["conclusion_ids"]) == 1
    first = evidence_repo.get_fact(result.data["fact_ids"][0])
    assert (first["run_id"], first["quote"], first["as_of"]) == ("run_1", "$3,000 per unit", "2026")
    source = evidence_repo.get_source(first["cited_source_id"])
    assert (source["url"], source["publisher"], source["published_date"]) == (
        "https://deakin.edu.au/fees", "Deakin University", "2026-02-01")
    children = [r["tool"] for r in reversed(tool_calls_repo.list_for_project(pid)) if r["parent_call_id"] == result.call_id]
    assert children == ["save_source", "save_evidence", "save_source", "save_evidence", "create_finding"]


def test_one_bad_fact_is_skipped_with_a_reason_and_the_rest_are_saved(pid):
    result = _record(_card(pid), [FACT, {"claim": ""}, "just a sentence", 42, {"quote": "no claim"}])
    assert len(result.data["fact_ids"]) == 1
    assert result.data["skipped"] == [{"item": f"fact {n}", "reason": "It has no claim.", "saved": False}
                                      for n in (2, 3, 4, 5)]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "www.deakin.edu.au", "ftp://deakin.edu.au/fees"])
def test_a_bad_page_address_saves_the_fact_without_its_page(pid, url):
    result = _record(_card(pid), [dict(FACT, source_url=url)])
    (fact_id,) = result.data["fact_ids"]
    assert evidence_repo.get_fact(fact_id)["cited_source_id"] is None
    (skip,) = result.data["skipped"]
    assert skip["item"] == "fact 1" and skip["saved"] is True
    assert skip["reason"].startswith("Saved without its source")


def test_long_text_is_clipped_not_refused(pid):
    result = _record(_card(pid), [dict(FACT, claim="c" * 900, quote="q" * 900, source_title="t" * 400, as_of=2026)])
    row = evidence_repo.get_fact(result.data["fact_ids"][0])
    assert (len(row["claim"]), len(row["quote"]), row["as_of"]) == (500, 500, "2026")
    assert len(evidence_repo.get_source(row["cited_source_id"])["title"]) == 300


def test_a_page_with_no_title_is_named_by_its_address(pid):
    result = _record(_card(pid), [dict(FACT, source_title="")])
    row = evidence_repo.get_fact(result.data["fact_ids"][0])
    assert evidence_repo.get_source(row["cited_source_id"])["title"] == "https://deakin.edu.au/fees"


def test_a_conclusion_keeps_only_the_facts_that_were_saved(pid):
    result = _record(_card(pid), [FACT, {"claim": ""}, FACT2],
                     [{"text": "Fees and intakes differ", "fact_numbers": [1, 2, 3]}])
    (conclusion_id,) = result.data["conclusion_ids"]
    assert [fact_id for fact_id, _ in evidence_repo.conclusion_fact_links(pid)[conclusion_id]] == result.data["fact_ids"]


@pytest.mark.parametrize("conclusion,reason", [
    ({"text": "Nothing behind it", "fact_numbers": [2]}, "None of the facts it cites were saved."),
    ({"text": "Out of range", "fact_numbers": [9]}, "None of the facts it cites were saved."),
    ({"text": "No numbers"}, "None of the facts it cites were saved."),
    ({"fact_numbers": [1]}, "It has no text."),
])
def test_a_conclusion_with_no_saved_facts_is_skipped(pid, conclusion, reason):
    result = _record(_card(pid), [FACT, {"claim": ""}], [conclusion])
    assert result.data["conclusion_ids"] == []
    assert {"item": "conclusion 1", "reason": reason, "saved": False} in result.data["skipped"]


@pytest.mark.parametrize("numbers", ["1, 2", ["1", 2.0], [1, 2, 2]])
def test_fact_numbers_sent_loosely_are_understood(pid, numbers):
    result = _record(_card(pid), [FACT, FACT2], [{"text": "Both", "fact_numbers": numbers}])
    (conclusion_id,) = result.data["conclusion_ids"]
    assert len(evidence_repo.conclusion_fact_links(pid)[conclusion_id]) == 2


def test_a_duplicate_claim_counts_as_saved(pid):
    card = _card(pid)
    first = _record(card, [FACT]).data
    again = _record(card, [dict(FACT, claim="deakin charges $3,000 per unit.")]).data
    assert again["fact_ids"] == first["fact_ids"] and again["skipped"] == []


def test_a_claim_the_person_rejected_is_not_brought_back(pid):
    card = _card(pid)
    (fact_id,) = _record(card, [FACT]).data["fact_ids"]
    tools.run_tool("update_evidence_status", "user", "P", {"kind": "fact", "id": fact_id, "action": "reject"})
    result = _record(card, [FACT], [{"text": "Pricey", "fact_numbers": [1]}]).data
    assert result["fact_ids"] == [] and result["conclusion_ids"] == []
    assert {"item": "fact 1", "reason": "It matches a fact you rejected.", "saved": False} in result["skipped"]
    assert evidence_repo.get_fact(fact_id)["status"] == "rejected"


def test_one_object_instead_of_a_list_is_accepted(pid):
    assert len(_record(_card(pid), FACT).data["fact_ids"]) == 1


def test_a_batch_holds_at_most_25_facts_and_3_conclusions(pid):
    card = _card(pid)
    assert _record(card, [FACT] * 26).error["code"] == "invalid_input"
    assert _record(card, [FACT], [{"text": "c", "fact_numbers": [1]}] * 4).error["code"] == "invalid_input"


def test_the_options_report_is_not_mined_for_facts(pid):
    result = _record(_card(pid, phase="7", method="SYNTHESIS", title="Options"), [FACT])
    assert result.error["code"] == "conflict" and evidence_repo.list_facts(pid) == []


@pytest.mark.parametrize("caller", ["user", "system"])
def test_only_the_supervisor_records_a_batch(pid, caller):
    assert _record(_card(pid), [FACT], caller=caller).error["code"] == "not_allowed"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_research_facts.py -v`
Expected: FAIL. `record_research_facts` isn't registered (`not_allowed`).

- [ ] **Step 3: Implement**

In `services/tools/types.py`, add `import json` at the top and append:

```python
def _clip_to(limit):
    def clip(value):
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        return str(value).strip()[:limit]
    return clip


def Clipped(max_chars):
    """Text that is cut to max_chars instead of refused, for model output that is saved item by item."""
    return Annotated[str, BeforeValidator(_clip_to(max_chars)), StringConstraints(max_length=max_chars)]
```

`services/tools/research_facts.py`:

```python
# services/tools/research_facts.py
"""Facts and conclusions from one research report, saved in one batch through child calls to save_source,
save_evidence and create_finding. Each item is checked on its own, so one bad fact never stops the others;
the result says exactly what was saved and what was skipped."""
import re
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from db.repositories import research_runs_repo, research_work_items_repo

from .evidence import (
    AS_OF_CHARS,
    CLAIM_CHARS,
    CONCLUSION_CHARS,
    DATE_CHARS,
    MAX_FACTS_PER_CONCLUSION,
    PUBLISHER_CHARS,
    QUOTE_CHARS,
    SOURCE_TITLE_CHARS,
    URL_CHARS,
    is_web_address,
)
from .registry import Tool, ToolError, register, run_tool
from .types import CardId, Clipped, ToolInput

MAX_FACTS, MAX_CONCLUSIONS = 25, 3
OPTIONS_REPORT = "Facts are not taken from the options report: it is written from other research, not from sources."
NO_CLAIM = "It has no claim."
REJECTED = "It matches a fact you rejected."
NO_TEXT = "It has no text."
NO_FACTS_LEFT = "None of the facts it cites were saved."
BAD_ADDRESS = "Saved without its source: the address does not start with http:// or https://."


def as_object_list(value):
    """A list from a field that should be one: one object becomes a list of one; anything else that is not a
    list becomes empty. (Garbled strings are recovered by the Supervisor before the tool is called.)"""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _numbers(value):
    """Fact numbers sent loosely: 2, "2", 2.0, "1, 2", ["1", 2]."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, float):
        return [int(value)] if value.is_integer() else []
    if isinstance(value, str):
        return [int(n) for n in re.findall(r"\d+", value)]
    if isinstance(value, (list, tuple)):
        return [n for item in value if not isinstance(item, (list, tuple)) for n in _numbers(item)]
    return []


class FactItem(ToolInput):
    claim: Clipped(CLAIM_CHARS) = Field(default="", description="One short, checkable claim the report makes.")
    quote: Clipped(QUOTE_CHARS) = Field(default="", description="The report's own words for it, if short.")
    source_url: Clipped(URL_CHARS) = Field(default="", description="Address of the page it came from (http or https).")
    source_title: Clipped(SOURCE_TITLE_CHARS) = Field(default="", description="Title of that page.")
    publisher: Clipped(PUBLISHER_CHARS) = ""
    published_date: Clipped(DATE_CHARS) = ""
    as_of: Clipped(AS_OF_CHARS) = Field(default="", description="The date or year the claim applies to, e.g. 2026.")

    @model_validator(mode="before")
    @classmethod
    def _only_objects(cls, value):
        return value if isinstance(value, dict) else {}


class ConclusionItem(ToolInput):
    text: Clipped(CONCLUSION_CHARS) = Field(default="", description="A short conclusion for this phase.")
    fact_numbers: Annotated[list[int], BeforeValidator(_numbers)] = Field(
        default_factory=list, description="The facts it rests on: their positions in your facts list, counting from 1.")

    @model_validator(mode="before")
    @classmethod
    def _only_objects(cls, value):
        return value if isinstance(value, dict) else {}


FactList = Annotated[list[FactItem], BeforeValidator(as_object_list), Field(
    max_length=MAX_FACTS, description="The report's key facts (at most 25), each with the page it came from.")]
ConclusionList = Annotated[list[ConclusionItem], BeforeValidator(as_object_list), Field(
    max_length=MAX_CONCLUSIONS, description="Up to 3 short conclusions for this phase, each citing its facts.")]


class RecordFactsInput(ToolInput):
    task_id: CardId
    facts: FactList = []
    conclusions: ConclusionList = []


def _call(ctx, name, inputs):
    return run_tool(name, ctx.caller, ctx.project_name, inputs, parent_call_id=ctx.call_id)


def _skip(skipped, item, reason, saved=False):
    skipped.append({"item": item, "reason": reason, "saved": saved})


def _save_fact(ctx, task_id, run_id, number, fact, skipped):
    """The fact's id if it was saved (or already known and active), else None. Adds to skipped as needed."""
    item = f"fact {number}"
    if not fact.claim:
        _skip(skipped, item, NO_CLAIM)
        return None
    source_id = None
    if fact.source_url:
        if not is_web_address(fact.source_url):
            _skip(skipped, item, BAD_ADDRESS, saved=True)
        else:
            source = _call(ctx, "save_source", {
                "url": fact.source_url, "title": fact.source_title or fact.source_url[:SOURCE_TITLE_CHARS],
                "publisher": fact.publisher, "published_date": fact.published_date,
            })
            if source.ok:
                source_id = source.data["source_id"]
            else:
                _skip(skipped, item, f"Saved without its source: {source.error['message']}", saved=True)
    saved = _call(ctx, "save_evidence", {"task_id": task_id, "claim": fact.claim, "quote": fact.quote,
                                         "source_id": source_id, "as_of": fact.as_of, "run_id": run_id})
    if not saved.ok:
        _skip(skipped, item, saved.error["message"])
        return None
    if saved.data["status"] != "active":
        _skip(skipped, item, REJECTED)
        return None
    return saved.data["fact_id"]


def record_research_facts(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    if card["research_method"] == "SYNTHESIS":
        raise ToolError("conflict", OPTIONS_REPORT)
    run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    run_id = run["id"] if run is not None else None
    fact_ids, conclusion_ids, skipped = [], [], []
    by_number = {}  # position in this batch (from 1) -> saved, active fact id
    for number, fact in enumerate(inputs.facts, start=1):
        fact_id = _save_fact(ctx, inputs.task_id, run_id, number, fact, skipped)
        if fact_id is not None:
            by_number[number] = fact_id
            if fact_id not in fact_ids:
                fact_ids.append(fact_id)
    for number, conclusion in enumerate(inputs.conclusions, start=1):
        item = f"conclusion {number}"
        if not conclusion.text:
            _skip(skipped, item, NO_TEXT)
            continue
        cited = list(dict.fromkeys(by_number[n] for n in conclusion.fact_numbers if n in by_number))
        if not cited:
            _skip(skipped, item, NO_FACTS_LEFT)
            continue
        made = _call(ctx, "create_finding", {"task_id": inputs.task_id, "text": conclusion.text,
                                             "fact_ids": cited[:MAX_FACTS_PER_CONCLUSION]})
        if made.ok:
            conclusion_ids.append(made.data["conclusion_id"])
        else:
            _skip(skipped, item, made.error["message"])
    return {"fact_ids": fact_ids, "conclusion_ids": conclusion_ids, "skipped": skipped}


register(Tool(
    name="record_research_facts",
    description="Save the key facts (with the pages they came from) and up to 3 conclusions from one finished "
                "research report. Each item is checked on its own; anything unusable is skipped with a reason.",
    input_model=RecordFactsInput, handler=record_research_facts,
    callers=frozenset({"supervisor"}), id_fields=("task_id",),
))
```

In `services/tools/__init__.py` add `research_facts` to the docstring list and the import: `from . import cards, evidence, research_facts, review, runs, state, status  # noqa: F401  (register every tool)`.

Add `"record_research_facts": {"supervisor"},` to `EXPECTED_CALLERS` in `tests/test_tools_structure.py`.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_research_facts.py tests/test_tools_structure.py -v` (expected: all pass). Then run `python -m ruff check services/tools tests/test_tools_research_facts.py tests/test_tools_structure.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add services/tools/research_facts.py services/tools/types.py services/tools/__init__.py tests/test_tools_research_facts.py tests/test_tools_structure.py
git commit -m "Add record_research_facts: save one report's facts and conclusions item by item"
```

---

### Task 4: Automatic fact extraction after every review

**Files:**
- Modify:
  - `services/tools/research_facts.py`: the `extract_research_facts` tool and `can_extract_facts`.
  - `services/supervisor_service.py`: the extraction call, the clean-up, and the hook at the end of `review_card`.
  - `services/board_service.py`: the `facts_recorded` Activity text.
  - Tests: `tests/test_tools_research_facts.py`, `tests/test_supervisor_service.py`, `tests/test_board_service.py`, `tests/test_tools_structure.py`.
- **Not modified:** `services/tools/review.py` (no new fields), `REVIEW_SYSTEM_PROMPT`, the review's `max_tokens=2000`, `services/review_limits.py`.

**Interfaces:**
- Consumes: from Task 3, the tool `record_research_facts` and the names `research_facts.MAX_FACTS`, `MAX_CONCLUSIONS` and `OPTIONS_REPORT`. From Task 1, `research_runs_repo.claim_facts_extraction` and `release_facts_extraction`.
- Produces:
  - In `services.tools.research_facts`:
    - `EXTRACTABLE_STATUSES = ("COMPLETE", "FOLLOW_UP_REQUIRED", "WAITING_FOR_HUMAN")`.
    - `can_extract_facts(card, run) -> bool`.
    - The messages `NOT_EXTRACTABLE` and `ALREADY_EXTRACTED`.
    - Tool `extract_research_facts` (callers: **user, system**; inputs `{task_id}`). It returns the same data as `record_research_facts` and records `user_extract_facts` only when the caller is `user`.
  - In `services.supervisor_service`:
    - Constants `FACTS_INSTRUCTIONS`, `EXTRACT_MENU = ("record_research_facts",)`, `EXTRACT_MAX_TOKENS = 6000`, `MAX_EXTRACT_REPORT_CHARS = 60000`, `EXTRACT_SYSTEM_PROMPT`, `FACT_FIELDS` and `CONCLUSION_FIELDS`.
    - `_clean_facts(raw) -> {"facts": [dict], "conclusions": [dict]}`. It is pure, never raises, keeps only objects, and cuts to 25 and 3.
    - `_facts_summary(data) -> None | {"facts", "conclusions", "skipped"}`.
    - `_record_facts_saved(project_id, card_id, title, summary)`.
    - `extract_facts(project_name, card_id, parent_call_id=None) -> dict`, which raises on any failure.
    - `_extract_after_review(project_name, card, outcome)`. It returns `None` when it does not run (a `FAILED` outcome or a SYNTHESIS card), `{"ok": True, "facts", "conclusions", "skipped"}` on success, or `{"ok": False, "error"}` on failure.
  - `review_card`'s **success** return gains the key `"extraction"`. Every other key, and the failure returns, are unchanged.
  - The decision type `facts_recorded` (detail `{"title", "facts", "conclusions", "skipped"}`) and its Activity text.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_research_facts.py`. Add `import anthropic`, `import httpx` and `from types import SimpleNamespace` at the top, add `agent_decisions_repo` and `research_work_items_repo` to the `db.repositories` import, and add `supervisor_service` to the `services` import.

```python
# ---- extract_research_facts: one paid call that only extracts facts ----

REPORT = "Sources (1):\n- Deakin fees - https://deakin.edu.au/fees\n\nDeakin charges $3,000 per unit in 2026."


class _Block:
    def __init__(self, name, input):
        self.type, self.name, self.input = "tool_use", name, input


class _FakeClient:
    def __init__(self, blocks=(), error=None):
        self.calls, self._blocks, self._error = [], list(blocks), error
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(content=self._blocks)


@pytest.fixture
def app_context(pid):
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        yield flask_app


def _install(monkeypatch, client):
    monkeypatch.setattr(supervisor_service.anthropic, "Anthropic", lambda api_key: client)
    return client


def _finished(pid, status="COMPLETE", method="TARGETED_WEB", phase="4", output=REPORT):
    card = _card(pid, phase=phase, method=method)
    research_work_items_repo.update_fields(card, status=status, completeness_score=0.8, evidence_score=0.7)
    research_runs_repo.create(f"run_{card}", pid, "resp", None, "p")
    research_runs_repo.update(f"run_{card}", research_work_item_id=card, status="completed", output_text=output)
    return card


def _answer(facts=(FACT,), conclusions=()):
    return _Block("record_research_facts", {"facts": list(facts), "conclusions": list(conclusions)})


def _extract(card, caller="user"):
    return tools.run_tool("extract_research_facts", caller, "P", {"task_id": card})


def test_extract_saves_facts_without_touching_the_review(app_context, pid, monkeypatch):
    card = _finished(pid)
    client = _install(monkeypatch, _FakeClient([_answer(conclusions=[{"text": "Deakin is pricey", "fact_numbers": [1]}])]))
    result = _extract(card)
    assert result.ok and len(result.data["fact_ids"]) == 1 and len(result.data["conclusion_ids"]) == 1
    row = research_work_items_repo.get(card)
    assert (row["status"], row["completeness_score"], row["evidence_score"]) == ("COMPLETE", 0.8, 0.7)
    assert research_runs_repo.get(f"run_{card}")["facts_extracted_at"]
    call = client.calls[0]
    assert [t["name"] for t in call["tools"]] == ["record_research_facts"]
    assert call["tool_choice"] == {"type": "tool", "name": "record_research_facts"}
    assert call["max_tokens"] == 6000
    assert "Deakin charges $3,000 per unit in 2026." in call["messages"][0]["content"]
    child = next(r for r in tool_calls_repo.list_for_project(pid) if r["tool"] == "record_research_facts")
    assert (child["caller"], child["parent_call_id"]) == ("supervisor", result.call_id)
    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid)[:2]] == [
        "user_extract_facts", "facts_recorded"]


def test_the_system_extracts_after_a_review_without_a_you_line(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(card, caller="system").ok
    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid)] == ["facts_recorded"]


def test_a_second_click_does_not_call_claude_again(app_context, pid, monkeypatch):
    card = _finished(pid)
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(card).ok
    second = _extract(card)
    assert second.error["code"] == "conflict" and len(client.calls) == 1


def test_a_click_while_extracting_is_refused_without_a_call(app_context, pid, monkeypatch):
    card = _finished(pid)
    research_runs_repo.claim_facts_extraction(f"run_{card}")  # the automatic step or another tab is extracting now
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(card).error["code"] == "conflict" and client.calls == []


def test_a_failed_claude_call_clears_the_claim(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient(error=anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://example.invalid"))))
    assert _extract(card).error["code"] == "unavailable"
    assert research_runs_repo.get(f"run_{card}")["facts_extracted_at"] is None
    assert evidence_repo.list_facts(pid) == []


def test_an_answer_with_no_usable_facts_leaves_the_button(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_answer(facts=[{"claim": ""}])]))
    result = _extract(card)
    assert result.ok and result.data["fact_ids"] == []
    assert research_runs_repo.get(f"run_{card}")["facts_extracted_at"] is None


def test_a_garbled_answer_is_cleaned_before_saving(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_Block("record_research_facts", {
        "facts": '<parameter name="facts">[{"claim": "Deakin charges $3,000 per unit"}, {"claim": "cut',
        "task_id": 99999})]))
    result = _extract(card)
    assert result.ok and [f["claim"] for f in evidence_repo.list_facts(pid)] == ["Deakin charges $3,000 per unit"]
    assert evidence_repo.list_facts(pid)[0]["research_work_item_id"] == card  # the model cannot pick the research


@pytest.mark.parametrize("make_card", [
    lambda pid: _finished(pid, status="RUNNING"),
    lambda pid: _finished(pid, method="SYNTHESIS", phase="7"),
    lambda pid: _finished(pid, output=""),
    lambda pid: _card(pid),
])
def test_extract_needs_a_finished_research_with_a_report(app_context, pid, monkeypatch, make_card):
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(make_card(pid)).error["code"] == "conflict" and client.calls == []


def test_the_supervisor_cannot_start_an_extraction(app_context, pid, monkeypatch):
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(_finished(pid), caller="supervisor").error["code"] == "not_allowed" and client.calls == []
```

Append to `tests/test_supervisor_service.py`. Add `import anthropic` and `import httpx` at the top, add `evidence_repo` to the `db.repositories` import, and add `board_service` to the `services` import.

```python
# ---- automatic fact extraction after the review ----

class _ScriptedClient:
    """Answers each Claude call with the next item, in order: the review first, then the extraction."""

    def __init__(self, *answers):
        self.calls, self._answers = [], list(answers)
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=[answer])


EXTRACTED = [{"claim": "Deakin charges $3,000 per unit", "source_url": "https://a.example", "source_title": "A",
              "as_of": "2026"},
             {"claim": "Monash runs three intakes"}]


def _facts_answer(facts=EXTRACTED, conclusions=({"text": "Fees and intakes differ", "fact_numbers": [1, 2]},)):
    return _Block("record_research_facts", {"facts": facts, "conclusions": list(conclusions)})


def test_a_review_is_followed_by_one_extraction_that_reads_the_whole_report(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid, output="Sources (1):\n- A - https://a.example\n\n" + "y" * 20000 + " LATE DETAIL")
    client = _install(monkeypatch, _ScriptedClient(_review("COMPLETE"), _facts_answer()))
    result = supervisor_service.review_card("P", card_id)
    assert result["reviewed"] is True and result["new_status"] == "COMPLETE"
    assert result["extraction"] == {"ok": True, "facts": 2, "conclusions": 1, "skipped": 0}
    review_call, extract_call = client.calls
    # The review call is exactly as before.
    assert [t["name"] for t in review_call["tools"]] == ["evaluate_research_output"]
    assert review_call["max_tokens"] == 2000
    assert "facts" not in review_call["tools"][0]["input_schema"]["properties"]
    assert "LATE DETAIL" not in review_call["messages"][0]["content"]
    # The extraction call reads the rest of the report.
    assert [t["name"] for t in extract_call["tools"]] == ["record_research_facts"]
    assert extract_call["tool_choice"] == {"type": "tool", "name": "record_research_facts"}
    assert extract_call["max_tokens"] == 6000
    assert "LATE DETAIL" in extract_call["messages"][0]["content"]
    assert len(evidence_repo.list_facts(pid)) == 2
    assert research_runs_repo.get(f"run_{card_id}")["facts_extracted_at"]
    # No "You extracted facts" line for the automatic step.
    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid)] == ["facts_recorded", "review_outcome"]
    texts = [a["text"] for a in board_service.get_board_state("P")["activity"]]
    assert texts[0] == 'Supervisor saved 2 facts and 1 conclusion from "Fees".'


def test_a_failed_review_triggers_no_extraction(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    client = _install(monkeypatch, _ScriptedClient(_review("FAILED")))
    result = supervisor_service.review_card("P", card_id)
    assert result["reviewed"] is True and result["extraction"] is None and len(client.calls) == 1
    assert research_runs_repo.get(f"run_{card_id}")["facts_extracted_at"] is None


def test_the_options_report_is_never_mined(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    research_work_items_repo.update_fields(card_id, research_method="SYNTHESIS")
    client = _install(monkeypatch, _ScriptedClient(_review("COMPLETE")))
    assert supervisor_service.review_card("P", card_id)["extraction"] is None and len(client.calls) == 1


@pytest.mark.parametrize("second_answer,ok", [
    (anthropic.APIConnectionError(request=httpx.Request("POST", "https://example.invalid")), False),
    (SimpleNamespace(type="text", text="Here are some facts."), False),
    (_Block("record_research_facts", {"facts": [{"claim": ""}, "not a fact"]}), True),
])
def test_an_extraction_that_fails_or_finds_nothing_leaves_the_review_exactly_as_it_was(
        temp_db, app_context, drafted, monkeypatch, second_answer, ok):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    _install(monkeypatch, _ScriptedClient(_review("FOLLOW_UP_REQUIRED", followup={"title": "Verify intakes"}), second_answer))
    result = supervisor_service.review_card("P", card_id)
    assert result["reviewed"] is True and result["extraction"]["ok"] is ok
    row = research_work_items_repo.get(card_id)
    assert (row["status"], row["completeness_score"], row["evidence_score"]) == ("FOLLOW_UP_REQUIRED", 0.8, 0.7)
    assert json.loads(row["identified_gaps_json"]) == ["No intake dates"]
    assert research_work_items_repo.get(result["followup_task_id"])["title"] == "Verify intakes"
    assert evidence_repo.list_facts(pid) == []
    assert research_runs_repo.get(f"run_{card_id}")["facts_extracted_at"] is None  # "Extract facts" stays available


def test_a_review_of_a_report_already_being_mined_makes_no_second_call(temp_db, app_context, monkeypatch):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    research_runs_repo.claim_facts_extraction(f"run_{card_id}")  # a click on "Extract facts" got there first
    client = _install(monkeypatch, _ScriptedClient(_review("COMPLETE")))
    result = supervisor_service.review_card("P", card_id)
    assert result["reviewed"] is True and research_work_items_repo.get(card_id)["status"] == "COMPLETE"
    assert len(client.calls) == 1 and result["extraction"]["ok"] is False


@pytest.mark.parametrize("facts_field,claims", [
    ('<parameter name="facts">[{"claim": "Deakin charges $3,000 per unit"}, {"claim": "cut sh',
     ["Deakin charges $3,000 per unit"]),
    ('[{"claim": "Deakin charges $3,000 per unit"}]', ["Deakin charges $3,000 per unit"]),
    ({"claim": "Deakin charges $3,000 per unit"}, ["Deakin charges $3,000 per unit"]),
    ("\n- A bulleted fact\n- Another", []),
])
def test_jumbled_extraction_answers_from_the_live_model_are_recovered(temp_db, app_context, monkeypatch,
                                                                     facts_field, claims):
    pid = projects_repo.get_or_create_id("P")
    card_id = _running_card(pid)
    _install(monkeypatch, _ScriptedClient(_review("COMPLETE"), _Block("record_research_facts", {"facts": facts_field})))
    assert supervisor_service.review_card("P", card_id)["reviewed"] is True
    assert research_work_items_repo.get(card_id)["status"] == "COMPLETE"
    assert [f["claim"] for f in evidence_repo.list_facts(pid)] == claims


@pytest.mark.parametrize("raw,claims", [
    ({"facts": [{"claim": "A"}, "not an object", 3]}, ["A"]),
    ({"facts": {"claim": "A"}}, ["A"]),
    ({"facts": '[{"claim": "A"}, {"claim": "B"}]'}, ["A", "B"]),
    ({"facts": '<parameter name="facts">[{"claim": "A"}, {"claim": "B", "source_url": "https://b.example"}, '
               '{"claim": "cut sh'}, ["A", "B"]),
    ({"facts": "\n- A bulleted fact\n- Another"}, []),
    ({"facts": None}, []),
    ({}, []),
    ("not even a dict", []),
])
def test_clean_facts_recovers_what_it_can(raw, claims):
    assert [f["claim"] for f in supervisor_service._clean_facts(raw)["facts"]] == claims


def test_clean_facts_rebuilds_one_fact_whose_fields_spilled_out():
    raw = {"facts": '\n<parameter name="claim">Deakin charges $3,000 per unit.',
           "source_url": "https://deakin.edu.au/fees", "as_of": "2026"}
    assert supervisor_service._clean_facts(raw)["facts"] == [
        {"claim": "Deakin charges $3,000 per unit.", "source_url": "https://deakin.edu.au/fees", "as_of": "2026"}]


def test_clean_facts_lifts_fields_a_garbled_string_swallowed():
    swallowed = {"facts": '[{"claim": "A"}]</parameter>\n<parameter name="conclusions">[{"text": "C", "fact_numbers": [1]}]'}
    assert supervisor_service._clean_facts(swallowed) == {"facts": [{"claim": "A"}],
                                                          "conclusions": [{"text": "C", "fact_numbers": [1]}]}
    one_conclusion = {"conclusions": '\n<parameter name="text">Fees differ', "fact_numbers": "1, 2"}
    assert supervisor_service._clean_facts(one_conclusion)["conclusions"] == [{"text": "Fees differ", "fact_numbers": "1, 2"}]


def test_clean_facts_cuts_to_the_batch_limits():
    cleaned = supervisor_service._clean_facts({"facts": [{"claim": str(i)} for i in range(30)],
                                              "conclusions": [{"text": str(i)} for i in range(5)]})
    assert (len(cleaned["facts"]), len(cleaned["conclusions"])) == (25, 3)
```

Note for the implementer: existing review tests use `_FakeClient`, which returns the review block for *every* call. In those tests the automatic extraction gets no `record_research_facts` block and fails quietly. That is expected, and they keep passing unchanged (`review_outcome` is still the newest decision, because a failed extraction records nothing).

Append to `tests/test_board_service.py`:

```python
@pytest.mark.parametrize("detail,expected", [
    ({"facts": 18, "conclusions": 2, "skipped": 0}, 'Supervisor saved 18 facts and 2 conclusions from "Fees".'),
    ({"facts": 1, "conclusions": 0, "skipped": 3}, 'Supervisor saved 1 fact and 0 conclusions from "Fees" (3 skipped).'),
    ({"facts": 0, "conclusions": 0, "skipped": 2}, 'Supervisor found no facts it could save in "Fees" (2 skipped).'),
])
def test_activity_reports_saved_facts(pid, detail, expected):
    card_id = _card(pid)
    agent_decisions_repo.record(pid, "facts_recorded", json.dumps(detail), research_work_item_id=card_id)
    assert board_service.get_board_state("P")["activity"][0]["text"] == expected
```

Add `"extract_research_facts": {"user", "system"},` to `EXPECTED_CALLERS` in `tests/test_tools_structure.py`.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_research_facts.py tests/test_supervisor_service.py tests/test_board_service.py tests/test_tools_structure.py -v`
Expected: the new tests FAIL (no `extract_research_facts` tool, no `extraction` key, no `_clean_facts`, generic Activity text). Existing tests still pass.

- [ ] **Step 3: Implement**

**`services/tools/research_facts.py`.** Add `from .activity import record_user_action` to the imports, and append above the existing `register(...)` call:

```python
EXTRACTABLE_STATUSES = ("COMPLETE", "FOLLOW_UP_REQUIRED", "WAITING_FOR_HUMAN")
NOT_EXTRACTABLE = "Facts can only be taken from a finished research that has a report."
ALREADY_EXTRACTED = "Facts are already being taken from this report, or have been."


def can_extract_facts(card, run):
    """Whether the "Extract facts" button shows for this card and its latest run."""
    return (card["status"] in EXTRACTABLE_STATUSES and card["research_method"] != "SYNTHESIS"
            and run is not None and bool(run["output_text"]) and not run["facts_extracted_at"])


class ExtractInput(ToolInput):
    task_id: CardId


def extract_research_facts(ctx, inputs):
    from .. import supervisor_service  # imported here: supervisor_service imports this package

    card = research_work_items_repo.get(inputs.task_id)
    run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    if card["research_method"] == "SYNTHESIS":
        raise ToolError("conflict", OPTIONS_REPORT)
    if card["status"] not in EXTRACTABLE_STATUSES or run is None or not run["output_text"]:
        raise ToolError("conflict", NOT_EXTRACTABLE)
    if not research_runs_repo.claim_facts_extraction(run["id"]):  # one paid call per report, whoever asks
        raise ToolError("conflict", ALREADY_EXTRACTED)
    try:
        data = supervisor_service.extract_facts(ctx.project_name, inputs.task_id, parent_call_id=ctx.call_id)
    except Exception:
        research_runs_repo.release_facts_extraction(run["id"])  # nothing was taken: the person can try again
        raise
    if not data["fact_ids"]:
        research_runs_repo.release_facts_extraction(run["id"])
    if ctx.caller == "user":  # the automatic step after a review has no "You extracted facts" line
        record_user_action(ctx, "user_extract_facts", inputs.task_id,
                           facts=len(data["fact_ids"]), conclusions=len(data["conclusion_ids"]))
    return data
```

Append after the existing `register(...)`:

```python
register(Tool(
    name="extract_research_facts",
    description="Take the facts and conclusions from a finished research's report with one paid Claude call. "
                "Started by the system right after a review, or by the person's Extract facts button.",
    input_model=ExtractInput, handler=extract_research_facts,
    callers=frozenset({"user", "system"}), id_fields=("task_id",),
))
```

**`services/supervisor_service.py`.** The review prompt, the review tool and `max_tokens=2000` stay as they are.

1. Add `from .tools import research_facts as facts_tool` next to the other `.tools` imports. Run `ruff check --fix` on the file if it reorders them.
2. Next to `DRAFTING_MENU` / `REVIEW_MENU`, add:

```python
EXTRACT_MENU = ("record_research_facts",)
EXTRACT_MAX_TOKENS = 6000  # room for up to 25 facts and 3 conclusions
# The extraction reads far more of the report than the review may (the review's view is set in review_limits.py).
MAX_EXTRACT_REPORT_CHARS = 60000

FACTS_INSTRUCTIONS = (
    "Record the report's key facts in facts (at most 25): one short, checkable claim each, with the address "
    "(source_url) and title (source_title) of the page it came from as listed in the report's sources, a short "
    "quote if the report gives one, and the date or year the claim applies to (as_of). Leave the source empty if "
    "the report does not say where a claim came from; never invent one. Then write up to 3 conclusions for this "
    "phase in conclusions, each citing the facts it rests on by their positions in your facts list "
    "(fact_numbers, counting from 1)."
)

EXTRACT_SYSTEM_PROMPT = (
    "You are the research supervisor taking facts from ONE finished research report for an Australian "
    "higher-education competitive-intelligence project. Do not score or judge the research. Use only what the "
    "report says. " + FACTS_INSTRUCTIONS
)
```

3. After `_unwrap`, add the clean-up and the decision helpers:

```python
FACT_FIELDS = ("claim", "quote", "source_url", "source_title", "publisher", "published_date", "as_of")
CONCLUSION_FIELDS = ("text", "fact_numbers")
_EMPTY = (None, "", [])
_DECODER = json.JSONDecoder()


def _complete_objects(text):
    """The complete JSON objects in a list that was cut short (or wrapped in stray text)."""
    found, start = [], text.find("{")
    while start != -1:
        try:
            value, end = _DECODER.raw_decode(text, start)
        except ValueError:
            break  # the list was cut short inside this object
        if isinstance(value, dict):
            found.append(value)
        start = text.find("{", end)
    return found


def _lift_swallowed(cleaned, names):
    """A garbled string can swallow the fields that came after it; lift those back to the top level."""
    for value in list(cleaned.values()):
        if isinstance(value, str) and "<parameter name=" in value:
            pieces = dict(_LEAKED_PARAMETER.findall(value))
            for name in names:
                if name in pieces and cleaned.get(name) in _EMPTY:
                    cleaned[name] = pieces[name]


def _object_list(cleaned, name, fields):
    """A list of objects from a field the model may have garbled: a JSON string (possibly cut short or wrapped in
    a stray <parameter> tag), one object, or one object whose fields arrived as tags and spilled to the top level."""
    value = cleaned.get(name)
    if isinstance(value, str):
        pieces = dict(_LEAKED_PARAMETER.findall(value))
        if fields[0] in pieces:  # one object, its fields as stray tags; the rest spilled to the top level
            item = {key: _leaked_value(text) for key, text in pieces.items() if key in fields}
            for key in fields:
                stray = cleaned.pop(key, None)
                if item.get(key) in _EMPTY and stray not in _EMPTY:
                    item[key] = stray
            return [item]
        head = value.split('<parameter name="', 1)[0].replace("</parameter>", "").strip()
        text = pieces.get(name) or head or (next(iter(pieces.values())) if len(pieces) == 1 else "")
        try:
            value = json.loads(text)
        except ValueError:
            value = _complete_objects(text)
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clean_facts(raw):
    """The facts and conclusions in the extraction answer, recovered from garbling and cut to the batch limits. Items
    that are not objects are dropped; record_research_facts then checks every remaining item on its own."""
    cleaned = dict(raw) if isinstance(raw, dict) else {}
    _lift_swallowed(cleaned, ("facts", "conclusions"))
    return {
        "facts": _object_list(cleaned, "facts", FACT_FIELDS)[:facts_tool.MAX_FACTS],
        "conclusions": _object_list(cleaned, "conclusions", CONCLUSION_FIELDS)[:facts_tool.MAX_CONCLUSIONS],
    }


def _facts_summary(data):
    """Counts from record_research_facts' result, or None when no facts were recorded."""
    if not isinstance(data, dict) or "fact_ids" not in data:
        return None
    return {"facts": len(data["fact_ids"]), "conclusions": len(data["conclusion_ids"]),
            "skipped": sum(1 for s in data["skipped"] if not s.get("saved"))}


def _record_facts_saved(project_id, card_id, title, summary):
    if summary and any(summary.values()):
        agent_decisions_repo.record(project_id, decision_type="facts_recorded",
                                    detail=json.dumps({"title": title, **summary}), research_work_item_id=card_id)
```

4. At the end of the module, add the extraction call and the step after the review:

```python
def _extract_context(card, run):
    phase_title = PHASE_DEFINITIONS.get(card["phase_key"], {}).get("title", card["phase_key"])
    report = _clip_run_output(run["output_text"] or "", MAX_EXTRACT_REPORT_CHARS)
    return (f"# RESEARCH\n\nPhase {card['phase_key']}: {phase_title}\nTitle: {card['title']}\n\n"
            f"# REPORT\n\n{report}")


def extract_facts(project_name, card_id, parent_call_id=None):
    """One paid Claude call that only extracts facts (after a review, or from the Extract facts button).
    extract_research_facts has already claimed the run; this raises on any failure so the tool can release it."""
    card = research_work_items_repo.get(card_id)
    run = research_runs_repo.find_latest_for_work_item(card_id)
    response = _client().messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=EXTRACT_MAX_TOKENS,
        system=EXTRACT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _extract_context(card, run)}],
        tools=claude_tools(EXTRACT_MENU),
        tool_choice={"type": "tool", "name": "record_research_facts"},
    )
    block = _tool_use(response, "record_research_facts")
    if block is None:
        raise RuntimeError("The Supervisor did not return any facts.")
    inputs = {**_clean_facts(block.input), "task_id": card_id}  # the system, not the model, picks the research
    result = run_tool("record_research_facts", "supervisor", project_name, inputs,
                      parent_call_id=parent_call_id, menu=EXTRACT_MENU)
    if not result.ok:
        raise RuntimeError(result.error["message"])
    _record_facts_saved(card["project_id"], card_id, card["title"], _facts_summary(result.data))
    return result.data


def _extract_after_review(project_name, card, outcome):
    """One extraction call on a card that was just reviewed. It never affects the review: a failure is logged (by
    run_tool, and printed here) and only reported in the returned summary."""
    if outcome == "FAILED" or card["research_method"] == "SYNTHESIS":
        return None
    try:
        result = run_tool("extract_research_facts", "system", project_name, {"task_id": card["id"]})
        if result.ok:
            return {"ok": True, **_facts_summary(result.data)}
        message = result.error["message"]
    except Exception as exc:  # run_tool does not raise; this only guarantees the review is never affected
        message = f"{type(exc).__name__}: {exc}"
    print(f"[supervisor] facts were not extracted from research {card['id']}: {message}")
    return {"ok": False, "error": message}
```

5. In `review_card`, keep everything as it is (including the `review_outcome` decision) and change only the final line to:

```python
    # Facts come from a second call that reads much more of the report than the review may (review_limits.py).
    # Whatever happens there, the review above stands.
    return {"reviewed": True, **result.data,
            "extraction": _extract_after_review(project_name, card, result.data["outcome"])}
```

(`card` is the row `review_card` already loads at its start. The extraction runs after the `review_outcome` decision is recorded, so `facts_recorded` is the newer line.)

**`services/board_service.py`.** Add the helpers below `_focus`:

```python
def _count(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
```

In `_activity_item`, add before the final `else:`:

```python
    elif dtype == "facts_recorded":
        facts, conclusions, skipped = (_as_int(detail.get(k)) for k in ("facts", "conclusions", "skipped"))
        extra = f" ({skipped} skipped)" if skipped else ""
        if facts or conclusions:
            text = (f"Supervisor saved {_count(facts, 'fact', 'facts')} and "
                    f"{_count(conclusions, 'conclusion', 'conclusions')} from \"{title}\"{extra}.")
        else:
            text = f'Supervisor found no facts it could save in "{title}"{extra}.'
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_research_facts.py tests/test_supervisor_service.py tests/test_board_service.py tests/test_tools_review.py tests/test_tools_structure.py -v`. Expected: all pass, including every existing review test (the jumbled follow-up tests from c42f1c6 are untouched). Then:
- `python -m ruff check services/tools/research_facts.py services/supervisor_service.py services/board_service.py tests/test_tools_research_facts.py tests/test_supervisor_service.py tests/test_board_service.py tests/test_tools_structure.py`
- `python -m pytest tests/ -q`
- Confirm `git diff --stat services/tools/review.py services/review_limits.py` is empty.

- [ ] **Step 5: Commit**

```bash
git add services/tools/research_facts.py services/supervisor_service.py services/board_service.py tests/test_tools_research_facts.py tests/test_supervisor_service.py tests/test_board_service.py tests/test_tools_structure.py
git commit -m "Extract facts automatically after each review, in a second call that reads the full report"
```

---

### Task 5: The "Extract facts" button

**Files:**
- Modify:
  - `services/board_service.py`: card state, `get_report`, `extract_facts`, `extract_note` and Activity text.
  - `routes/board.py`: the route.
  - `static/board.js`: card button and report reader.
  - `templates/index.html`: `board.js?v=4`.
  - Tests: `tests/test_board_service.py`, `tests/test_routes_board.py`, `tests/js/test_board_view.js`.

**Interfaces:**
- Consumes: from Task 4, the tool `extract_research_facts` (callers user and system; the button calls it as `user`), plus `research_facts.can_extract_facts(card, run)` and `board_service._count`.
- Produces:
  - In `board_service`: board cards gain `"can_extract_facts": bool`, and `get_report` gains `"id"` and `"can_extract_facts"`. New functions `extract_facts(project_name, card_id) -> dict` and `extract_note(result) -> str`, plus the message `EXTRACT_FAILED` and the `_USER_TEXT` entry for `user_extract_facts`.
  - Route `POST /api/board/cards/<id>/extract-facts` returns `{"success", "board", "result", "note"}`.
  - In `board.js`: the view gains `confirmExtract` (a card id or `null`) and `extracting` (`{id: true}`). New exported `extractHtml(card, view)`, the constant `EXTRACT_CONFIRM`, and the actions `extract-open`, `extract-cancel` and `extract-start`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board_service.py`:

```python
def _finished(pid, status="COMPLETE", method="TARGETED_WEB", phase="4", output="The report."):
    card_id = _card(pid, status=status, method=method, phase=phase)
    research_runs_repo.create(f"run_{card_id}", pid, None, None, "p")
    research_runs_repo.update(f"run_{card_id}", research_work_item_id=card_id, status="completed", output_text=output)
    return card_id


def _can_extract(card_id):
    return next(c for c in board_service.get_board_state("P")["cards"] if c["id"] == card_id)["can_extract_facts"]


@pytest.mark.parametrize("status", ["COMPLETE", "FOLLOW_UP_REQUIRED", "WAITING_FOR_HUMAN"])
def test_finished_research_offers_extract_facts_until_its_facts_are_taken(pid, status):
    card_id = _finished(pid, status=status)
    assert _can_extract(card_id) is True
    research_runs_repo.claim_facts_extraction(f"run_{card_id}")
    assert _can_extract(card_id) is False


def test_no_extract_facts_without_a_report_or_for_the_options_report(pid):
    cards = [_finished(pid, status="RUNNING"), _card(pid, status="COMPLETE"), _finished(pid, output=""),
             _finished(pid, method="SYNTHESIS", phase="7")]
    assert [_can_extract(c) for c in cards] == [False, False, False, False]


def test_the_report_reader_knows_whether_facts_can_be_extracted(pid):
    card_id = _finished(pid)
    report = board_service.get_report("P", card_id)
    assert (report["id"], report["can_extract_facts"]) == (card_id, True)


def test_extract_note():
    assert board_service.extract_note({"fact_ids": [1, 2], "conclusion_ids": [3], "skipped": []}) == (
        "Saved 2 facts and 1 conclusion. You'll find them on the Insights tab, under this research's phase.")
    assert board_service.extract_note({"fact_ids": [], "conclusion_ids": [], "skipped": []}) == (
        "No facts could be taken from this report, so nothing was saved. You can try again.")


def test_activity_says_you_extracted_facts(pid):
    card_id = _card(pid)
    agent_decisions_repo.record(pid, "user_extract_facts", json.dumps({"facts": 2}), research_work_item_id=card_id)
    assert board_service.get_board_state("P")["activity"][0]["text"] == 'You extracted facts from "Fees".'
```

Append to `tests/test_routes_board.py`:

```python
def test_extract_facts_saves_facts_and_explains_itself(client, monkeypatch):
    card_id = _card(status="COMPLETE")
    pid = projects_repo.get_or_create_id("P")
    research_runs_repo.create(f"run_{card_id}", pid, None, None, "p")
    research_runs_repo.update(f"run_{card_id}", research_work_item_id=card_id, status="completed",
                              output_text="Deakin charges $3,000 per unit.")
    block = _Block("record_research_facts", {"facts": [{"claim": "Deakin charges $3,000 per unit",
                                                        "source_url": "https://deakin.example/fees"}]})
    calls = []
    fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: calls.append(kw) or SimpleNamespace(content=[block])))
    monkeypatch.setattr(supervisor_service.anthropic, "Anthropic", lambda api_key: fake)
    resp = client.post(f"/api/board/cards/{card_id}/extract-facts", json={"project": "P"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["note"].startswith("Saved 1 fact and 0 conclusions.")
    card = next(c for c in body["board"]["cards"] if c["id"] == card_id)
    assert (card["can_extract_facts"], card["status"]) == (False, "COMPLETE")
    again = client.post(f"/api/board/cards/{card_id}/extract-facts", json={"project": "P"})
    assert again.status_code == 409 and len(calls) == 1


def test_extract_facts_on_a_card_without_a_report_is_409(client):
    assert client.post(f"/api/board/cards/{_card(status='COMPLETE')}/extract-facts", json={"project": "P"}).status_code == 409
```

In `tests/js/test_board_view.js`, add `can_extract_facts: false,` to the defaults in `card()`, and add these tests before the runner:

```js
test('a finished card with no facts yet offers Extract facts, warning that it is a paid call', () => {
    const c = card({ status: 'COMPLETE', can_extract_facts: true, run: { has_report: true } });
    assert(B.cardHtml(c, state([]), view()).includes('data-act="extract-open"'));
    const v = view(); v.confirmExtract = 7;
    const html = B.cardHtml(c, state([]), v);
    assert(html.includes('one paid Claude call') && html.includes('data-act="extract-start"') && html.includes('data-act="extract-cancel"'));
    v.extracting[7] = true;
    const busy = B.cardHtml(c, state([]), v);
    assert(busy.includes('Extracting facts') && !busy.includes('data-act="extract-start"'));
});
test('no Extract facts once the facts were taken', () => {
    const html = B.cardHtml(card({ status: 'COMPLETE', can_extract_facts: false, run: { has_report: true } }), state([]), view());
    assert(!html.includes('extract-open'));
});
test('a card waiting for your review can extract facts too', () => {
    assert(B.cardHtml(card({ status: 'WAITING_FOR_HUMAN', can_extract_facts: true }), state([]), view()).includes('data-act="extract-open"'));
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_board_service.py tests/test_routes_board.py -v` and `node tests/js/test_board_view.js`
Expected: the new tests FAIL (no `can_extract_facts`, no route, no button).

- [ ] **Step 3: Implement**

**`services/board_service.py`:**

1. Add `from .tools.research_facts import can_extract_facts` to the imports.
2. In `_card_state`, add `"can_extract_facts": can_extract_facts(card, run_row),` after the `"run"` entry.
3. In `get_report`, return `{"id": card["id"], "title": card["title"], "text": run_row["output_text"], "filename": filename, "can_extract_facts": can_extract_facts(card, run_row)}`.
4. Add `"user_extract_facts": 'You extracted facts from "{title}".',` to `_USER_TEXT`.
5. Add these functions after `mark_failed`, outside `ACTIONS`, because the route returns a note:

```python
EXTRACT_FAILED = "Couldn't take facts from this report just now. Try again shortly."


def extract_facts(project_name, card_id):
    """The "Extract facts" button. One paid Claude call; the tool makes sure a report is only paid for once."""
    try:
        return _user_tool(project_name, "extract_research_facts", {"task_id": card_id})
    except (CardNotFound, ValueError):  # BoardStateError is a ValueError: the card's state or an earlier extraction
        raise
    except DraftingUnavailable as exc:
        raise DraftingUnavailable(EXTRACT_FAILED) from exc
    except RuntimeError as exc:
        raise RuntimeError(EXTRACT_FAILED) from exc


def extract_note(result):
    facts, conclusions = len(result["fact_ids"]), len(result["conclusion_ids"])
    if not facts:
        return "No facts could be taken from this report, so nothing was saved. You can try again."
    return (f"Saved {_count(facts, 'fact', 'facts')} and {_count(conclusions, 'conclusion', 'conclusions')}. "
            "You'll find them on the Insights tab, under this research's phase.")
```

**`routes/board.py`.** Add this above `card_action` (Werkzeug matches the literal segment before `<action>`):

```python
@board_bp.route("/api/board/cards/<int:card_id>/extract-facts", methods=["POST"])
def extract_facts(card_id):
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        result = board_service.extract_facts(project, card_id)
        return _ok(project, result=result, note=board_service.extract_note(result))
    except Exception as exc:
        return _error(exc)
```

**`static/board.js`:**

1. In `newView()`, add `confirmExtract: null, extracting: {}` to the returned object.
2. After `reportButton`, add:

```js
    const EXTRACT_CONFIRM = 'Take the facts from this report? This makes one paid Claude call.';
    function extractHtml(card, view) {
        if (!card.can_extract_facts) return '';
        const id = card.id;
        if (view.extracting && view.extracting[id]) return '<div class="rb-row"><button type="button" class="rb-btn" disabled>Extracting facts…</button></div>';
        if (view.confirmExtract === id) {
            return `<div class="rb-confirm" role="group" aria-label="Confirm extract facts"><p class="rb-small">${EXTRACT_CONFIRM}</p>`
                + `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="extract-start" data-id="${id}">Extract facts</button>`
                + `<button type="button" class="rb-btn" data-act="extract-cancel" data-id="${id}">Not yet</button></div></div>`;
        }
        return `<div class="rb-row"><button type="button" class="rb-btn" data-act="extract-open" data-id="${id}">Extract facts</button><span class="rb-hint">Saves this report's facts to Insights.</span></div>`;
    }
```

3. Change `reviewHtml(card)` to `reviewHtml(card, view)`, ending in `...${file}<div class="rb-row">${reportButton(card)}</div>${extractHtml(card, view)}</div>`. Update its three callers in `cardBody` to `reviewHtml(card, view)`.
4. Add `extractHtml` and `EXTRACT_CONFIRM` to the `pure` export object.
5. In `ACTIONS`, add:

```js
        'extract-open'(el) { board.view.confirmExtract = Number(el.dataset.id); render(); },
        'extract-cancel'() { board.view.confirmExtract = null; render(); },
        'extract-start'(el) {
            const id = Number(el.dataset.id);
            board.view.confirmExtract = null;
            attempt(id, async project => {
                board.view.extracting[id] = true;
                render();
                let data;
                try { data = await cardAction(project, id, 'extract-facts'); }
                finally { delete board.view.extracting[id]; }
                if (project !== board.project) return;
                board.view.message = data.note || '';
                takeState(data);
            });
        },
```

6. In `showReader(report)`, right before `document.body.appendChild(overlay);`, add the reader's button. It is built as DOM, and the note is set as text:

```js
        if (report.can_extract_facts) {
            const head = overlay.querySelector('.rb-reader-head');
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'rb-btn';
            button.textContent = 'Extract facts';
            button.addEventListener('click', async () => {
                if (!window.confirm(EXTRACT_CONFIRM)) return;
                const project = board.project;
                button.disabled = true;
                button.textContent = 'Extracting facts…';
                try {
                    const data = await cardAction(project, report.id, 'extract-facts');
                    const note = document.createElement('span');
                    note.className = 'rb-small';
                    note.textContent = data.note || 'Done.';
                    button.replaceWith(note);
                    if (project === board.project) { board.view.message = data.note || ''; takeState(data); }
                } catch (err) {
                    button.disabled = false;
                    button.textContent = 'Extract facts';
                    const message = document.createElement('span');
                    message.className = 'rb-err';
                    message.textContent = err.message;
                    button.after(message);
                }
            });
            head.insertBefore(button, head.querySelector('[data-reader-close]'));
        }
```

**`templates/index.html`.** Change `board.js?v=3` to `board.js?v=4`.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_board_service.py tests/test_routes_board.py -v` and `node tests/js/test_board_view.js` (expected: all 23 passed). Then run `python -m ruff check services/board_service.py routes/board.py tests/test_board_service.py tests/test_routes_board.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add services/board_service.py routes/board.py static/board.js templates/index.html tests/test_board_service.py tests/test_routes_board.py tests/js/test_board_view.js
git commit -m "Add the Extract facts button for finished research"
```

---

### Task 6: Known facts in the drafting briefing

**Files:**
- Modify: `services/tools/state.py` (`known_facts`), `services/supervisor_service.py` (`format_briefing`, `DRAFTING_SYSTEM_PROMPT`, `MAX_KNOWN_FACTS_CHARS`), `tests/test_tools_state.py`, `tests/test_supervisor_service.py`

**Interfaces:**
- Consumes: Task 1's `evidence_repo.known_facts(project_id, per_phase=10)`.
- Produces:
  - `get_project_state`'s data gains `"known_facts": {phase_key: {"count": int, "recent": [str]}}`. It covers active facts only, with up to 10 newest claims per phase, each clipped by `clip_text(..., 200)`.
  - `supervisor_service.MAX_KNOWN_FACTS_CHARS = 4000`.
  - `format_briefing` gains a `# KNOWN FACTS` section between `# EXISTING PHASE FINDINGS` and `# RESEARCH TASKS`. It reads `state.get("known_facts")`, so states without the key still work.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_state.py`, adding `evidence_repo` to its `db.repositories` import:

```python
def test_project_state_lists_known_facts_per_phase(pid):
    card4 = research_work_items_repo.create(pid, "4", "Fees")
    card2 = research_work_items_repo.create(pid, "2", "Students")
    evidence_repo.get_or_create_fact(pid, "4", card4, "Older fee fact", "older fee fact")
    evidence_repo.get_or_create_fact(pid, "4", card4, "x" * 400, "x" * 400)
    gone, _ = evidence_repo.get_or_create_fact(pid, "2", card2, "Rejected", "rejected")
    evidence_repo.set_fact_status(gone, "rejected")
    known = tools.run_tool("get_project_state", "system", "P", {}).data["known_facts"]
    assert set(known) == {"4"}
    assert known["4"]["count"] == 2
    assert known["4"]["recent"] == ["x" * 200 + "...[truncated]", "Older fee fact"]
```

Append to `tests/test_supervisor_service.py`:

```python
# ---- known facts in the drafting briefing ----

def _known(pid, phase, claims):
    card = research_work_items_repo.create(pid, phase, f"Card {phase}")
    for claim in claims:
        evidence_repo.get_or_create_fact(pid, phase, card, claim, claim.lower())
    return card


def _known_section(context):
    return context.split("# KNOWN FACTS", 1)[1].split("\n# ", 1)[0]


def test_drafting_briefing_lists_known_facts_and_not_rejected_ones(temp_db):
    pid = projects_repo.get_or_create_id("P")
    _known(pid, "4", ["Deakin charges $3,000 per unit"])
    card = _known(pid, "2", [])
    gone, _ = evidence_repo.get_or_create_fact(pid, "2", card, "A rejected claim", "a rejected claim")
    evidence_repo.set_fact_status(gone, "rejected")
    context = supervisor_service.build_context("P")
    section = _known_section(context)
    assert "Phase 4 (Product Features): 1 fact" in section
    assert "Deakin charges $3,000 per unit" in section
    assert "A rejected claim" not in context
    assert context.index("# EXISTING PHASE FINDINGS") < context.index("# KNOWN FACTS") < context.index("# RESEARCH TASKS")


def test_no_known_facts_says_so(temp_db):
    projects_repo.get_or_create_id("P")
    assert "(no facts recorded yet)" in _known_section(supervisor_service.build_context("P"))


def test_known_facts_have_their_own_budget(temp_db):
    pid = projects_repo.get_or_create_id("P")
    for phase in "123456":
        _known(pid, phase, [f"Phase {phase} claim {i} " + "y" * 190 for i in range(12)])
    context = supervisor_service.build_context("P")
    section = _known_section(context)
    assert supervisor_service.MAX_KNOWN_FACTS_CHARS == 4000
    assert len(section) <= supervisor_service.MAX_KNOWN_FACTS_CHARS + 200
    assert "(more known facts not shown)" in section
    assert "# RESEARCH TASKS" in context and "# RECENT SUPERVISOR DECISIONS" in context
    assert "context truncated" not in context


def test_drafting_is_told_to_aim_at_the_gaps():
    assert "KNOWN FACTS" in supervisor_service.DRAFTING_SYSTEM_PROMPT
    assert "gaps" in supervisor_service.DRAFTING_SYSTEM_PROMPT
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_tools_state.py tests/test_supervisor_service.py -v`
Expected: the new tests FAIL (`KeyError: 'known_facts'`, and there is no `# KNOWN FACTS` section).

- [ ] **Step 3: Implement**

**`services/tools/state.py`.** Add `evidence_repo` to the `db.repositories` import, add the constants and the key:

```python
KNOWN_FACTS_PER_PHASE, KNOWN_FACT_CHARS = 10, 200
```

and in `get_project_state`'s returned dict, after `"recent_decisions": decisions,`:

```python
        "known_facts": {
            key: {"count": entry["count"], "recent": [clip_text(claim, KNOWN_FACT_CHARS) for claim in entry["recent"]]}
            for key, entry in evidence_repo.known_facts(ctx.project_id, per_phase=KNOWN_FACTS_PER_PHASE).items()
        },
```

Also extend the tool's description: `"...Phase 7 readiness, recent decisions and the facts already known per phase."`

**`services/supervisor_service.py`:**

1. Below `MAX_GAPS_CHARS`, add:

```python
# Known facts get their own budget inside the briefing (not one of the review limits in review_limits.py),
# so however many facts a project has, they cannot push the rest of the briefing out.
MAX_KNOWN_FACTS_CHARS = 4000
KNOWN_FACTS_HEADING = "# KNOWN FACTS (already established by earlier research; do not research these again)"
```

2. Add this function above `format_briefing`:

```python
def _known_facts_block(known, titles):
    if not known:
        return f"{KNOWN_FACTS_HEADING}\n\n(no facts recorded yet)"
    lines = []
    for key in sorted(known):
        entry = known[key]
        noun = "fact" if entry["count"] == 1 else "facts"
        lines.append(f"- Phase {key} ({titles.get(key, key)}): {entry['count']} {noun}. Most recent:")
        lines.extend(f"  - {_one_line(claim, 200)}" for claim in entry["recent"])
    body, used = [], 0
    for line in lines:
        if used + len(line) + 1 > MAX_KNOWN_FACTS_CHARS:
            body.append("  (more known facts not shown)")
            break
        body.append(line)
        used += len(line) + 1
    return f"{KNOWN_FACTS_HEADING}\n\n" + "\n".join(body)
```

3. In `format_briefing`, right after the `# EXISTING PHASE FINDINGS` block is appended and before the `item_lines = []` loop, add:

```python
    blocks.append(_known_facts_block(state.get("known_facts") or {}, titles))
```

(The decisions block stays last, so the existing over-cap handling of `blocks[-1]` is unchanged.)

4. Append this sentence to `DRAFTING_SYSTEM_PROMPT` (inside the parentheses, after `"...Phase 7 is never drafted here."`):

```python
    " The KNOWN FACTS section lists what earlier research already established for each phase: do not propose "
    "research to find those facts again; aim at the gaps."
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_tools_state.py tests/test_supervisor_service.py -v`. Expected: all pass, including every existing `build_context_*` test and `test_helpers_and_limits_moved_without_changing_values`. Then run `python -m ruff check services/tools/state.py services/supervisor_service.py tests/test_tools_state.py tests/test_supervisor_service.py` and `python -m pytest tests/ -q`. Confirm `git diff services/review_limits.py` is empty.

- [ ] **Step 5: Commit**

```bash
git add services/tools/state.py services/supervisor_service.py tests/test_tools_state.py tests/test_supervisor_service.py
git commit -m "Show the Supervisor known facts per phase when it drafts research"
```

---

### Task 7: Facts and conclusions on the Insights tab

**Files:**
- Create: `routes/evidence.py`, `static/evidence.js`, `tests/test_routes_evidence.py`, `tests/js/test_evidence_view.js`
- Modify: `services/evidence_service.py` (`list_evidence`), `app.py` (register blueprint), `static/app.js` (slot + hook in `renderInsights`), `templates/index.html` (search box, script tags), `static/style.css`, `tests/test_board_js.py`

**Interfaces:**
- Consumes: `evidence_repo.list_facts`, `list_conclusions` and `conclusion_fact_links` (Task 1). The tools `search_existing_evidence` and `update_evidence_status` (Task 2). `evidence_service.fact_dict` (Task 2). `tools.HTTP_STATUS` (4a).
- Produces:
  - `evidence_service.list_evidence(project_name, phase_key=None) -> {phase_key: {"facts": [fact_dict], "conclusions": [{"id", "phase_key", "text", "status", "fact_ids", "rejected_fact_count", "card_id", "card_title"}]}}`. Phases with nothing are left out.
  - Routes:
    - `GET /api/evidence?project=&phase=` returns `{"success", "phases"}`. A bad phase is a 400.
    - `GET /api/evidence/search?project=&q=&phase=` returns `{"success", "facts"}`. `q` is clipped to 200 characters.
    - `POST /api/evidence/<fact|conclusion>/<id>/<reject|restore>` returns `{"success", "id", "status"}`. Any other kind or action is a 404, and tool errors map through `tools.HTTP_STATUS`.
    - With no project, every route is a 400 "No project selected."
  - `static/evidence.js` exports these (in Node) `safeUrl(url) -> string | null`, `factNode(doc, fact, inSearch)`, `conclusionNode(doc, conclusion)`, `phaseEvidenceNode(doc, phaseData)` and `searchResultsNode(doc, facts, words)`. In the browser it sets `window.evidenceRenderAll(project)`.
  - DOM contract:
    - Each phase slot is `[data-evidence-phase="N"]`. The search box is `#evidence-search-input` and its results go in `#evidence-search-results`.
    - Buttons carry `data-ev-act` (`reject` | `restore`), `data-ev-kind` and `data-ev-id`.
    - Fact anchors are `#ev-fact-<id>`.

- [ ] **Step 1: Write the failing tests**

`tests/test_routes_evidence.py`:

```python
import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import projects_repo
from services import research_task_service, tools


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            c.post("/api/projects", json={"name": "P"})
            yield c
    set_database_path(None)


def _seed():
    pid = projects_repo.get_or_create_id("P")
    card = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB")
    data = tools.run_tool("record_research_facts", "supervisor", "P", {"task_id": card, "facts": [
        {"claim": "Deakin charges $3,000 per unit", "source_url": "https://deakin.example/fees",
         "source_title": "Deakin fees", "as_of": "2026"},
        {"claim": "Monash runs three intakes", "quote": "three intakes a year"},
    ], "conclusions": [{"text": "Fees and intakes differ", "fact_numbers": [1, 2]}]}).data
    return card, data


def test_list_groups_facts_and_conclusions_by_phase(client):
    card, data = _seed()
    body = client.get("/api/evidence?project=P").get_json()
    assert body["success"] is True and set(body["phases"]) == {"4"}
    phase = body["phases"]["4"]
    assert [f["claim"] for f in phase["facts"]] == ["Deakin charges $3,000 per unit", "Monash runs three intakes"]
    assert phase["facts"][0]["source"] == {"url": "https://deakin.example/fees", "title": "Deakin fees",
                                           "publisher": "", "published_date": ""}
    assert (phase["facts"][0]["card_title"], phase["facts"][0]["as_of"]) == ("Fees", "2026")
    assert phase["facts"][1]["source"] is None and phase["facts"][1]["quote"] == "three intakes a year"
    (conclusion,) = phase["conclusions"]
    assert conclusion["text"] == "Fees and intakes differ"
    assert conclusion["fact_ids"] == data["fact_ids"] and conclusion["rejected_fact_count"] == 0


def test_list_for_one_phase(client):
    _seed()
    assert set(client.get("/api/evidence?project=P&phase=4").get_json()["phases"]) == {"4"}
    assert client.get("/api/evidence?project=P&phase=2").get_json()["phases"] == {}
    assert client.get("/api/evidence?project=P&phase=9").status_code == 400


def test_a_project_with_no_facts_lists_nothing(client):
    assert client.get("/api/evidence?project=P").get_json()["phases"] == {}


def test_search(client):
    _seed()
    body = client.get("/api/evidence/search?project=P&q=deakin").get_json()
    assert [f["claim"] for f in body["facts"]] == ["Deakin charges $3,000 per unit"]
    assert client.get("/api/evidence/search?project=P&q=" + "x" * 500).status_code == 200
    assert client.get("/api/evidence/search?project=P&q=deakin&phase=2").get_json()["facts"] == []


def test_reject_and_restore_a_fact(client):
    _, data = _seed()
    fact_id = data["fact_ids"][0]
    resp = client.post(f"/api/evidence/fact/{fact_id}/reject", json={"project": "P"})
    assert resp.status_code == 200 and resp.get_json() == {"success": True, "id": fact_id, "status": "rejected"}
    phase = client.get("/api/evidence?project=P").get_json()["phases"]["4"]
    assert phase["facts"][0]["status"] == "rejected"
    assert phase["conclusions"][0]["rejected_fact_count"] == 1
    client.post(f"/api/evidence/fact/{fact_id}/restore", json={"project": "P"})
    assert client.get("/api/evidence?project=P").get_json()["phases"]["4"]["facts"][0]["status"] == "active"


def test_reject_a_conclusion(client):
    _, data = _seed()
    conclusion_id = data["conclusion_ids"][0]
    assert client.post(f"/api/evidence/conclusion/{conclusion_id}/reject", json={"project": "P"}).get_json()["status"] == "rejected"


@pytest.mark.parametrize("url", ["/api/evidence/table/1/reject", "/api/evidence/fact/1/delete", "/api/evidence/fact/99999/reject"])
def test_unknown_things_are_404(client, url):
    _seed()
    assert client.post(url, json={"project": "P"}).status_code == 404


def test_no_project_is_400(client):
    assert client.get("/api/evidence?project=Nope").status_code == 400
    assert client.get("/api/evidence/search?project=Nope&q=x").status_code == 400
    assert client.post("/api/evidence/fact/1/reject", json={"project": "Nope"}).status_code == 400


def test_the_page_loads_the_evidence_script_and_search_box(client):
    html = client.get("/").get_data(as_text=True)
    assert 'src="/static/evidence.js' in html and 'id="evidence-search-input"' in html
```

`tests/js/test_evidence_view.js`:

```js
// Run: node tests/js/test_evidence_view.js  (also run by tests/test_board_js.py)
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const E = require('../../static/evidence.js');

// A tiny DOM: enough for evidence.js, and it refuses innerHTML outright.
class FakeNode {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.className = ''; this.ownText = ''; }
    appendChild(child) { this.children.push(child); return child; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    get textContent() { return this.ownText + this.children.map(c => c.textContent).join(''); }
    set innerHTML(value) { throw new Error('evidence.js must never set innerHTML'); }
}
const doc = { createElement: tag => new FakeNode(tag) };
const all = (node, test, out = []) => { if (test(node)) out.push(node); node.children.forEach(c => all(c, test, out)); return out; };
const tagged = (node, tag) => all(node, n => n.tagName === tag.toUpperCase());

function fact(over) {
    return Object.assign({ id: 12, phase_key: '4', claim: 'Deakin charges $3,000 per unit', quote: '', as_of: '2026', status: 'active',
        source: { url: 'https://deakin.edu.au/fees', title: 'Deakin fees', publisher: '', published_date: '' },
        card_id: 7, card_title: 'Fees', created_at: '2026-10-05T10:00:00' }, over || {});
}
function conclusion(over) {
    return Object.assign({ id: 3, phase_key: '4', text: 'Deakin is the priciest', status: 'active', fact_ids: [2, 5, 7],
        rejected_fact_count: 0, card_id: 7, card_title: 'Fees' }, over || {});
}
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('the source never uses HTML-string APIs', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'evidence.js'), 'utf8');
    ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write'].forEach(api => assert(!source.includes(api), api));
});
test('AI and web text is shown as text, not markup', () => {
    const node = E.phaseEvidenceNode(doc, { facts: [fact({ claim: '<img src=x onerror=alert(1)>', card_title: '<b>Fees</b>' })], conclusions: [] });
    assert.strictEqual(tagged(node, 'img').length, 0);
    assert.strictEqual(tagged(node, 'b').length, 0);
    assert(node.textContent.includes('<img src=x onerror=alert(1)>'));
});
test('a web page becomes a safe link that opens in a new tab', () => {
    const [link] = tagged(E.factNode(doc, fact(), false), 'a');
    assert.strictEqual(link.getAttribute('href'), 'https://deakin.edu.au/fees');
    assert.strictEqual(link.getAttribute('target'), '_blank');
    assert.strictEqual(link.getAttribute('rel'), 'noopener noreferrer');
    assert.strictEqual(link.textContent, 'Deakin fees');
});
test('only http and https addresses become links', () => {
    ['javascript:alert(1)', 'data:text/html,hi', ' JAVASCRIPT:alert(1)', 'ftp://x.example', '//evil.example', ''].forEach(url => {
        const node = E.factNode(doc, fact({ source: { url, title: 'Page' } }), false);
        assert.strictEqual(tagged(node, 'a').length, 0, url);
        assert(node.textContent.includes('Page'), url);
    });
    assert.strictEqual(E.safeUrl('HTTPS://ok.example/x'), 'HTTPS://ok.example/x');
    assert.strictEqual(E.safeUrl('javascript:alert(1)'), null);
    assert.strictEqual(E.safeUrl(null), null);
});
test('a fact without a page says it comes from the report', () => {
    assert(E.factNode(doc, fact({ source: null }), false).textContent.includes('Source: the research report'));
});
test('a fact shows its number, date, research and a Reject button', () => {
    const node = E.factNode(doc, fact(), false);
    assert.strictEqual(node.getAttribute('id'), 'ev-fact-12');
    const text = node.textContent;
    assert(text.includes('#12') && text.includes('As of 2026') && text.includes('From: Fees'));
    const [button] = tagged(node, 'button');
    assert.deepStrictEqual([button.getAttribute('data-ev-act'), button.getAttribute('data-ev-kind'), button.getAttribute('data-ev-id')],
                           ['reject', 'fact', '12']);
});
test('a conclusion links to the facts behind it', () => {
    const node = E.conclusionNode(doc, conclusion());
    assert.deepStrictEqual(tagged(node, 'a').map(a => a.getAttribute('href')), ['#ev-fact-2', '#ev-fact-5', '#ev-fact-7']);
    assert(node.textContent.includes('Based on facts 2, 5, 7'));
    assert(E.conclusionNode(doc, conclusion({ fact_ids: [4] })).textContent.includes('Based on fact 4'));
});
test('a conclusion says when its facts were rejected', () => {
    assert(E.conclusionNode(doc, conclusion({ rejected_fact_count: 1 })).textContent.includes('1 of its facts was rejected'));
    assert(E.conclusionNode(doc, conclusion({ rejected_fact_count: 2 })).textContent.includes('2 of its facts were rejected'));
});
test('rejected items sit under Show rejected, with Restore', () => {
    const node = E.phaseEvidenceNode(doc, { facts: [fact(), fact({ id: 13, claim: 'Old claim', status: 'rejected' })],
                                             conclusions: [conclusion({ status: 'rejected' })] });
    const [box] = tagged(node, 'details');
    assert(box.textContent.includes('Show rejected (2)') && box.textContent.includes('Old claim'));
    assert.deepStrictEqual(tagged(box, 'button').map(b => b.getAttribute('data-ev-act')), ['restore', 'restore']);
    assert(node.textContent.includes('Facts (1)'));
    assert(!node.textContent.includes('Conclusions'));  // its only conclusion was rejected
});
test('a phase with nothing yet says so', () => {
    assert(E.phaseEvidenceNode(doc, undefined).textContent.includes('No facts yet'));
    assert(E.phaseEvidenceNode(doc, { facts: [], conclusions: [] }).textContent.includes('No facts yet'));
});
test('search results show the phase, with no buttons or anchors', () => {
    const node = E.searchResultsNode(doc, [fact()], 'deakin');
    assert(node.textContent.includes('1 fact matches') && node.textContent.includes('Phase 4'));
    assert.strictEqual(tagged(node, 'button').length, 0);
    assert.strictEqual(all(node, n => n.getAttribute('id') !== null).length, 0);
    assert(E.searchResultsNode(doc, [], '<x>').textContent.includes('No facts match “<x>”.'));
});

let failed = 0;
for (const [name, fn] of tests) {
    try { fn(); console.log('ok -', name); } catch (e) { failed++; console.log('FAIL -', name, '\n ', e.message); }
}
if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log(`all ${tests.length} passed`);
```

Append to `tests/test_board_js.py`:

```python
@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_evidence_view_js():
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "test_evidence_view.js")],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_routes_evidence.py -v` and `node tests/js/test_evidence_view.js`
Expected: FAIL (404s, and `Cannot find module '../../static/evidence.js'`).

- [ ] **Step 3: Implement**

Append to `services/evidence_service.py`, and add `from db.repositories import evidence_repo, projects_repo` at the top:

```python
def list_evidence(project_name, phase_key=None):
    """Every fact and conclusion of the project (rejected ones included and marked), grouped by phase."""
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    phases = {}

    def slot(key):
        return phases.setdefault(key, {"facts": [], "conclusions": []})

    for row in evidence_repo.list_facts(project_id, phase_key):
        slot(row["phase_key"])["facts"].append(fact_dict(row))
    links = evidence_repo.conclusion_fact_links(project_id)
    for row in evidence_repo.list_conclusions(project_id, phase_key):
        cited = links.get(row["id"], [])
        slot(row["phase_key"])["conclusions"].append({
            "id": row["id"], "phase_key": row["phase_key"], "text": row["text"], "status": row["status"],
            "fact_ids": [fact_id for fact_id, _ in cited],
            "rejected_fact_count": sum(1 for _, status in cited if status == "rejected"),
            "card_id": row["research_work_item_id"], "card_title": row["card_title"] or "",
        })
    return phases
```

`routes/evidence.py`:

```python
from flask import Blueprint, jsonify, request, session

from services import evidence_service, tools
from services.project_service import normalize_project_name, project_exists

evidence_bp = Blueprint("evidence", __name__)
PHASES = ("1", "2", "3", "4", "5", "6", "7")
KINDS = ("fact", "conclusion")
STATUS_ACTIONS = ("reject", "restore")
SEARCH_CHARS = 200


def _project():
    data = request.get_json(silent=True) or {}
    project = normalize_project_name(data.get("project") or request.args.get("project") or session.get("current_project"))
    return project if project and project_exists(project) else None


def _no_project():
    return jsonify({"success": False, "error": "No project selected."}), 400


def _phase():
    return (request.args.get("phase") or "").strip() or None


def _tool_response(result):
    if result.ok:
        return jsonify({"success": True, **result.data})
    return jsonify({"success": False, "error": result.error["message"]}), tools.HTTP_STATUS.get(result.error["code"], 500)


@evidence_bp.route("/api/evidence", methods=["GET"])
def list_evidence():
    project = _project()
    if not project:
        return _no_project()
    phase = _phase()
    if phase is not None and phase not in PHASES:
        return jsonify({"success": False, "error": "Choose a phase from 1 to 7."}), 400
    return jsonify({"success": True, "phases": evidence_service.list_evidence(project, phase)})


@evidence_bp.route("/api/evidence/search", methods=["GET"])
def search_evidence():
    project = _project()
    if not project:
        return _no_project()
    inputs = {"words": (request.args.get("q") or "")[:SEARCH_CHARS], "limit": 20}
    phase = _phase()
    if phase is not None:
        if phase not in PHASES:
            return jsonify({"success": False, "error": "Choose a phase from 1 to 7."}), 400
        inputs["phase_key"] = phase
    return _tool_response(tools.run_tool("search_existing_evidence", "user", project, inputs))


@evidence_bp.route("/api/evidence/<kind>/<int:item_id>/<action>", methods=["POST"])
def set_status(kind, item_id, action):
    project = _project()
    if not project:
        return _no_project()
    if kind not in KINDS or action not in STATUS_ACTIONS:
        return jsonify({"success": False, "error": "Unknown action."}), 404
    return _tool_response(tools.run_tool("update_evidence_status", "user", project,
                                         {"kind": kind, "id": item_id, "action": action}))
```

In `app.py` add `from routes.evidence import evidence_bp` (in sorted position) and `app.register_blueprint(evidence_bp)` after `board_bp`.

`static/evidence.js`:

```js
// static/evidence.js
// Facts and conclusions on the Insights tab (below each phase write-up), and the fact search box.
// Everything is built as DOM nodes: AI and web text is only ever set with textContent, and links are made
// only for http/https addresses. The builders take the document as an argument so the Node tests in
// tests/js/test_evidence_view.js can run them against a small fake DOM.
(function () {
    'use strict';

    function safeUrl(url) {
        if (typeof url !== 'string') return null;
        const trimmed = url.trim();
        return /^https?:\/\/\S/i.test(trimmed) ? trimmed : null;
    }
    function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }
    function el(doc, tag, cls, text) {
        const node = doc.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = String(text);
        return node;
    }
    function actionButton(doc, kind, item) {
        const act = item.status === 'rejected' ? 'restore' : 'reject';
        const button = el(doc, 'button', 'ev-btn', act === 'reject' ? 'Reject' : 'Restore');
        button.setAttribute('type', 'button');
        button.setAttribute('data-ev-act', act);
        button.setAttribute('data-ev-kind', kind);
        button.setAttribute('data-ev-id', String(Number(item.id)));
        return button;
    }
    function sourceNode(doc, source) {
        if (!source) return el(doc, 'span', 'ev-src', 'Source: the research report');
        const label = source.title || source.url || 'Source';
        const href = safeUrl(source.url);
        if (!href) return el(doc, 'span', 'ev-src', label);
        const link = el(doc, 'a', 'ev-src', label);
        link.setAttribute('href', href);
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
        return link;
    }
    // In search results a fact shows its phase and has no button and no anchor id (ids stay unique on the page).
    function factNode(doc, fact, inSearch) {
        const item = el(doc, 'li', fact.status === 'rejected' ? 'ev-fact rejected' : 'ev-fact');
        if (!inSearch) item.setAttribute('id', `ev-fact-${Number(fact.id)}`);
        const head = el(doc, 'div', 'ev-head');
        head.appendChild(el(doc, 'span', 'ev-num', inSearch ? `Phase ${fact.phase_key}` : `#${Number(fact.id)}`));
        head.appendChild(el(doc, 'span', 'ev-claim', fact.claim));
        item.appendChild(head);
        if (fact.quote) item.appendChild(el(doc, 'blockquote', 'ev-quote', fact.quote));
        const meta = el(doc, 'div', 'ev-meta');
        meta.appendChild(sourceNode(doc, fact.source));
        if (fact.as_of) meta.appendChild(el(doc, 'span', 'ev-asof', `As of ${fact.as_of}`));
        if (fact.card_title) meta.appendChild(el(doc, 'span', 'ev-from', `From: ${fact.card_title}`));
        if (!inSearch) meta.appendChild(actionButton(doc, 'fact', fact));
        item.appendChild(meta);
        return item;
    }
    function basedOnNode(doc, conclusion) {
        const ids = (conclusion.fact_ids || []).map(Number);
        const line = el(doc, 'p', 'ev-based', ids.length === 1 ? 'Based on fact ' : 'Based on facts ');
        ids.forEach((id, i) => {
            if (i) line.appendChild(el(doc, 'span', null, ', '));
            const ref = el(doc, 'a', 'ev-ref', String(id));
            ref.setAttribute('href', `#ev-fact-${id}`);
            line.appendChild(ref);
        });
        const rejected = Number(conclusion.rejected_fact_count) || 0;
        if (rejected) {
            line.appendChild(el(doc, 'span', 'ev-warn',
                rejected === 1 ? ' · 1 of its facts was rejected' : ` · ${rejected} of its facts were rejected`));
        }
        return line;
    }
    function conclusionNode(doc, conclusion) {
        const item = el(doc, 'li', conclusion.status === 'rejected' ? 'ev-concl rejected' : 'ev-concl');
        item.appendChild(el(doc, 'p', 'ev-text', conclusion.text));
        const meta = el(doc, 'div', 'ev-meta');
        meta.appendChild(basedOnNode(doc, conclusion));
        meta.appendChild(actionButton(doc, 'conclusion', conclusion));
        item.appendChild(meta);
        return item;
    }
    function listNode(doc, items, build) {
        const list = el(doc, 'ul', 'ev-list');
        items.forEach(x => list.appendChild(build(doc, x)));
        return list;
    }
    const pageFact = (doc, fact) => factNode(doc, fact, false);

    function phaseEvidenceNode(doc, data) {
        const facts = (data && data.facts) || [];
        const conclusions = (data && data.conclusions) || [];
        const wrap = el(doc, 'section', 'ev-phase');
        wrap.setAttribute('aria-label', 'Facts and conclusions');
        if (!facts.length && !conclusions.length) {
            wrap.appendChild(el(doc, 'p', 'ev-empty', 'No facts yet. They are saved here when finished research for this phase is reviewed.'));
            return wrap;
        }
        const live = list => list.filter(x => x.status !== 'rejected');
        const gone = list => list.filter(x => x.status === 'rejected');
        if (live(conclusions).length) {
            wrap.appendChild(el(doc, 'h4', null, 'Conclusions'));
            wrap.appendChild(listNode(doc, live(conclusions), conclusionNode));
        }
        if (live(facts).length) {
            wrap.appendChild(el(doc, 'h4', null, `Facts (${live(facts).length})`));
            wrap.appendChild(listNode(doc, live(facts), pageFact));
        }
        const hidden = gone(conclusions).length + gone(facts).length;
        if (hidden) {
            const box = el(doc, 'details', 'ev-rejected');
            box.appendChild(el(doc, 'summary', null, `Show rejected (${hidden})`));
            if (gone(conclusions).length) box.appendChild(listNode(doc, gone(conclusions), conclusionNode));
            if (gone(facts).length) box.appendChild(listNode(doc, gone(facts), pageFact));
            wrap.appendChild(box);
        }
        return wrap;
    }
    function searchResultsNode(doc, facts, words) {
        const wrap = el(doc, 'div', 'ev-results');
        if (!facts.length) {
            wrap.appendChild(el(doc, 'p', 'ev-empty', `No facts match “${words}”.`));
            return wrap;
        }
        wrap.appendChild(el(doc, 'p', 'ev-count', plural(facts.length, 'fact matches', 'facts match')));
        wrap.appendChild(listNode(doc, facts, (d, f) => factNode(d, f, true)));
        return wrap;
    }

    const pure = { safeUrl, factNode, conclusionNode, phaseEvidenceNode, searchResultsNode };
    if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }

    // ---------------- browser glue ----------------
    const ev = { project: null, seq: 0, searchSeq: 0, timer: null };

    async function call(method, url, body) {
        const options = { method };
        if (body !== undefined) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(body); }
        const res = await fetch(url, options);
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!res.ok || !data || data.success === false) throw new Error((data && data.error) || `Something went wrong (${res.status}).`);
        return data;
    }
    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
    function slots() { return Array.from(document.querySelectorAll('[data-evidence-phase]')); }

    function resetSearch() {
        const input = document.getElementById('evidence-search-input');
        const out = document.getElementById('evidence-search-results');
        if (input) input.value = '';
        if (out) clear(out);
    }

    async function renderAll(project) {
        if (project !== ev.project) { ev.project = project; resetSearch(); }
        const seq = ++ev.seq;
        if (!project || !slots().length) return;
        let data;
        try {
            data = await call('GET', `/api/evidence?project=${encodeURIComponent(project)}`);
        } catch (err) {
            if (seq !== ev.seq) return;
            slots().forEach(slot => { clear(slot); slot.appendChild(el(document, 'p', 'ev-err', err.message)); });
            return;
        }
        if (seq !== ev.seq || project !== ev.project) return;
        slots().forEach(slot => {
            clear(slot);
            slot.appendChild(phaseEvidenceNode(document, data.phases[slot.getAttribute('data-evidence-phase')]));
        });
    }

    async function runSearch(words) {
        const out = document.getElementById('evidence-search-results');
        if (!out) return;
        const seq = ++ev.searchSeq;
        if (!ev.project || !words.trim()) { clear(out); return; }
        let node;
        try {
            const data = await call('GET', `/api/evidence/search?project=${encodeURIComponent(ev.project)}&q=${encodeURIComponent(words)}`);
            node = searchResultsNode(document, data.facts || [], words);
        } catch (err) {
            node = el(document, 'p', 'ev-err', err.message);
        }
        if (seq !== ev.searchSeq) return;
        clear(out);
        out.appendChild(node);
    }

    document.addEventListener('input', event => {
        if (!event.target || event.target.id !== 'evidence-search-input') return;
        clearTimeout(ev.timer);
        const words = event.target.value;
        ev.timer = setTimeout(() => runSearch(words), 300);
    });

    document.addEventListener('click', async event => {
        const target = event.target;
        if (!target || !target.closest) return;
        const ref = target.closest('a.ev-ref');
        if (ref) {  // open "Show rejected" if the fact is in there; the browser then follows the #anchor
            const factEl = document.getElementById((ref.getAttribute('href') || '').slice(1));
            const box = factEl && factEl.closest('details');
            if (box) box.open = true;
            return;
        }
        const button = target.closest('[data-ev-act]');
        if (!button || button.disabled || !ev.project) return;
        const project = ev.project;
        button.disabled = true;
        try {
            await call('POST', `/api/evidence/${button.getAttribute('data-ev-kind')}/${button.getAttribute('data-ev-id')}/${button.getAttribute('data-ev-act')}`, { project });
            if (project === ev.project) renderAll(project);
        } catch (err) {
            button.disabled = false;
            button.after(el(document, 'span', 'ev-err', err.message));
        }
    });

    window.evidenceRenderAll = renderAll;
})();
```

**`static/app.js`**, in `renderInsights`. Replace:

```js
                    ${renderPhaseLinkedFiles(key, phase)}
                    ${hasContent ? '<div class="phase-edit-hint">Click to edit</div>' : ''}
                </div>`;
        });
    }

    renderExcludedCompetitors();
```

with:

```js
                    ${renderPhaseLinkedFiles(key, phase)}
                    ${hasContent ? '<div class="phase-edit-hint">Click to edit</div>' : ''}
                </div>
                <div class="ev-slot" data-evidence-phase="${escapeAttr(key)}"></div>`;
        });
    }

    // Facts and conclusions sit below each phase card, outside it, so clicking them never opens the phase editor.
    if (typeof evidenceRenderAll === 'function') evidenceRenderAll(currentProject);

    renderExcludedCompetitors();
```

**`templates/index.html`:**

1. Replace `<div class="story-map-content">` followed by `<div class="section-header-with-action">` with:

```html
                    <div class="story-map-content">
                        <div id="evidence-search" class="ev-search">
                            <label for="evidence-search-input">Search facts</label>
                            <input type="search" id="evidence-search-input" placeholder="Search the facts saved from your research, across all phases" autocomplete="off">
                            <div id="evidence-search-results" aria-live="polite"></div>
                        </div>
                        <div class="section-header-with-action">
```

2. Replace the script tags:

```html
    <script src="/static/app.js?v=13.6"></script>
    <script src="/static/board.js?v=4"></script>
    <script src="/static/evidence.js?v=1"></script>
```

Append to **`static/style.css`**:

```css
/* ---- Facts and conclusions on the Insights tab (static/evidence.js) ---- */
.ev-search { margin: 0 0 1.5rem; }
.ev-search label { display: block; font-weight: 600; color: var(--oes-ink); margin-bottom: 0.25rem; }
.ev-search input { width: 100%; max-width: 32rem; padding: 0.5rem 0.75rem; border: 1px solid #ccc; border-radius: 6px; }
.ev-slot { margin: -0.5rem 0 1.5rem; }
.ev-phase { background: #fff; border: 1px solid #e0e0e0; border-top: 0; border-radius: 0 0 8px 8px; padding: 0.75rem 1rem; }
.ev-phase h4 { font-size: 0.9rem; margin: 0.5rem 0; }
.ev-list { list-style: none; margin: 0; padding: 0; }
.ev-fact, .ev-concl { padding: 0.5rem 0; border-bottom: 1px solid #f0f0f0; }
.ev-fact.rejected, .ev-concl.rejected { opacity: 0.6; }
.ev-head { display: flex; gap: 0.5rem; align-items: baseline; }
.ev-num { font-family: monospace; font-size: 0.75rem; color: #666; flex-shrink: 0; }
.ev-text { margin: 0; }
.ev-quote { margin: 0.25rem 0 0 1.5rem; padding-left: 0.5rem; border-left: 3px solid var(--oes-sky); color: #444; font-size: 0.85rem; }
.ev-meta { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; font-size: 0.8rem; color: #666; margin-top: 0.25rem; }
.ev-meta a { color: var(--oes-ink); overflow-wrap: anywhere; }
.ev-based { margin: 0; }
.ev-warn { color: #b45309; }
.ev-err { color: #dc3545; font-size: 0.85rem; }
.ev-empty { color: #666; font-size: 0.85rem; margin: 0; }
.ev-btn { font-size: 0.75rem; padding: 0.15rem 0.5rem; border: 1px solid #ccc; border-radius: 4px; background: #fff; cursor: pointer; margin-left: auto; }
.ev-btn:hover { border-color: var(--oes-ink); }
.ev-rejected summary { cursor: pointer; font-size: 0.85rem; color: #666; margin-top: 0.5rem; }
.ev-results { margin-top: 0.5rem; }
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `python -m pytest tests/test_routes_evidence.py tests/test_board_js.py -v`, `node tests/js/test_evidence_view.js` (expected: all 11 passed) and `node tests/js/test_board_view.js`. Then run `python -m ruff check routes/evidence.py services/evidence_service.py app.py tests/test_routes_evidence.py tests/test_board_js.py` and `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add routes/evidence.py services/evidence_service.py app.py static/evidence.js static/app.js static/style.css templates/index.html tests/test_routes_evidence.py tests/js/test_evidence_view.js tests/test_board_js.py
git commit -m "Show facts and conclusions per phase on the Insights tab, with fact search"
```

---

### Task 8: Acceptance tests, docs, demo and walkthrough

**Files:**
- Modify: `tests/test_tools_structure.py`, `CLAUDE.md`, `scripts/run_board_demo.py`

**Interfaces:**
- Consumes: the full registry (Tasks 2, 3 and 4), and `supervisor_service.DRAFTING_MENU`, `REVIEW_MENU` and `EXTRACT_MENU`.
- Confirms `EXPECTED_CALLERS` (built up in Tasks 2–4) now holds the seven new tools: `save_source`, `save_evidence` and `create_finding` as `{supervisor, system}`; `search_existing_evidence` as `{supervisor, user, system}`; `record_research_facts` as `{supervisor}`; `extract_research_facts` as `{user, system}`; `update_evidence_status` as `{user}`.

- [ ] **Step 1: Write the tests**

In `tests/test_tools_structure.py`, rename `test_the_registry_is_exactly_the_phase_4a_tools_with_the_agreed_callers` to `test_the_registry_is_exactly_the_agreed_tools_with_the_agreed_callers` (the body is unchanged). Then replace `test_the_supervisor_menus_only_hold_tools_it_may_call` with the following, and add the three new tests:

```python
SUPERVISOR_MENUS = ("DRAFTING_MENU", "REVIEW_MENU", "EXTRACT_MENU")


def _menu_tools():
    return [name for menu in SUPERVISOR_MENUS for name in getattr(supervisor_service, menu)]


def test_the_supervisor_menus_only_hold_tools_it_may_call():
    for name in _menu_tools():
        assert "supervisor" in tools.get_tool(name).callers


def test_the_supervisor_saves_facts_only_through_record_research_facts():
    assert supervisor_service.EXTRACT_MENU == ("record_research_facts",)
    assert set(_menu_tools()).isdisjoint({"save_source", "save_evidence", "create_finding",
                                          "update_evidence_status", "extract_research_facts"})


def test_the_review_menu_is_unchanged_and_the_review_has_no_fact_fields():
    assert supervisor_service.REVIEW_MENU == ("evaluate_research_output",)
    props = tools.claude_tools(["evaluate_research_output"])[0]["input_schema"]["properties"]
    assert "facts" not in props and "conclusions" not in props


@pytest.mark.parametrize("name,caller", [
    ("update_evidence_status", "supervisor"), ("update_evidence_status", "system"),
    ("extract_research_facts", "supervisor"),  # the system may extract (after a review); the Supervisor never
])
def test_the_supervisor_cannot_reject_restore_or_start_a_paid_extraction(temp_db, name, caller):
    pid = projects_repo.get_or_create_id("P")
    card = research_work_items_repo.create(pid, "4", "Card")
    inputs = {"kind": "fact", "id": 1, "action": "reject"} if name == "update_evidence_status" else {"task_id": card}
    assert tools.run_tool(name, caller, "P", inputs).error["code"] == "not_allowed"
```

- [ ] **Step 2: Run them**

Run `python -m pytest tests/test_tools_structure.py -v`.
Expected: PASS. If something fails, it has found a real gap: fix the tool or menu, not the test. Then run `python -m ruff check tests/test_tools_structure.py`, `python -m pytest tests/ -q`, `node tests/js/test_board_view.js` and `node tests/js/test_evidence_view.js`.

- [ ] **Step 3: Document**

In `CLAUDE.md`:

1. Under **Backend → Services**, in the `tools/` bullet, change `(`supervisor_service.DRAFTING_MENU`, `REVIEW_MENU`)` to `(`supervisor_service.DRAFTING_MENU`, `REVIEW_MENU`, `EXTRACT_MENU`)`. Then add a new bullet:

```markdown
  - Facts and conclusions (Phase 4b): right after each review (any outcome but FAILED; never the options report), the system runs `extract_research_facts`. That is one more Claude call, reading up to 60,000 characters of the report (the review itself still sees 8,000, unchanged). It saves the report's key facts (each with the page it came from and the date it applies to) and up to 3 conclusions per phase through `record_research_facts` (`services/tools/research_facts.py`; the smaller `save_source` / `save_evidence` / `create_finding` / `search_existing_evidence` / `update_evidence_status` tools are in `services/tools/evidence.py`). A failed extraction never changes the review. Cards without facts (older reports, or a failed or empty extraction) get an "Extract facts" button: the same tool, called by the user. `research_runs.facts_extracted_at` stops a report being paid for twice. Drafting sees a capped `# KNOWN FACTS` section (`MAX_KNOWN_FACTS_CHARS` in `supervisor_service`). Nothing is deleted; the person can reject and restore facts and conclusions on the Insights tab.
```

2. Under **Frontend**, after the Research bullet, add:

```markdown
- **Insights → facts** (`static/evidence.js`, `routes/evidence.py`, `services/evidence_service.py`): below each phase write-up, the phase's conclusions and facts with their sources, Reject / Restore, and a fact search box. Built as DOM nodes with `textContent` only; links only for http/https.
```

3. Under **Data Model**, add:

```markdown
**Facts and conclusions** live in the database (migration `0007`): `cited_sources`, `facts`, `conclusions`, `conclusion_facts`. These are separate from the older `evidence` / `findings` tables, which back the Insights phase write-ups.
```

- [ ] **Step 4: Update the demo**

In `scripts/run_board_demo.py`, make the fake Anthropic client answer the new calls. Add `demo_numbers = itertools.count(1)` beside `review_outcomes`, and add this helper:

```python
def demo_facts():
    n = next(demo_numbers)
    return {"facts": [
        {"claim": f"Providers offer graduate certificates and masters (demo report {n}).",
         "source_url": "https://example.edu/psych", "source_title": "Provider page", "as_of": "2026"},
        {"claim": f"Intake dates were not on an official page (demo report {n}).", "as_of": "2026"},
    ], "conclusions": [{"text": f"Online Psychology study is mostly certificates and masters (demo report {n}).",
                        "fact_numbers": [1, 2]}]}
```

Also add `extraction_calls = itertools.count(1)` beside it. In `FakeAnthropic.create`, after the `create_research_task` branch, add the extraction answer. It is used both after each review and by the button. Every third answer finds nothing, so the walkthrough has a finished card that still shows the button:

```python
        if names == {"record_research_facts"}:  # the extraction after a review, or the Extract facts button
            if next(extraction_calls) % 3 == 0:
                return SimpleNamespace(content=[Block("record_research_facts", {"facts": [], "conclusions": []})])
            return SimpleNamespace(content=[Block("record_research_facts", demo_facts())])
```

The review answer (`evaluate_research_output`) is unchanged: it has no facts.

- [ ] **Step 5: Commit**

```bash
git add tests/test_tools_structure.py CLAUDE.md scripts/run_board_demo.py
git commit -m "Extend the tool acceptance tests to evidence; document facts and update the demo"
```

- [ ] **Step 6 (controller): Browser walkthrough, then one real run**

1. Start `python scripts/run_board_demo.py` and walk through the stubbed app:
   - Draft, approve and run three researches, and wait for the automatic reviews.
   - In Activity, check for "Supervisor saved 2 facts and 1 conclusion from …" right after "Supervisor reviewed …". There is no "You extracted facts" line for the automatic step.
   - On Insights, check each phase that had research:
     - Conclusions appear first, with "Based on facts …" links that jump to their facts.
     - Facts show source links that open in a new tab.
   - Reject a fact. It moves under "Show rejected (1)", and its conclusion says "1 of its facts was rejected". Restore it.
   - Search for "Providers".
   - On the finished card whose automatic extraction found nothing (it still shows "Extract facts"), use the button from the card: check the paid-call confirm, the note, the "You extracted facts" Activity line, and that the button is gone afterwards. If another card still shows the button, repeat the check from the report reader.
   - Draft again. The Supervisor's draft request (its briefing) has a `# KNOWN FACTS` section.
   - Switch project and check that Insights facts and the search box reset.

   Any failure goes back through the normal fix loop. Stop the demo server afterwards.
2. Ask the user before making **one real run** (it costs money). One real web research should be reviewed by the real Claude model, then go through the automatic extraction. It should end with facts saved, or a clean "skipped" Activity line if the model garbles them. Note the extra time and cost of the extraction call (expected: roughly 10–15 cents). Record anything the model did that the clean-up did not handle.
