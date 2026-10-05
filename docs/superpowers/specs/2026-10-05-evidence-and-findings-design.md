# Phase 4b-1: Evidence and findings — Design

**Date:** 2026-10-05
**Status:** Approved in conversation; awaiting written-spec review. Amended 2026-10-05: facts are extracted in a separate call after the review, not inside it (§4.1).
**Roadmap:** Phase 4, part b, first of three pieces (evidence & findings → targeted search → Power BI). The other two get their own specs.
**Builds on:** Phase 4a tool layer (`2026-10-05-supervisor-tool-layer-design.md`), Research Board (`2026-10-05-research-board-design.md`).

## 1. Goal

Finished research reports turn into **individual cited facts** and a few **cited conclusions** per phase, stored so they can be searched and traced. Two outcomes, both chosen by the user:

1. **Traceability** — every fact carries the page it came from and the date it applies to; every conclusion names the facts behind it.
2. **Don't pay twice** — when the Supervisor drafts new research it sees what is already known per phase and aims at the gaps.

### Decisions already made (by the user)

| Topic | Decision |
|---|---|
| Order | Evidence & findings first; targeted search and Power BI later, each with its own spec. |
| When facts are made | Automatically right after the review, in a second call that reads the full report (up to 60,000 characters). The review call itself is unchanged: it sees only the first 8,000 characters (`services/review_limits.py`), too little to mine facts from. |
| Sign-off | Facts and conclusions are saved and used straight away; the user can reject (and restore) any. Rejected items are ignored from then on. |
| Where shown | Insights, per phase, below the phase write-up. |
| Findings | Short cited conclusions per phase, drafted by the Supervisor in the extraction call that follows the review (or the "Extract facts" button), each linked to the facts behind it. |
| Older reports | An "Extract facts" button on finished cards with no facts yet — one paid Claude call each, only when clicked. The same button covers a report whose automatic extraction failed or found nothing. |
| Storage | New, separate tables (approach A). The existing `findings` / `evidence` / `finding_evidence` tables (Insights phase write-ups and linked files) are not touched. |
| Supervisor search | No search-then-decide loop yet. Drafting gets a "known facts" briefing instead; the search tool exists and can be added to a Supervisor menu later with one line. |

### Out of scope

- `run_targeted_search` and `query_powerbi` (later specs).
- Facts from the user's uploaded files directly (facts come only from research reports).
- Meaning-based ("semantic") search — search is plain word matching.
- Using facts in the Phase 7 options report (possible later).
- Any change to the review limits in `docs/TO-TEST.md` / `services/review_limits.py`.
- Any change to the Insights phase write-ups, their version history, or linked files.

## 2. Storage — migration `0007` (additive)

Four new tables. No existing table changes.

**`cited_sources`** — a web page facts cite. One row per page per project.
- `id`, `project_id` (FK projects), `url` (≤2000, `http`/`https` only), `title` (≤300), `publisher` (≤200, optional), `published_date` (≤40, optional free text), `created_at`.
- `UNIQUE(project_id, url)`.

**`facts`** — one cited claim.
- `id`, `project_id`, `phase_key`, `claim` (≤500), `claim_key` (normalised claim: lower-cased, whitespace collapsed, trailing punctuation dropped), `quote` (≤500, optional), `cited_source_id` (FK cited_sources, nullable — a fact may cite only the report), `as_of` (≤40, optional, e.g. "2026"), `research_work_item_id` (FK research_work_items), `run_id` (FK research_runs, nullable), `status` (`active` | `rejected`), `created_at`, `updated_at`.
- `UNIQUE(project_id, claim_key)`.

**`conclusions`** — a short cited statement for a phase.
- `id`, `project_id`, `phase_key`, `text` (≤600), `research_work_item_id`, `status` (`active` | `rejected`), `created_at`, `updated_at`.

**`conclusion_facts`** — `conclusion_id`, `fact_id`, `PRIMARY KEY (conclusion_id, fact_id)`.

**`research_runs.facts_extracted_at`** — the one added column (nullable text), used as an atomic "extraction in progress / done" claim, so neither a double-click nor the automatic step plus a click can pay twice for the same report (§4.1, §4.3).

New repository `db/repositories/evidence_repo.py` holds all SQL for these tables. Tools never touch SQL directly (4a safety rule).

## 3. The tools

All go through the 4a door (`run_tool`): validated inputs, project scoping of ids, structured results and failure codes, a `tool_calls` log row. Plain-English names in the UI are "facts" and "conclusions"; tool names follow the roadmap.

| Tool | Does | Callers |
|---|---|---|
| `save_source` | Records a cited page; the same URL again returns the existing row. | supervisor, system |
| `save_evidence` | Saves a fact for a card (phase taken from the card). The same claim (same `claim_key`) returns the existing fact rather than a duplicate. | supervisor, system |
| `create_finding` | Saves a conclusion citing 1–10 facts; each fact must belong to this project and be active. | supervisor, system |
| `record_research_facts` | Saves a batch from one report: up to 25 facts and up to 3 conclusions, via child calls to the three tools above. Checks each item on its own (§4.2). | supervisor |
| `search_existing_evidence` | Word search over **active** facts (claim, quote, page title), optional phase filter, at most 20 results, newest first. Read-only. | supervisor, user, system |
| `update_evidence_status` | Reject or restore a fact or a conclusion. | **user only** |
| `extract_research_facts` | One paid Claude call that only extracts facts and conclusions from a finished card's report: run by the system right after a review (§4.1), and by the "Extract facts" button (§4.3). | **user, system** (never the Supervisor) |

### 3.1 Inputs (key fields and limits)

- `save_source`: `url` (http/https, ≤2000), `title` (≤300), `publisher?` (≤200), `published_date?` (≤40) → `{source_id, created}`.
- `save_evidence`: `task_id` (CardId), `claim` (1–500), `quote?` (≤500), `source_id?` (scoped to project), `as_of?` (≤40), `run_id?` → `{fact_id, created}`.
- `create_finding`: `task_id` (CardId), `text` (1–600), `fact_ids` (1–10 ids, scoped, active) → `{conclusion_id}`.
- `record_research_facts`: `task_id` (CardId), `facts` (≤25 of `{claim, quote?, source_url?, source_title?, publisher?, published_date?, as_of?}`), `conclusions` (≤3 of `{text, fact_numbers: 1-based positions in this batch}`) → `{fact_ids, conclusion_ids, skipped: [{item, reason}]}`.
- `search_existing_evidence`: `query?` (≤200), `phase_key?`, `limit` (1–20, default 10) → `{facts: [{id, phase_key, claim, quote, as_of, source: {url, title}, card_title}]}`.
- `update_evidence_status`: `kind` (`fact` | `conclusion`), `id` (scoped), `action` (`reject` | `restore`) → `{id, status}`.
- `extract_research_facts`: `task_id` (CardId) → `{fact_ids, conclusion_ids, skipped}`.

## 4. How facts get made and used

### 4.1 Automatically, right after the review

- The review stays exactly as it is today. `evaluate_research_output` gets no new fields, its `max_tokens` stays at 2000, and its prompt is unchanged. It sees only the first 8,000 characters of a report (`services/review_limits.py`, unchanged), which is too little to mine facts from.
- After a review succeeds, the system makes **one** extraction call on the same card, as long as the outcome is anything except `FAILED`. It is the same path as the "Extract facts" button (§4.3): `run_tool("extract_research_facts", "system", ...)`.
  - The call reads up to `MAX_EXTRACT_REPORT_CHARS = 60000` characters of the report.
  - It answers with `record_research_facts`, the only tool on `EXTRACT_MENU`.
  - It uses the same atomic run claim (`facts_extracted_at`). The claim is released when nothing is saved or the call fails, so the button stays available for that report.
- The extraction never affects the review. The review's result, the card's status, its scores and its follow-up stand exactly as the review set them, whatever happens to the extraction. A failure is logged (the tool-call log, plus a printed line), and the review's return value keeps its shape, with an added `extraction` summary key.
- If the run is already claimed (a click on "Extract facts" got there first), the automatic step makes no Claude call.
- Synthesis (Phase 7 options report) cards are never mined for facts, because their content is derived from other research, not sourced. A `FAILED` review triggers no extraction.
- Cost: roughly 10–15 cents more per reviewed report, for the second call. The review itself costs the same as before.
- The model's answer is cleaned before any tool sees it, as `_clean_review_input` is cleaned for reviews:
  - leaked `<parameter>` markup is recovered (as fixed in c42f1c6);
  - a list sent as one string is parsed, and a list cut short keeps its complete items;
  - one object becomes a list, and a fact whose fields spilled to the top level is rebuilt;
  - lists are cut to 25 facts and 3 conclusions, and items that are not objects are dropped.

### 4.2 Checking each item on its own

`record_research_facts` saves items one at a time:
- Text fields are clipped to their limits first, so length alone never rejects an item.
- A fact with no claim is skipped. A fact whose source address is not http/https is saved **without** the source. Both are recorded in `skipped` with a reason.
- A duplicate claim returns the existing fact (counts as saved, not skipped).
- A conclusion keeps the facts that were saved; if none are left, it is skipped.
- The batch result and the log show exactly what was saved and what was skipped.

### 4.3 The "Extract facts" button

- Shown on a card that is COMPLETE, FOLLOW_UP_REQUIRED or WAITING_FOR_HUMAN, whose latest run has a report, is not a synthesis card, and whose run has not had facts extracted (`facts_extracted_at` empty).
- The confirm dialog says it makes one paid Claude call.
- `extract_research_facts` is called by the user for the button, and by the system for the automatic step in §4.1. It refuses the Supervisor.
  - It first claims the run atomically: `facts_extracted_at` is set only if it is empty. A second click, or a click after the automatic step, gets "already extracting / done".
  - It then calls Claude with a fixed extraction prompt and a menu holding only `record_research_facts` (`EXTRACT_MENU`).
  - If the Claude call fails, or nothing is saved, the claim is cleared so the user can try again.
- It changes nothing else about the card: no scores, no status change.
- Once facts have been saved from a run, whether by the automatic step or by the button, the button disappears for that run.

### 4.4 Not paying twice — the drafting briefing

- `get_project_state` adds `known_facts`: per phase, the count of active facts and up to 10 most recent claims (each clipped to 200 characters).
- `format_briefing` adds a `# KNOWN FACTS` section with its own budget constant (`MAX_KNOWN_FACTS_CHARS = 4000`, in `supervisor_service`, not `review_limits`) placed before the existing overall cap, so known facts cannot push other briefing content out unexpectedly.
- The drafting prompt adds: don't propose research for facts already listed; aim at the gaps.

### 4.5 Activity feed

- "Supervisor saved 18 facts and 2 conclusions from \"<card>\"." (skipped count added when non-zero). Both the automatic step and the button record this line.
- "You rejected a fact." / "You restored a conclusion." / "You extracted facts from \"<card>\"." The last of these is for the button only; the automatic step does not record it.

## 5. The Insights screen

New file `static/evidence.js` (rendering kept out of the already-large `app.js`); `app.js` calls it when a phase renders.

- Per phase, below the write-up: **Conclusions** (sentence, "based on facts 2, 5, 7" linking to the facts, Reject) then **Facts** (claim, source link opening in a new tab, as-of date, "from: <card>", Reject). Rejected items sit under "Show rejected (n)" with Restore. A conclusion citing a rejected fact shows "1 of its facts was rejected".
- A search box at the top of Insights searches facts across phases (`search_existing_evidence`).
- All AI and web text is inserted as text, never HTML. Links are rendered only for `http`/`https` URLs, with `rel="noopener noreferrer"`.
- Routes (new blueprint `routes/evidence.py`): `GET /api/evidence?phase=`, `GET /api/evidence/search?q=&phase=`, `POST /api/evidence/<kind>/<id>/<reject|restore>`. The button: `POST /api/board/cards/<id>/extract-facts` (Research tab), plus the same button in the card's report reader.

## 6. Safety rules (carried from 4a)

- No tool takes a file path, SQL, or table name; the 4a structure tests extend to the new tools and the new callers table.
- The Supervisor can save facts, sources and conclusions only through `record_research_facts` on `EXTRACT_MENU`. It cannot reject, restore, delete, or start the paid extraction.
- `extract_research_facts` (callers: user, system) and `update_evidence_status` (callers: user) refuse the Supervisor. The review menu is unchanged.
- Nothing is ever deleted; reject is a status.

## 7. Testing

- **Tools:** each tool's validation, project scoping, duplicate handling (same URL, same claim), callers, and log rows.
- **Batch:** a bad fact skipped with a reason while the rest save; a bad source address saved without the source; a conclusion losing a skipped fact; a conclusion with no facts left skipped.
- **Automatic extraction after a review:**
  - A successful review is followed by one extraction call that saves facts, records "Supervisor saved…", and sets `facts_extracted_at`.
  - A `FAILED` review triggers no extraction, and neither does a synthesis card.
  - When an extraction fails or saves nothing, the reviewed card's status, scores and follow-up stay exactly as the review set them, and the claim is released.
  - A review that finishes while the run is already claimed makes no second Claude call.
  - The review call itself is unchanged: same tool, no new fields, `max_tokens` 2000.
  - Jumbled extraction answers are recovered: leaked `<parameter>` markup, facts sent as one string, a list cut short, spilled fields.
- **Button:**
  - Extraction saves facts without changing status or scores.
  - A second click does not call Claude, and neither does a click after a successful automatic extraction.
  - A failed Claude call clears the claim.
- **Briefing:** known facts per phase appear, rejected facts don't, the section respects its budget.
- **Screens:** Node tests for `evidence.js` rendering (text not HTML, only http/https links, rejected section, "based on facts" links).
- **Structure:** the 4a acceptance tests updated with the new tools and callers.
- **End to end:** browser walkthrough on stubbed AI services, then one real run on the user's approval.

## 8. Build order (for the plan)

1. Migration `0007` + `evidence_repo`.
2. `save_source`, `save_evidence`, `create_finding`, `search_existing_evidence`, `update_evidence_status`.
3. `record_research_facts` with per-item checking.
4. `extract_research_facts` (callers user and system), with the extraction call (`EXTRACT_MENU`, clean-up of the answer, run claim), run automatically after every non-`FAILED` review.
5. The "Extract facts" button (board state, route, card and report reader).
6. Known-facts briefing for drafting.
7. Routes + `static/evidence.js` + Insights hook + Activity text.
8. Structure/acceptance tests, docs, browser walkthrough.
