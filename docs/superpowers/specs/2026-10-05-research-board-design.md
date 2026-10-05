# Research Board — Design

**Date:** 2026-10-05
**Status:** Approved in conversation; awaiting written-spec review
**Mockup:** `docs/mockups/research-board.html` (approved 2026-10-04: "I love it")
**Builds on:** Phase 2 (`2026-10-04-research-task-state-design.md`), Phase 3 (`2026-10-04-research-supervisor-design.md`)

## 1. Goal

Merge the Prompt Developer and the Research Supervisor into one screen, the **Research** tab, where:

- the Supervisor drafts researches as cards, each carrying a full research prompt drafted from that phase's expert framework;
- the user edits, approves or skips each card;
- **nothing runs until the user approves it and presses Run**;
- results and the Supervisor's review land on the same card, and follow-ups arrive as new drafts that also need approval.

### Decisions already made (by the user)

| Topic | Decision |
|---|---|
| Approval | Always required before a run. The Supervisor never starts research. |
| Engine | OpenAI deep research (web) and Claude file analysis only. A Claude web-research engine is a separate later project; no engine dropdown in this build. |
| Ad-hoc research | "+ Add research" on each phase. The Prompt Developer's "Deep Research Generator" and "Web Scraping" utility templates are retired. |
| Results | Each finished report is saved as a source file and linked to its phase. Insights summaries still only regenerate when the user presses Refresh on Insights. |
| Supervisor triggers | Drafting on click ("Draft next researches"); reviewing automatic when a research finishes. |
| Placement | A new **Research** tab in the existing app, replacing the Prompt Developer tab. The Insights tab returns to findings only. |

### Out of scope

- Claude web-research engine (own spec later).
- The supervisor report-visibility experiment (`docs/TO-TEST.md`). The review limits (`MAX_RUN_OUTPUT_REVIEW_CHARS`, `MAX_REVIEW_OUTPUT_TOTAL_CHARS`, `MAX_CONTEXT_CHARS`, `MAX_WEB_SOURCES_LISTED`) are **not changed**.
- Any change to how Insights phase summaries are generated (Refresh / Refresh All).
- Dark mode (the app has none).

## 2. Card lifecycle and the approval rule

A card is one row of `research_work_items`. Status names are unchanged; the board labels them:

| Card label | Status | Who moves it there |
|---|---|---|
| Needs your approval | `PROPOSED` | Supervisor drafting; user (Add research, Restore, Back to draft) |
| Approved, waiting for Run | `READY` | **User only** (Approve, Retry) |
| Running | `RUNNING` | **User only** (Run → Confirm) |
| Being reviewed | `REVIEWING` | System (review claim, §4.3) |
| Finished | `COMPLETE` | Supervisor review; user (Accept) |
| Finished, follow-up suggested | `FOLLOW_UP_REQUIRED` | Supervisor review; user (Needs follow-up) |
| Needs your review | `WAITING_FOR_HUMAN` | Supervisor review |
| Failed | `FAILED` | System (start error, run error); Supervisor review; user (Failed) |
| Skipped | `SKIPPED` | User only |

### 2.1 Transition table changes

Added edges:

| Edge | Used by |
|---|---|
| `READY → PROPOSED` | user "Back to draft", or saving an edit to an approved card |
| `SKIPPED → PROPOSED` | user "Restore" |
| `FAILED → PROPOSED` | user "Back to draft" on a failed card (to edit it) |
| `REVIEWING → FAILED` | Supervisor review outcome "Failed" (the review claim moves the card to REVIEWING first) |
| `REVIEWING → RUNNING` | system only: releasing a review claim when the review call errors or the claim is stale (§4.3) |

Unchanged guards still apply: READY requires dependencies COMPLETE/SKIPPED; FAILED→READY requires `retry_count < max_retries`; COMPLETE from RUNNING/REVIEWING is blocked when `human_review_required`; WAITING_FOR_HUMAN requires the flag.

`FOLLOW_UP_REQUIRED → READY` stays in the table but nothing on the board uses it: a follow-up **does not re-run the original**. The original keeps its report and its "follow-up suggested" state; the follow-up is a separate card.

### 2.2 The approval rule (server-enforced)

1. The Supervisor has **no** tool that moves a card to `READY` or `RUNNING`. `mark_ready`, `dispatch_task`, `skip_task` and `trigger_synthesis` are removed from its tool set.
2. Only the board's user endpoints move cards to `READY` (Approve, Retry) or `RUNNING` (Run). The board API never accepts a raw target status.
3. Run receives the list of card ids the user confirmed. Each is started only if it is **still `READY`**, using a conditional update (`UPDATE … SET status='RUNNING' WHERE id=? AND status='READY'`), so a double-click or a second tab cannot start a card twice, and a card edited after the confirm (now `PROPOSED`) is not started.
4. Saving an edit to a `READY` card moves it to `PROPOSED`. Only `PROPOSED` and `READY` cards are editable.
5. Approve refuses a card with an empty title, no method, or a prompt shorter than 40 characters.
6. The generic `POST /api/research-tasks/<id>/transition` endpoint (which could set `RUNNING` without starting anything) is retired along with the Insights panels that used it.

## 3. Data model — migration `0005` (additive only)

```sql
ALTER TABLE research_work_items ADD COLUMN prompt_text TEXT;          -- exactly what gets sent
ALTER TABLE research_work_items ADD COLUMN framework_key TEXT;        -- e.g. 'oes-marketing-website'
ALTER TABLE research_work_items ADD COLUMN rationale TEXT;            -- "why proposed"
ALTER TABLE research_work_items ADD COLUMN suggested_from_work_item_id INTEGER REFERENCES research_work_items(id);
ALTER TABLE research_runs ADD COLUMN prompt_text TEXT;                -- full prompt as sent (prompt_preview is clipped to 200)
ALTER TABLE research_runs ADD COLUMN report_stable_file_id TEXT;      -- the saved report source file
```

No earlier migration is edited. Existing rows get NULLs; the board treats a card with no `prompt_text` as "needs a prompt" (cannot be approved until one is written or drafted).

`research_method` values: `TARGETED_WEB` (label "Web research"), `FILE_ANALYSIS` ("My files"), and new `SYNTHESIS` (Phase 7 options report only). "Focus" on the card is the existing `entities_json`.

## 4. Supervisor

`services/supervisor_service.py` is reshaped into two narrow jobs. `run_supervisor_cycle`, the single "Run Supervisor" button and `POST /api/supervisor/run` are removed. Every Supervisor call is still recorded in `agent_decisions`.

### 4.1 Drafting — "Draft next researches"

- Context: objective, phase definitions (with each phase's framework names), every card with status, focus, rationale and review scores/gaps (including `SKIPPED` ones, so it does not re-propose them), and recent decisions. Same `MAX_CONTEXT_CHARS` clipping as today.
- Tools: `propose_tasks` and `no_action` only; `tool_choice={"type":"any"}`.
- `propose_tasks` per task: `phase_key`, `title`, `focus` (list), `research_method` (`TARGETED_WEB` | `FILE_ANALYSIS`), `framework_key` (must be one of that phase's frameworks; defaults to the phase's first), `rationale`, optional `priority`, optional dependency fields (kept from Phase 3). Phase 7 is refused here (it has its own flow, §5.4).
- Each created card is `PROPOSED`, then its prompt is drafted (§4.2).

### 4.2 Prompt drafting from the phase frameworks

- The ten OES phase frameworks (`PROMPT_TEMPLATES` in `static/app.js`: 1, 2, 3a, 3b, 3c, 4, 5a, 5b, 6, 7) move **verbatim** to a new server module `services/prompt_frameworks.py`, keyed as today, each tagged with its phase. The two utility templates are not ported.
- `services/prompt_drafting_service.draft_prompt(project, card)` makes one Claude call: the framework's text as the system prompt, and as inputs the project objective, card title, focus and rationale, with the same output rules and budget-section stripping as today's `/api/prompt-dev` (that logic moves here).
- On failure the card still gets a simple fallback prompt (title + focus + objective) and a visible note: "Couldn't draft from the framework. Edit before approving." Drafting failure never blocks card creation.
- Used by: Supervisor drafting, "Re-draft from framework" on a card, and "+ Add research" when the user ticks "Draft the prompt for me".

### 4.3 Reviewing — automatic

- Triggered by the board's refresh (§5.2) for each card that is `RUNNING` and whose latest run is finished with output text.
- **Claim first:** conditional update `RUNNING → REVIEWING` (`WHERE status='RUNNING'`); if no row changed, another request already claimed it and this one skips it. A claim older than 10 minutes (process died mid-review) is released back to `RUNNING` on the next refresh.
- One Claude call per card with a single forced tool, `review_outcome` (`tool_choice={"type":"tool","name":"review_outcome"}`): `completeness_score`, `evidence_score`, `identified_gaps`, `outcome` ∈ `COMPLETE | FOLLOW_UP_REQUIRED | NEEDS_HUMAN | FAILED`, `reason`, and optional `followup` {`title`, `focus`, `research_method`, `rationale`}.
- The reviewer sees the objective, the card, its prompt, and the run output formatted and clipped **exactly as today** (same constants and `_format_web_research_output`).
- Outcomes: `COMPLETE` → COMPLETE. `FOLLOW_UP_REQUIRED` → FOLLOW_UP_REQUIRED, plus, if `followup` given, a new `PROPOSED` card in the same phase with `suggested_from_work_item_id` set and its prompt drafted (§4.2). `NEEDS_HUMAN` → set `human_review_required`, → WAITING_FOR_HUMAN. `FAILED` → FAILED.
- If the review call errors: release the claim (`REVIEWING → RUNNING`); the card shows "Review didn't complete, will retry"; the next refresh retries.
- No dependency edges are created by follow-ups (the original is not re-run, §2.1).

## 5. Running and results

New `services/research_execution_service.py` holds what used to be the Supervisor's dispatch, now user-triggered only.

### 5.1 Run

- `start_runs(project, card_ids)`: for each id, the conditional `READY → RUNNING` claim (§2.2), create a `research_runs` row linked to the card with the full `prompt_text`, then:
  - `TARGETED_WEB`: `openai_service.start_deep_research(card.prompt_text)`; store `response_id`.
  - `FILE_ANALYSIS`: run in a background thread (with app context): Claude reads the selected source files using the card's prompt as the instruction (existing file-selection and size limits); store `output_text`, status `completed`.
  - `SYNTHESIS`: §5.4.
- A card that fails to start goes to FAILED with the error on the run; the others still start. The response lists started / skipped (not READY any more) / failed ids.
- The thread entry point is a plain function so tests call it synchronously.

### 5.2 Board refresh

`POST /api/board/refresh` (called on opening the tab and every 15 s while any card is RUNNING or REVIEWING, **only while the Research tab is visible**):
1. Sync web runs from OpenAI (`sync_web_research_runs`, moved from the Supervisor module, unchanged behaviour).
2. For each newly finished run, save its report (§5.3).
3. Run failed → card `RUNNING → FAILED`.
4. Review each claimable card (§4.3).
5. Return the board state.

### 5.3 Report → source file → phase link

- When a run completes with text, the report is saved with `save_project_file` + `ensure_file_id` as `Research P{phase} - {title} ({YYYY-MM-DD}).md` (sanitised; a numeric suffix on collision). Content: title, phase, method, date, the prompt sent, then the report (web reports with their sources list, as formatted today).
- The file's stable id is stored on the run (`report_stable_file_id`) and **linked to the card's phase** by loading the current insights, adding the file to that phase's `linked_file_ids`/`linked_files`, and saving via `insights_service.save_insights` — the same effect as clicking Link today (a new Insights version). The version's `generated_at` is carried over unchanged so it still reflects when summaries were last generated.
- Saving happens once per run (guarded by `report_stable_file_id`).
- **Stale-overwrite guard:** the browser holds insights in memory and saves the whole object. The Insights tab therefore reloads insights from the server whenever it is shown, and board polling only runs while the Research tab is visible, so a server-side link is never overwritten by an older in-memory copy.

### 5.4 Phase 7 — options report

- Locked until each of Phases 1–6 has at least one `COMPLETE` card (same rule as today's `trigger_synthesis` gate). The board shows "N of 6 ready".
- When unlocked: **Draft options report** creates a `PROPOSED` Phase 7 card, method `SYNTHESIS`, prompt drafted from the Phase 7 framework (`oes-options-whitespace`).
- Approve + Run: Claude writes the report using the card's prompt as the instruction and the **current Insights summaries for Phases 1–6** as input (today's synthesis input); the result is saved into Insights Phase 7 (as `trigger_synthesis` does today) and kept on the card's run. The card goes straight to COMPLETE; the Supervisor does not review it.
- The card warns when the Insights summaries are older than the newest finished report ("Refresh Insights first to include your latest research").

## 6. The Research tab

Follows the mockup, using the app's existing fonts and OES colours (Ink `#001738`, Valencia `#FF8A00`, Sky `#82CBD4`).

- **Header:** "Research plan", project name, progress steps Objective → Plan → Run → Findings.
- **Objective panel:** the project objective, editable in place (same field as the Objective tab; both stay in sync). Supervisor note and **Draft next researches** button (disabled while drafting).
- **Filter chips:** All, Needs approval (PROPOSED), Approved (READY), Running (RUNNING, REVIEWING), Finished (COMPLETE, FOLLOW_UP_REQUIRED); plus Needs your review (WAITING_FOR_HUMAN) and Failed (FAILED) when any exist. "All" excludes SKIPPED, which has its own list.
- **Phases 1–7**, collapsible, each with a summary line, its cards and **+ Add research** (phase, title, method, focus, rationale, prompt or "Draft the prompt for me"). Phase 7 shows its lock or its options-report flow.
- **Card by status:**
  - PROPOSED: prompt preview, "Suggested after reviewing …" when a follow-up, *Edit and approve*, *Skip*. Edit form: title, method, prompt (monospace, "This text is exactly what gets sent", "Drafted from the Phase N — <framework> framework"), focus, why; *Approve*, *Re-draft from framework*, *Close*.
  - READY: prompt preview, *Edit* (un-approves), *Back to draft*.
  - RUNNING: pulsing state with **elapsed time** ("Running · 6 min"), not a percentage (OpenAI reports no progress).
  - REVIEWING: "Being reviewed".
  - COMPLETE / FOLLOW_UP_REQUIRED: review panel (completeness and evidence bars, gaps), *Read report* (full text in a reader), link to the follow-up card.
  - WAITING_FOR_HUMAN: review panel plus *Accept as finished*, *Needs follow-up* (creates a drafted follow-up from the gaps), *Mark failed*.
  - FAILED: the error, *Retry* (re-approves; disabled at the retry limit), *Back to draft*, *Skip*.
- **Skipped list** with *Restore*.
- **Right rail:** Run box (approved cards, *Run N approved researches* → confirm "They run on your OpenAI account, which bills for them") and **Activity** feed (Supervisor decisions and the user's board actions, both stored in `agent_decisions`; user actions with a `user_` decision type).
- Code: `static/board.js` (new file; `app.js` only gains the tab hook), styles in a clearly delimited block in `static/style.css` with an `rb-` class prefix.

### Removed

- Prompt Developer tab, its HTML, `PROMPT_TEMPLATES` and its functions in `app.js`, the "Research Runs" list and `/api/prompt-dev`. Past manual runs remain in Artifacts.
- The Insights task panels and Supervisor panel (Phase 2/3), `routes/supervisor.py`'s run endpoint, and the generic task-transition endpoint.

## 7. API

All under `/api/board`, all taking `project`, all returning `{"success": bool, …}`; errors return a user-readable `error`.

| Method & path | Purpose |
|---|---|
| `GET /api/board?project=` | Board state: objective, cards (with latest run summary, review, report file), phase summaries, Phase 7 readiness, activity |
| `POST /api/board/refresh` | §5.2; returns board state |
| `POST /api/board/draft` | Supervisor drafting (§4.1) |
| `POST /api/board/cards` | User-created card (+ Add research) |
| `PATCH /api/board/cards/<id>` | Edit title/prompt/focus/method/rationale (PROPOSED/READY only; READY → PROPOSED) |
| `POST /api/board/cards/<id>/<action>` | `approve`, `unapprove`, `skip`, `restore`, `retry`, `back-to-draft`, `redraft`, `accept`, `needs-followup`, `mark-failed` |
| `POST /api/board/run` | `{card_ids: [...]}` (§5.1) |
| `GET /api/board/cards/<id>/report` | Full report text |
| `POST /api/board/phase7/draft` | Draft the options report card |

A request acting on a card in the wrong state returns 409 with a message; the board reloads that card and shows the message.

## 8. Error handling

- External call failures never strand a card: start failure → FAILED with Retry; review failure → claim released, retried next refresh; drafting failure → fallback prompt with a note.
- Every board fetch checks `res.ok` and `success` before using the response (fixes a Phase 3 minor).
- Stale UI → 409 → reload card + message.
- Double review / double start prevented by conditional claims (§2.2, §4.3).

## 9. Testing

- **pytest**, with every Anthropic/OpenAI boundary mocked, using `temp_db` and `monkeypatch.chdir(tmp_path)` in any test that creates a project:
  - approval rule: no Supervisor tool handler can produce READY or RUNNING; Run starts only still-READY cards; editing READY → PROPOSED; approve validation;
  - new transition edges and unchanged guards;
  - drafting: tool set restricted to `propose_tasks`/`no_action`; framework selection and fallback; Phase 7 refused;
  - review: forced tool, claim/skip/release, each outcome, follow-up card creation;
  - execution: web start, file analysis worker, start failure isolation;
  - report save + phase link + `generated_at` preserved + saved once;
  - Phase 7 lock and synthesis;
  - board API happy paths and 409s; migration 0005 columns.
- **Browser check** (Claude in Chrome) against a throwaway project with external services stubbed: draft → edit → approve → Run → finish → review → follow-up draft, with screenshots. Then one real end-to-end run on a small test project **only with the user's go-ahead** (OpenAI cost).

## 10. Build order

1. Migration 0005; lifecycle edges and service functions.
2. Server-side frameworks and prompt drafting.
3. Execution service (run, file analysis, sync moved, report save + link).
4. Supervisor reshaped: drafting and automatic review.
5. Board API.
6. Research tab UI (`board.js`, styles) — alongside the old tab until it works.
7. Removal: Prompt Developer tab, Insights panels, retired endpoints; Insights reload-on-show.

The app works at every step; removal is last.
