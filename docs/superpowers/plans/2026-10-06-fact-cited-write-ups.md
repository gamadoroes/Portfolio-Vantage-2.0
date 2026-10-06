# Fact-cited Phase Write-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build each Insights phase write-up from that phase's checked facts and conclusions, cited as `[F#]` / `[C#]`, so every claim can be traced to a source and a date and rejected facts stay out. The citations carry through to the Word report and the Phase 7 options report.

**Architecture:**
- **Server briefing.** `evidence_service.phase_brief(project_id, phase_key)` builds a capped text briefing (facts newest first, conclusions, the rejected list, the citation rules). `GET /api/evidence/brief` serves it.
- **Generate is unchanged apart from one block.** `generatePhaseInsight` in `static/app.js` still builds the prompt in the browser and sends it through the existing chat route with the linked files. It now first asks `evidence.js` for the briefing and appends it when the phase has facts. A failed request adds nothing.
- **One Sources-list builder.** `evidence_service.cited_markers`, `citation_index` and `sources_lines` / `sources_markdown` are shared by the Word report (`docx_service.generate_insights_report`) and the options report (`research_execution_service.run_synthesis`).
- **Cards.** `static/evidence.js` already fetches every fact and conclusion of the project to fill the phase slots. With the same answer it now turns markers on the phase cards into in-page links, built as DOM nodes. `app.js` only marks the write-up box (`data-cite-phase`) and shows the "Not fact-cited" note.
- No migration, no new Claude call, no new dependency.

**Tech Stack:** Python 3.13, Flask, SQLite, python-docx, pytest, vanilla JS (Node for view tests).

**Spec:** `docs/superpowers/specs/2026-10-06-fact-cited-write-ups-design.md`

**Refinements to the spec made while planning (deliberate):**
- **The options report is not saved as a project file in today's code.** `run_synthesis` writes its text to Insights Phase 7 and to the run's `output_text`, which is what the Research tab's report reader shows. `_settle_card` never calls `save_report` for `SYNTHESIS` cards. So "the saved file" is read as the run's saved report: the Sources list is appended to `output_text` only. Insights Phase 7 keeps the plain text, because its card links the markers and the Word report adds its own Sources list (appending there too would print the list twice in Word). No new file is created.
- **"Its drafting instruction" is `SYNTHESIS_SYSTEM_PROMPT`**, the instruction every options-report run is written under. It is not the `oes-options-whitespace` framework in `services/prompt_frameworks.json`, which only drafts the card's editable prompt. The marker instruction is one constant, `evidence_service.KEEP_MARKERS_INSTRUCTION`. `app.js` repeats it word for word for Phase 7 on Insights, and a test keeps the two copies the same.
- **The budget goes to the rejected list first, then conclusions, then facts.** The spec says conclusions and the rejected list come before facts. Between the two of them, the "do not use" list gets the room first. Each section that is cut ends with `… n more … not shown`. Text order is unchanged: facts, conclusions, rejected, rules. An empty section prints `(none)`.
- **Missing parts in a line are left out.**
  - Brief: a fact without a page is just `[F12] claim`.
  - Sources: when there is no publisher, the page title is not printed twice: `F12 — Deakin fees (2026) — https://…`.
  - A fact without a page reads `F12 — Research report: <research title>`.
  - A rejected **conclusion** also gets `(since rejected)`, and an unknown conclusion reads `C9 — (not found)`.
- **The brief lists only active conclusions**, each with its active facts. Rejected conclusions are left out (the spec's rejected list holds fact claims only).
- `GET /api/evidence/brief` with no `phase` is a 400 with the same message as a bad phase ("Choose a phase from 1 to 7.").
- `phase_brief` takes a project **id**, as the spec says. The route converts with `projects_repo.get_id`.
- **Word report.**
  - The real phase loop is inline in `generate_insights_report`. `_add_phase_section` is unused dead code and stays untouched.
  - The "Not fact-cited" line goes above the phase text, under the Confidence line, as on the card.
  - `generate_insights_report` gains `citations=None`. The route looks them up for the report's project, validated with the route's existing `_existing_project_name`. For an unknown project every marker reads "(not found)".
  - `docx_service.py` is not ruff-clean today (5 auto-fixable import issues). Task 3 fixes them first.
- **Cards.**
  - The rejected tooltip text is "Rejected".
  - A link keeps the marker's own text (e.g. `[F007]` links to fact 7).
  - While a past Insights version is showing there are no phase slots, so nothing is fetched and markers stay plain text. The note still shows, because it depends on the text alone.
  - Markers inside existing links are left alone, like code.
- If the person switches project while the briefing loads, that Generate stops quietly (button reset). This stops a prompt built for one project from being sent under another.

## Global Constraints

- Markers are `[F<id>]` (fact) and `[C<id>]` (conclusion), `<id>` being the database id; `(not fact-checked)` marks a claim taken only from the reports. "Fact-cited" is derived from the text (at least one marker); no new column.
- **No migration.** Never edit `db/migrations/0001`–`0007`. `services/review_limits.py` is unchanged.
- `MAX_PHASE_BRIEF_CHARS = 12000` (in `services/evidence_service.py`). `len(brief["text"]) <= MAX_PHASE_BRIEF_CHARS` always.
- Brief route: `GET /api/evidence/brief?project=&phase=` → `{"success": true, "brief": {"text", "fact_count", "conclusion_count", "shown_facts"}}`; no project → 400 `{"success": false, "error": "No project selected."}`; missing or unknown phase → 400 `{"success": false, "error": "Choose a phase from 1 to 7."}`.
- Generate: when `fact_count > 0` the brief goes into the prompt after the phase guidance and before the user's instructions; when `fact_count == 0` or the request fails, the prompt is exactly as today. Linked files, streaming and saving are unchanged.
- Sources lines (verbatim formats): fact `F<id> — <publisher or page title> — <page title> (<as_of>) — <url>`, `(since rejected)` appended for a rejected fact, `F<id> — (not found)` for an id the project doesn't have; conclusion `C<id> — Conclusion: <text, clipped to 200 chars> (based on F…)`.
- Note text, everywhere: `Not fact-cited — written from the reports only.`
- Tests never call Anthropic or OpenAI (stub `llm_service.prompt_completion`). Any test that creates a project calls `monkeypatch.chdir(tmp_path)` first (the `client` fixture in `tests/test_routes_evidence.py` already does).
- Board routes, existing on-screen messages and HTTP status codes stay as they are. No change to who may call which tool; no Supervisor menu changes; no new Claude calls.
- User-facing text is plain English, with no tool names or codes.
- All model and web text goes into the DOM as text: `textContent` / text nodes in `evidence.js`, which never uses `innerHTML`, `outerHTML` or `insertAdjacentHTML`. Markers become **in-page** links only (`href="#ev-fact-<id>"` / `#ev-concl-<id>`), never to other addresses. The only HTML string added to `app.js` is the constant note, passed through `escapeHtml`.
- The brief and the Sources lookups are scoped to the current project.
- Ruff: every touched Python file must be clean (`python -m ruff check <files>`). Add no dependency.
- Baselines on `vantage-fresh`: `python -m pytest tests/ -q` 851 passed; `node tests/js/test_evidence_view.js` all 31 passed; `node tests/js/test_board_view.js` all 27 passed.
- Bash on Windows: the repo path contains `!!`, so start bash commands with `set +H;` or use PowerShell.
- Implementers never start the app (`python app.py`). The browser check in Task 7 is a controller step, run from the main checkout only, never from a worktree (a worktree run once wiped file records, because `projects/` was resolved from the start folder).

## Review Focus

1. **The person switches project while the briefing loads.** That Generate must not go out at all. Otherwise the old project's prompt would be sent and saved under the new one (Task 5 source test: `if (currentProject !== briefProject)`).
2. **A fact or conclusion is rejected or restored after the card was linked.** The card is not rebuilt. Its marker must turn struck through (or back) on the re-render, without being wrapped a second time (Task 6: "linking twice…" and "the controller … re-marks them after a reject").
3. **Hostile or odd model text around markers.** This covers `<img onerror>` beside a marker, markers inside code blocks or existing links, and claims or titles with line breaks and `#`. All of it stays text, no marker in code is linked, and no brief item can open a heading (Task 1 "one line" test; Task 6 code, markup and link tests).
4. **Markers that point nowhere.** An id this project doesn't have, another project's id, or `[f12]` / `[F]`. On the card it stays plain text, in Word and the options report it reads "(not found)", and it never appears in the brief (Task 1 scoping test, Task 2 sources test, Task 3 route test, Task 6 unknown-id test).
5. **A phase with very many facts, rejected facts or conclusions.** The brief must stay within 12,000 characters, keep the rules, keep the newest facts and say how many were cut (Task 1 budget tests, including a huge rejected list and oversized conclusions).

---

### Task 1: The facts briefing and its route

**Files:**
- Modify: `services/evidence_service.py` (add briefing constants and `phase_brief`), `routes/evidence.py` (add `GET /api/evidence/brief`)
- Create: `tests/test_evidence_citations.py`
- Test: `tests/test_routes_evidence.py` (append)

**Interfaces:**
- Consumes: `evidence_repo.list_facts(project_id, phase_key=None)` (rows oldest first, with `source_title`, `source_publisher`), `evidence_repo.list_conclusions(project_id, phase_key=None)`, `evidence_repo.conclusion_fact_links(project_id) -> {conclusion_id: [(fact_id, status), ...]}`; `routes.board._request_project`, `_no_project`; `routes.evidence._phase_filter`.
- Produces:
  - `evidence_service.MAX_PHASE_BRIEF_CHARS = 12000`, `REJECTED_HEADING = "# REJECTED — DO NOT USE"`, `BRIEF_RULES` (str, starts `"RULES:"`).
  - `evidence_service._flat(text) -> str` (one line; also used by Task 2).
  - `evidence_service.phase_brief(project_id, phase_key) -> {"text": str, "fact_count": int, "conclusion_count": int, "shown_facts": int}`. `project_id` may be `None` (empty brief).
  - `GET /api/evidence/brief?project=&phase=` → `{"success": True, "brief": {...}}`; 400s as in Global Constraints. `routes.evidence.CHOOSE_A_PHASE = "Choose a phase from 1 to 7."`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evidence_citations.py`:

```python
import pytest

from db.repositories import evidence_repo, projects_repo, research_work_items_repo
from services import evidence_service


@pytest.fixture
def pid(temp_db):
    return projects_repo.get_or_create_id("P")


def _card(pid, phase="1", title="Landscape"):
    return research_work_items_repo.create(pid, phase, title)


def _fact(pid, card, claim, phase="1", source=None, **kw):
    """source is (url, title, publisher)."""
    if source:
        kw["cited_source_id"] = evidence_repo.get_or_create_source(pid, *source)[0]
    return evidence_repo.get_or_create_fact(pid, phase, card, claim, claim.lower(), **kw)[0]


# ---- the facts briefing ----

def test_the_brief_lists_facts_newest_first_then_conclusions_then_rejected_then_rules(pid):
    card = _card(pid)
    old = _fact(pid, card, "Old claim", source=("https://a.example/p", "Page A", "Uni A"), as_of="2025")
    new = _fact(pid, card, "New claim")
    gone = _fact(pid, card, "Wrong claim")
    evidence_repo.set_fact_status(gone, "rejected")
    conclusion = evidence_repo.create_conclusion(pid, "1", card, "A conclusion", [old, new, gone])
    brief = evidence_service.phase_brief(pid, "1")
    assert (brief["fact_count"], brief["conclusion_count"], brief["shown_facts"]) == (2, 1, 2)
    assert brief["text"].split("\n") == [
        "# CHECKED FACTS FOR PHASE 1 (newest first)",
        f"[F{new}] New claim",
        f"[F{old}] Old claim — Page A, Uni A, as of 2025",
        "",
        "# CONCLUSIONS",
        f"[C{conclusion}] A conclusion (based on F{old}, F{new})",
        "",
        "# REJECTED — DO NOT USE",
        "- Wrong claim",
        "",
        *evidence_service.BRIEF_RULES.split("\n"),
    ]


def test_the_rules_say_how_to_cite(pid):
    rules = evidence_service.BRIEF_RULES
    assert rules.startswith("RULES:")
    for words in ("[F#]", "[C#]", "(not fact-checked)", "Never use a fact listed under REJECTED", "exactly as written"):
        assert words in rules


def test_a_phase_with_nothing_says_none_in_every_section(pid):
    brief = evidence_service.phase_brief(pid, "3")
    assert (brief["fact_count"], brief["conclusion_count"], brief["shown_facts"]) == (0, 0, 0)
    sections = brief["text"].split("\n\n")
    assert sections[:3] == ["# CHECKED FACTS FOR PHASE 3 (newest first)\n(none)", "# CONCLUSIONS\n(none)",
                            "# REJECTED — DO NOT USE\n(none)"]
    assert evidence_service.phase_brief(None, "3")["fact_count"] == 0     # a project with no database row


def test_rejected_facts_and_conclusions_never_appear_as_citable(pid):
    card = _card(pid)
    kept = _fact(pid, card, "Kept claim")
    gone = _fact(pid, card, "Gone claim")
    evidence_repo.set_fact_status(gone, "rejected")
    dropped = evidence_repo.create_conclusion(pid, "1", card, "Dropped conclusion", [kept])
    evidence_repo.set_conclusion_status(dropped, "rejected")
    brief = evidence_service.phase_brief(pid, "1")
    facts_section, conclusions_section, rejected_section = brief["text"].split("\n\n")[:3]
    assert f"[F{gone}]" not in brief["text"] and "Gone claim" not in facts_section
    assert "- Gone claim" in rejected_section
    assert "Dropped conclusion" not in brief["text"] and conclusions_section == "# CONCLUSIONS\n(none)"
    assert brief["conclusion_count"] == 0


def test_only_this_phase_and_this_project(pid):
    _fact(pid, _card(pid), "Phase one claim")
    _fact(pid, _card(pid, phase="2"), "Phase two claim", phase="2")
    other = projects_repo.get_or_create_id("Other")
    _fact(other, _card(other), "Other project claim")
    text = evidence_service.phase_brief(pid, "1")["text"]
    assert "Phase one claim" in text
    assert "Phase two claim" not in text and "Other project claim" not in text


def test_every_item_is_one_line_so_none_can_start_a_heading(pid):
    card = _card(pid)
    fact = _fact(pid, card, "Line one\n# Not a heading\n\nline   three",
                 source=("https://a.example", "Title\n# with break", None))
    gone = _fact(pid, card, "Rejected\n# heading")
    evidence_repo.set_fact_status(gone, "rejected")
    evidence_repo.create_conclusion(pid, "1", card, "Conclusion\n## heading", [fact])
    text = evidence_service.phase_brief(pid, "1")["text"]
    assert [line for line in text.split("\n") if line.startswith("#")] == [
        "# CHECKED FACTS FOR PHASE 1 (newest first)", "# CONCLUSIONS", "# REJECTED — DO NOT USE"]
    assert f"[F{fact}] Line one # Not a heading line three — Title # with break" in text


def test_a_long_fact_list_is_cut_newest_first_within_the_budget(pid):
    card = _card(pid)
    ids = [_fact(pid, card, f"Claim {n:03d} " + "x" * 150) for n in range(200)]
    gone = _fact(pid, card, "Rejected claim")
    evidence_repo.set_fact_status(gone, "rejected")
    evidence_repo.create_conclusion(pid, "1", card, "The conclusion", ids[:2])
    brief = evidence_service.phase_brief(pid, "1")
    text = brief["text"]
    assert len(text) <= evidence_service.MAX_PHASE_BRIEF_CHARS
    assert brief["fact_count"] == 200 and 0 < brief["shown_facts"] < 200
    facts_section = text.split("\n\n")[0].split("\n")
    shown = facts_section[1:-1]
    assert [line.split("]")[0] for line in shown] == [f"[F{i}" for i in reversed(ids)][:brief["shown_facts"]]
    assert facts_section[-1] == f"… {200 - brief['shown_facts']} more facts not shown"
    assert "The conclusion" in text and "- Rejected claim" in text and evidence_service.BRIEF_RULES in text


def test_conclusions_and_the_rejected_list_come_first_and_are_clipped_only_if_they_alone_are_too_long(pid):
    card = _card(pid)
    fact = _fact(pid, card, "A long fact " + "w" * 640)
    for n in range(40):
        evidence_repo.create_conclusion(pid, "1", card, f"Conclusion {n} " + "y" * 590, [fact])
    gone = _fact(pid, card, "Rejected claim")
    evidence_repo.set_fact_status(gone, "rejected")
    brief = evidence_service.phase_brief(pid, "1")
    text = brief["text"]
    assert len(text) <= evidence_service.MAX_PHASE_BRIEF_CHARS
    assert "- Rejected claim" in text                       # the rejected list is never squeezed out by conclusions
    assert "more conclusions not shown" in text
    assert text.split("\n\n")[0].split("\n")[1:] == ["… 1 more fact not shown"]
    assert brief["shown_facts"] == 0 and brief["conclusion_count"] == 40
    assert text.endswith(evidence_service.BRIEF_RULES)


def test_a_huge_rejected_list_is_clipped_and_the_brief_still_fits(pid):
    card = _card(pid)
    for n in range(100):
        gone = _fact(pid, card, f"Rejected {n:03d} " + "r" * 200)
        evidence_repo.set_fact_status(gone, "rejected")
    brief = evidence_service.phase_brief(pid, "1")
    assert len(brief["text"]) <= evidence_service.MAX_PHASE_BRIEF_CHARS
    rejected = brief["text"].split("\n\n")[2].split("\n")
    assert rejected[0] == "# REJECTED — DO NOT USE" and rejected[-1].endswith("more rejected facts not shown")
    assert brief["text"].endswith(evidence_service.BRIEF_RULES)
```

Append to `tests/test_routes_evidence.py` (it already has the `client` fixture, `_seed` and `_other_project`):

```python


# ---- the facts briefing for Generate ----

def test_the_brief_for_a_phase(client):
    _, data = _seed()
    first, second = data["fact_ids"]
    body = client.get("/api/evidence/brief?project=P&phase=4").get_json()
    assert body["success"] is True
    brief = body["brief"]
    assert (brief["fact_count"], brief["conclusion_count"], brief["shown_facts"]) == (2, 1, 2)
    assert brief["text"].startswith("# CHECKED FACTS FOR PHASE 4 (newest first)\n"
                                    f"[F{second}] Monash runs three intakes\n"
                                    f"[F{first}] Deakin charges $3,000 per unit — Deakin fees, as of 2026\n")
    assert client.get("/api/evidence/brief?project=P&phase=2").get_json()["brief"]["fact_count"] == 0


@pytest.mark.parametrize("query", ["", "&phase=", "&phase=9", "&phase=x"])
def test_the_brief_needs_a_phase_from_1_to_7(client, query):
    resp = client.get(f"/api/evidence/brief?project=P{query}")
    assert resp.status_code == 400
    assert resp.get_json() == {"success": False, "error": "Choose a phase from 1 to 7."}


def test_the_brief_with_no_project_is_400(client):
    resp = client.get("/api/evidence/brief?project=Nope&phase=4")
    assert resp.status_code == 400 and resp.get_json() == {"success": False, "error": "No project selected."}


def test_the_brief_never_includes_another_projects_facts(client):
    _other_project(client)
    text = client.get("/api/evidence/brief?project=P&phase=4").get_json()["brief"]["text"]
    assert "Deakin" in text and "Torrens" not in text and "Federation" not in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_evidence_citations.py tests/test_routes_evidence.py -v`
Expected: FAIL. `evidence_service` has no `phase_brief` / `BRIEF_RULES` (AttributeError), and `/api/evidence/brief` does not exist yet: Flask matches no route, so the brief tests get 404 / 405 instead of 200 / 400.

- [ ] **Step 3: Implement**

In `services/evidence_service.py`, replace the import line

```python
from db.repositories import evidence_repo, projects_repo
```

with

```python
from db.repositories import evidence_repo, projects_repo

# The facts briefing each phase's Generate gets (spec section 3): about 3,000 tokens at most.
MAX_PHASE_BRIEF_CHARS = 12000
# Kept back for each section's closing line ("… n more … not shown" or "(none)"), so a clipped brief still fits.
_CLOSING_LINE_ROOM = 60
REJECTED_HEADING = "# REJECTED — DO NOT USE"
BRIEF_RULES = (
    "RULES:\n"
    "- Cite every factual claim with the marker of the fact or conclusion it rests on: [F#] for a fact, "
    "[C#] for a conclusion. Several may sit together, e.g. [F26] [F28].\n"
    "- Mark anything taken only from the attached reports, and not from these facts, \"(not fact-checked)\".\n"
    "- Never use a fact listed under REJECTED — DO NOT USE.\n"
    "- Keep every marker exactly as written: square brackets, the letter and the number."
)
```

Append to the end of `services/evidence_service.py`:

```python


def _flat(text):
    """One line: line breaks and runs of spaces become single spaces, so no item can start a heading."""
    return " ".join(str(text or "").split())


def _fit(lines, room, one, many):
    """(shown, printed lines, characters used). Whole lines, in order, while they fit in `room` characters (each
    with its line break). When some are cut, "… n more <many> not shown" closes the list; an empty list prints
    "(none)". Closing lines are paid for from _CLOSING_LINE_ROOM, not from `room`."""
    if not lines:
        return 0, ["(none)"], 0
    shown, used = 0, 0
    for line in lines:
        if used + len(line) + 1 > room:
            break
        shown += 1
        used += len(line) + 1
    printed = lines[:shown]
    cut = len(lines) - shown
    if cut:
        printed.append(f"… {cut} more {one if cut == 1 else many} not shown")
    return shown, printed, used


def _brief_fact_line(row):
    details = [part for part in (_flat(row["source_title"]), _flat(row["source_publisher"])) if part]
    if row["as_of"]:
        details.append(f"as of {_flat(row['as_of'])}")
    line = f"[F{row['id']}] {_flat(row['claim'])}"
    return f"{line} — {', '.join(details)}" if details else line


def _brief_conclusion_line(row, links):
    active = [f"F{fact_id}" for fact_id, status in links if status == "active"]
    line = f"[C{row['id']}] {_flat(row['text'])}"
    return f"{line} (based on {', '.join(active)})" if active else line


def phase_brief(project_id, phase_key):
    """The facts briefing for one phase's Generate (spec section 3): the phase's active facts (newest first), its
    active conclusions, the rejected claims not to use, and the citation rules, within MAX_PHASE_BRIEF_CHARS.
    The rejected list and then the conclusions get the room first; facts fill the rest, newest first."""
    facts = evidence_repo.list_facts(project_id, phase_key)
    active = sorted((row for row in facts if row["status"] == "active"), key=lambda row: row["id"], reverse=True)
    rejected = [row for row in facts if row["status"] == "rejected"]
    conclusions = [row for row in evidence_repo.list_conclusions(project_id, phase_key) if row["status"] == "active"]
    links = evidence_repo.conclusion_fact_links(project_id) if conclusions else {}

    facts_heading = f"# CHECKED FACTS FOR PHASE {phase_key} (newest first)"
    fixed = (len(facts_heading) + len("# CONCLUSIONS") + len(REJECTED_HEADING) + len(BRIEF_RULES)
             + 3 * len("\n\n") + 3 * _CLOSING_LINE_ROOM)
    room = MAX_PHASE_BRIEF_CHARS - fixed
    _, rejected_lines, used = _fit([f"- {_flat(row['claim'])}" for row in rejected], room,
                                   "rejected fact", "rejected facts")
    room -= used
    conclusion_lines = [_brief_conclusion_line(row, links.get(row["id"], [])) for row in conclusions]
    _, conclusion_lines, used = _fit(conclusion_lines, room, "conclusion", "conclusions")
    room -= used
    shown, fact_lines, _ = _fit([_brief_fact_line(row) for row in active], room, "fact", "facts")
    text = "\n\n".join([
        "\n".join([facts_heading, *fact_lines]),
        "\n".join(["# CONCLUSIONS", *conclusion_lines]),
        "\n".join([REJECTED_HEADING, *rejected_lines]),
        BRIEF_RULES,
    ])
    return {"text": text, "fact_count": len(active), "conclusion_count": len(conclusions), "shown_facts": shown}
```

In `routes/evidence.py`:

1. Replace

```python
from flask import Blueprint, jsonify, request

from routes.board import _no_project, _request_project
```

with

```python
from flask import Blueprint, jsonify, request

from db.repositories import projects_repo
from routes.board import _no_project, _request_project
```

2. Replace `SEARCH_CHARS = 200` with

```python
SEARCH_CHARS = 200
CHOOSE_A_PHASE = "Choose a phase from 1 to 7."
```

3. In `_phase_filter`, replace `return None, (jsonify({"success": False, "error": "Choose a phase from 1 to 7."}), 400)` with `return None, (jsonify({"success": False, "error": CHOOSE_A_PHASE}), 400)`.

4. Insert this route just above `@evidence_bp.route("/api/evidence/<kind>/<int:item_id>/<action>", methods=["POST"])`:

```python
@evidence_bp.route("/api/evidence/brief", methods=["GET"])
def phase_brief():
    """The facts briefing Generate adds to a phase's prompt (spec section 3)."""
    project, _ = _request_project()
    if not project:
        return _no_project()
    phase, bad_phase = _phase_filter()
    if bad_phase:
        return bad_phase
    if phase is None:
        return jsonify({"success": False, "error": CHOOSE_A_PHASE}), 400
    return jsonify({"success": True, "brief": evidence_service.phase_brief(projects_repo.get_id(project), phase)})


```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_evidence_citations.py tests/test_routes_evidence.py -v`
Expected: all pass.
Then run `python -m ruff check services/evidence_service.py routes/evidence.py tests/test_evidence_citations.py tests/test_routes_evidence.py` (expected: clean) and `python -m pytest tests/ -q` (expected: all pass).

- [ ] **Step 5: Commit**

```bash
git add services/evidence_service.py routes/evidence.py tests/test_evidence_citations.py tests/test_routes_evidence.py
git commit -m "Add the facts briefing for a phase's Generate" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BboZJ8FCLubxrmK1HY4ZeX"
```

---

### Task 2: The shared Sources-list builder

**Files:**
- Modify: `services/evidence_service.py`
- Test: `tests/test_evidence_citations.py` (append)

**Interfaces:**
- Consumes: `evidence_service.fact_dict(row)` (existing: `{"id", "phase_key", "claim", "quote", "as_of", "status", "source": None | {"url", "title", "publisher", "published_date"}, "card_id", "card_title", "created_at"}`), `evidence_service._flat` (Task 1), `evidence_repo.list_facts`, `list_conclusions`, `conclusion_fact_links`, `projects_repo.get_id`.
- Produces:
  - `evidence_service.MARKER_RE` (compiled `\[([FC])(\d+)\]`), `SOURCE_CONCLUSION_CHARS = 200`.
  - `cited_markers(text) -> list[tuple[str, int]]`: `("F" | "C", id)`, distinct, in order of first appearance; non-strings give `[]`.
  - `citation_index(project_name) -> {"facts": {id: fact_dict}, "conclusions": {id: {"id", "text", "status", "fact_ids": [int, ...]}}}`: every status; unknown / falsy project → `{"facts": {}, "conclusions": {}}`.
  - `sources_lines(text, index) -> list[str]`: one line per cited item (formats in Global Constraints); `index=None` counts as empty.
  - `sources_markdown(text, index) -> str`: `"\n\n## Sources\n\n- line\n- line"`, or `""` when nothing is cited.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evidence_citations.py`:

```python


# ---- the Sources list ----

def test_markers_are_read_once_each_in_order_of_first_appearance():
    text = "A [F26] [F28] [F4]. B [C3] and [F26] again. [f5], [F], [X1] and F7 are not markers. [F007] is fact 7."
    assert evidence_service.cited_markers(text) == [("F", 26), ("F", 28), ("F", 4), ("C", 3), ("F", 7)]
    assert evidence_service.cited_markers("No markers here.") == []
    assert evidence_service.cited_markers(None) == []


def test_the_index_holds_every_fact_and_conclusion_of_the_project_whatever_its_status(pid):
    card = _card(pid)
    kept, gone = _fact(pid, card, "Kept"), _fact(pid, card, "Gone")
    evidence_repo.set_fact_status(gone, "rejected")
    conclusion = evidence_repo.create_conclusion(pid, "1", card, "Both", [kept, gone])
    other = projects_repo.get_or_create_id("Other")
    theirs = _fact(other, _card(other), "Theirs")
    index = evidence_service.citation_index("P")
    assert set(index["facts"]) == {kept, gone} and theirs not in index["facts"]
    assert index["facts"][gone]["status"] == "rejected"
    assert index["conclusions"] == {conclusion: {"id": conclusion, "text": "Both", "status": "active",
                                                 "fact_ids": [kept, gone]}}
    assert evidence_service.citation_index("Nope") == {"facts": {}, "conclusions": {}}
    assert evidence_service.citation_index(None) == {"facts": {}, "conclusions": {}}


def test_each_cited_item_gets_one_sources_line_in_order_of_first_appearance(pid):
    card = _card(pid)
    full = _fact(pid, card, "Fees", source=("https://deakin.example/fees", "Deakin fees", "Deakin University"),
                 as_of="2026")
    bare = _fact(pid, card, "No page", as_of="2025")
    titled = _fact(pid, card, "Titled", source=("https://b.example", "Page B", None))
    gone = _fact(pid, card, "Gone", source=("https://c.example", "Page C", "Uni C"))
    evidence_repo.set_fact_status(gone, "rejected")
    conclusion = evidence_repo.create_conclusion(pid, "1", card, "x" * 250, [full, bare])
    other = projects_repo.get_or_create_id("Other")
    theirs = _fact(other, _card(other), "Theirs")
    text = (f"[F{full}] a [C{conclusion}] b [F{bare}] [F{full}] [F{titled}] [F{gone}] [F99999] [F{theirs}] "
            "[C88888]")
    assert evidence_service.sources_lines(text, evidence_service.citation_index("P")) == [
        f"F{full} — Deakin University — Deakin fees (2026) — https://deakin.example/fees",
        f"C{conclusion} — Conclusion: {'x' * 199}… (based on F{full}, F{bare})",
        f"F{bare} — Research report: Landscape (2025)",
        f"F{titled} — Page B — https://b.example",
        f"F{gone} — Uni C — Page C — https://c.example (since rejected)",
        "F99999 — (not found)",
        f"F{theirs} — (not found)",
        "C88888 — (not found)",
    ]


def test_a_rejected_conclusion_says_so(pid):
    card = _card(pid)
    fact = _fact(pid, card, "A fact")
    conclusion = evidence_repo.create_conclusion(pid, "1", card, "Short\nconclusion", [fact])
    evidence_repo.set_conclusion_status(conclusion, "rejected")
    assert evidence_service.sources_lines(f"[C{conclusion}]", evidence_service.citation_index("P")) == [
        f"C{conclusion} — Conclusion: Short conclusion (based on F{fact}) (since rejected)"]


def test_no_markers_means_no_sources(pid):
    index = evidence_service.citation_index("P")
    assert evidence_service.sources_lines("Plain text.", index) == []
    assert evidence_service.sources_markdown("Plain text.", index) == ""
    assert evidence_service.sources_lines("[F1]", None) == ["F1 — (not found)"]


def test_the_sources_list_as_markdown(pid):
    fact = _fact(pid, _card(pid), "Fees", source=("https://a.example", "Page A", None))
    assert evidence_service.sources_markdown(f"Dear [F{fact}].", evidence_service.citation_index("P")) == (
        f"\n\n## Sources\n\n- F{fact} — Page A — https://a.example")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_evidence_citations.py -v`
Expected: the six new tests FAIL with `AttributeError: module 'services.evidence_service' has no attribute 'cited_markers'` (and `citation_index`, `sources_lines`, `sources_markdown`); the Task 1 tests still pass.

- [ ] **Step 3: Implement**

In `services/evidence_service.py`:

1. Make `re` the first import. The top becomes:

```python
import re

from db.repositories import evidence_repo, projects_repo
```

2. After the `BRIEF_RULES = (...)` block, add:

```python

# [F12] cites fact 12, [C3] conclusion 3 (spec section 2).
MARKER_RE = re.compile(r"\[([FC])(\d+)\]")
SOURCE_CONCLUSION_CHARS = 200
```

3. Append to the end of the file:

```python


def cited_markers(text):
    """The [F#] / [C#] markers in a write-up as (kind, id) pairs, each once, in order of first appearance."""
    seen, found = set(), []
    for kind, digits in MARKER_RE.findall(text if isinstance(text, str) else ""):
        key = (kind, int(digits))
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def citation_index(project_name):
    """Every fact and conclusion of the project by id, whatever its status: what a Sources list looks markers up
    in. An unknown project (or none) has nothing, so every marker in its text reads "(not found)"."""
    project_id = projects_repo.get_id(project_name) if project_name else None
    if project_id is None:
        return {"facts": {}, "conclusions": {}}
    links = evidence_repo.conclusion_fact_links(project_id)
    return {
        "facts": {row["id"]: fact_dict(row) for row in evidence_repo.list_facts(project_id)},
        "conclusions": {
            row["id"]: {"id": row["id"], "text": row["text"], "status": row["status"],
                        "fact_ids": [fact_id for fact_id, _ in links.get(row["id"], [])]}
            for row in evidence_repo.list_conclusions(project_id)
        },
    }


def _clip(text, limit):
    flat = _flat(text)
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def _fact_source_line(fact_id, fact):
    if fact is None:
        return f"F{fact_id} — (not found)"
    source = fact["source"] or {}
    title, publisher, card = _flat(source.get("title")), _flat(source.get("publisher")), _flat(fact["card_title"])
    parts = [f"F{fact_id}", publisher or title or (f"Research report: {card}" if card else "Research report")]
    if publisher and title:
        parts.append(title)
    if fact["as_of"]:
        parts[-1] += f" ({_flat(fact['as_of'])})"
    if source.get("url"):
        parts.append(_flat(source["url"]))
    line = " — ".join(parts)
    return f"{line} (since rejected)" if fact["status"] == "rejected" else line


def _conclusion_source_line(conclusion_id, conclusion):
    if conclusion is None:
        return f"C{conclusion_id} — (not found)"
    line = f"C{conclusion_id} — Conclusion: {_clip(conclusion['text'], SOURCE_CONCLUSION_CHARS)}"
    if conclusion["fact_ids"]:
        line += f" (based on {', '.join(f'F{fact_id}' for fact_id in conclusion['fact_ids'])})"
    return f"{line} (since rejected)" if conclusion["status"] == "rejected" else line


def sources_lines(text, index):
    """The Sources list for a write-up (spec section 6): one line per distinct cited fact or conclusion, in order of
    first appearance. `index` is citation_index(project); None counts as a project with nothing."""
    index = index or {}
    facts, conclusions = index.get("facts", {}), index.get("conclusions", {})
    return [
        _fact_source_line(item_id, facts.get(item_id)) if kind == "F"
        else _conclusion_source_line(item_id, conclusions.get(item_id))
        for kind, item_id in cited_markers(text)
    ]


def sources_markdown(text, index):
    """The same Sources list as a Markdown section to append to a saved report, or "" when nothing is cited."""
    lines = sources_lines(text, index)
    if not lines:
        return ""
    return "\n\n## Sources\n\n" + "\n".join(f"- {line}" for line in lines)
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_evidence_citations.py -v` (expected: all pass), then `python -m ruff check services/evidence_service.py tests/test_evidence_citations.py` (clean) and `python -m pytest tests/ -q` (all pass).

- [ ] **Step 5: Commit**

```bash
git add services/evidence_service.py tests/test_evidence_citations.py
git commit -m "Add the shared Sources list for fact-cited write-ups" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BboZJ8FCLubxrmK1HY4ZeX"
```

---

### Task 3: Sources lists and the "Not fact-cited" line in the Word report

**Files:**
- Modify: `services/evidence_service.py` (one constant), `services/docx_service.py`, `routes/ai.py` (`insights_report`)
- Create: `tests/test_docx_report.py`
- Test: `tests/test_routes_evidence.py` (append; two imports added)

**Interfaces:**
- Consumes: `evidence_service.cited_markers`, `sources_lines`, `citation_index` (Task 2); `routes.ai._existing_project_name(raw) -> str | None` (existing).
- Produces:
  - `evidence_service.NOT_CITED_NOTE = "Not fact-cited — written from the reports only."` (Task 6 checks `evidence.js` uses the same words).
  - `docx_service.generate_insights_report(data, project_name="Research Project", citations=None) -> io.BytesIO`. For each phase with a real summary, either a `NOT_CITED_NOTE` paragraph above the text (no markers), or a `Sources` paragraph and one paragraph per `sources_lines` line after the text.
  - `docx_service._add_citation_sources(doc, summary, citations)`.
  - `POST /api/insights/report` passes `citations=evidence_service.citation_index(_existing_project_name(project_name))`. Its request, response and errors are otherwise unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_docx_report.py`:

```python
from docx import Document

from services import docx_service, evidence_service

FACT_12 = {"id": 12, "status": "active", "as_of": "2026", "card_title": "Fees",
           "source": {"url": "https://deakin.example/fees", "title": "Deakin fees", "publisher": "Deakin University",
                      "published_date": ""}}
FACT_13 = {"id": 13, "status": "rejected", "as_of": "", "card_title": "Fees",
           "source": {"url": "https://old.example", "title": "Old page", "publisher": "", "published_date": ""}}
CITATIONS = {"facts": {12: FACT_12, 13: FACT_13},
             "conclusions": {3: {"id": 3, "text": "Deakin is the priciest", "status": "active", "fact_ids": [12]}}}


def _phase(title, summary):
    return {"title": title, "summary": summary, "confidence": "medium", "evidence_sources": [], "gaps": []}


def _report(phases, citations=CITATIONS):
    buf = docx_service.generate_insights_report({"competitors": [], "phases": phases}, "P", citations=citations)
    return [p.text for p in Document(buf).paragraphs]


def _section(texts, start, end=None):
    first = texts.index(start)
    return texts[first:texts.index(end)] if end else texts[first:]


def test_a_fact_cited_phase_ends_with_its_sources_in_order_of_first_appearance():
    texts = _report({"1": _phase("The Landscape", "Fees rose [F12]. Deakin leads [C3] [F12]. Old view [F13] [F99].")})
    phase = _section(texts, "Phase 1: The Landscape")
    assert any("Fees rose [F12]." in t for t in phase)          # the markers stay in the body text
    start = phase.index("Sources")
    assert phase[start + 1:start + 5] == [
        "F12 — Deakin University — Deakin fees (2026) — https://deakin.example/fees",
        "C3 — Conclusion: Deakin is the priciest (based on F12)",
        "F13 — Old page — https://old.example (since rejected)",
        "F99 — (not found)",
    ]
    assert evidence_service.NOT_CITED_NOTE not in phase


def test_a_phase_without_markers_gets_the_note_and_no_sources():
    texts = _report({"1": _phase("The Landscape", "Cited [F12]."),
                     "2": _phase("The Student", "Written from the reports."),
                     "3": _phase("Review of Marketing", "MISSING")})
    two = _section(texts, "Phase 2: The Student", "Phase 3: Review of Marketing")
    assert evidence_service.NOT_CITED_NOTE in two and "Sources" not in two
    assert two.index(evidence_service.NOT_CITED_NOTE) < two.index("Written from the reports.")   # above the text
    three = _section(texts, "Phase 3: Review of Marketing", "Data Completeness Overview")
    assert evidence_service.NOT_CITED_NOTE not in three and "Sources" not in three


def test_without_a_lookup_every_marker_is_not_found():
    texts = _report({"1": _phase("The Landscape", "Cited [F12].")}, citations=None)
    assert "F12 — (not found)" in texts
```

In `tests/test_routes_evidence.py`:

1. Replace the imports at the top

```python
import re
from pathlib import Path

import pytest
```

with

```python
import io
import re
from pathlib import Path

import pytest
from docx import Document
```

2. Replace `from services import research_task_service, tools` with `from services import evidence_service, research_task_service, tools`.

3. Append:

```python


# ---- the Word report ----

def test_the_word_report_lists_the_sources_of_this_projects_facts_only(client):
    mine, theirs = _other_project(client)
    deakin, monash = mine["fact_ids"]
    torrens = theirs["fact_ids"][0]
    insights = {"competitors": [], "phases": {
        "4": {"title": "Product Features", "summary": f"Deakin is dear [F{deakin}]. Torrens [F{torrens}]. [F{monash}]",
              "confidence": "medium"},
        "5": {"title": "Academic Content", "summary": "From the reports only.", "confidence": "low"},
    }}
    resp = client.post("/api/insights/report", json={"insights": insights, "project_name": "P"})
    assert resp.status_code == 200
    texts = [p.text for p in Document(io.BytesIO(resp.data)).paragraphs]
    start = texts.index("Sources")
    assert texts[start + 1:start + 4] == [
        f"F{deakin} — Deakin fees (2026) — https://deakin.example/fees",
        f"F{torrens} — (not found)",
        f"F{monash} — Research report: Fees",
    ]
    assert texts.count("Sources") == 1 and evidence_service.NOT_CITED_NOTE in texts
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_docx_report.py tests/test_routes_evidence.py -v`
Expected: FAIL. `generate_insights_report()` gets an unexpected keyword argument `citations`, `evidence_service` has no `NOT_CITED_NOTE`, and the route test finds no "Sources" paragraph (`ValueError: 'Sources' is not in list`).

- [ ] **Step 3: Implement**

1. In `services/evidence_service.py`, after `SOURCE_CONCLUSION_CHARS = 200`, add:

```python
NOT_CITED_NOTE = "Not fact-cited — written from the reports only."
```

2. `services/docx_service.py` is not ruff-clean today (unused `Inches`, `Emu`, `WD_ORIENT`, `qn`; unsorted imports). Run `python -m ruff check services/docx_service.py --fix` first. Its import block then reads:

```python
import io
import re
from datetime import datetime

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Cm, Pt, RGBColor
```

Add one line after it (blank line between):

```python

from .evidence_service import NOT_CITED_NOTE, cited_markers, sources_lines
```

3. In `services/docx_service.py`, just above the divider block

```python
# ---------------------------------------------------------------------------
# Rating visuals (filled/unfilled blocks)
# ---------------------------------------------------------------------------
```

add:

```python
def _add_citation_sources(doc, summary, citations):
    """After a fact-cited phase: a Sources heading and one line per cited fact or conclusion (spec section 6)."""
    lines = sources_lines(summary, citations)
    if not lines:
        return
    _para(doc, "Sources", size=10, bold=True, colour=CLR_PRIMARY_TEXT, space_before=8, space_after=2)
    for line in lines:
        _para(doc, line, size=8, colour=CLR_DARK_GREY, space_before=1, space_after=1)


```

4. Replace the signature and docstring

```python
def generate_insights_report(data, project_name="Research Project"):
    """Generate a professionally formatted Word document from insights JSON.

    Returns an io.BytesIO buffer containing the .docx file.
    """
```

with

```python
def generate_insights_report(data, project_name="Research Project", citations=None):
    """Generate a professionally formatted Word document from insights JSON.

    `citations` is evidence_service.citation_index(project): what each phase's [F#] / [C#] markers are looked up in
    for its Sources list. None counts as a project with no facts (every marker then reads "(not found)").

    Returns an io.BytesIO buffer containing the .docx file.
    """
```

5. In the phase loop of `generate_insights_report` (not in the unused `_add_phase_section`), replace

```python
        if not summary or summary == "MISSING":
            _para(doc,
                  "No data available for this phase. Use the Research Prompt feature in the dashboard "
                  "to generate targeted content for this section.",
                  size=9, italic=True, colour=CLR_DARK_GREY, space_before=8, space_after=12)
        else:
            _render_markdown_to_docx(doc, summary)
```

with

```python
        if not summary or summary == "MISSING":
            _para(doc,
                  "No data available for this phase. Use the Research Prompt feature in the dashboard "
                  "to generate targeted content for this section.",
                  size=9, italic=True, colour=CLR_DARK_GREY, space_before=8, space_after=12)
        else:
            if not cited_markers(summary):
                _para(doc, NOT_CITED_NOTE, size=8, italic=True, colour=CLR_DARK_GREY, space_before=2, space_after=6)
            _render_markdown_to_docx(doc, summary)
            _add_citation_sources(doc, summary, citations)
```

6. In `routes/ai.py`, add `from services import evidence_service` as the first `services` import. It must sit directly above `from services.chat_service import append_message_to_chat, ...`; ruff's isort wants it there. Then in `insights_report()` replace

```python
        buf = generate_insights_report(insights, project_name=project_name)
```

with

```python
        # Each phase's [F#] / [C#] markers are looked up in this project's facts only (spec section 6).
        citations = evidence_service.citation_index(_existing_project_name(project_name))
        buf = generate_insights_report(insights, project_name=project_name, citations=citations)
```

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_docx_report.py tests/test_routes_evidence.py -v` (expected: all pass). Then `python -m ruff check services/evidence_service.py services/docx_service.py routes/ai.py tests/test_docx_report.py tests/test_routes_evidence.py` (clean) and `python -m pytest tests/ -q` (all pass).

- [ ] **Step 5: Commit**

```bash
git add services/evidence_service.py services/docx_service.py routes/ai.py tests/test_docx_report.py tests/test_routes_evidence.py
git commit -m "List each phase's cited sources in the Word report" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BboZJ8FCLubxrmK1HY4ZeX"
```

---

### Task 4: The options report keeps the markers and lists its sources

**Files:**
- Modify: `services/evidence_service.py` (one constant), `services/research_execution_service.py`
- Test: `tests/test_research_execution_service.py` (append; imports widened)

**Interfaces:**
- Consumes: `evidence_service.sources_markdown`, `citation_index` (Task 2).
- Produces:
  - `evidence_service.KEEP_MARKERS_INSTRUCTION = "Keep the [F#] and [C#] citation markers from the phase write-ups exactly as written, next to each point you take from them."` (Task 5 repeats it word for word in `app.js`).
  - `research_execution_service.SYNTHESIS_SYSTEM_PROMPT` ends with `KEEP_MARKERS_INSTRUCTION`.
  - `research_execution_service._options_sources(project_name, text) -> str` (Markdown Sources section or `""`; never raises).
  - `run_synthesis`: the run's `output_text` = Claude's text + `_options_sources(...)`; Insights Phase 7 `summary` = Claude's text, unchanged. No markers → both identical to today.

- [ ] **Step 1: Write the failing tests**

In `tests/test_research_execution_service.py` replace

```python
from db.repositories import projects_repo, research_runs_repo, research_work_items_repo
from services import insights_service, llm_service, research_execution_service, research_task_service
```

with

```python
from db.repositories import evidence_repo, projects_repo, research_runs_repo, research_work_items_repo
from services import evidence_service, insights_service, llm_service, research_execution_service, research_task_service
```

and append:

```python


def _cited_phases(pid):
    """Phases 1-6 finished, a fact saved for Phase 4, and every phase write-up citing it. Returns the fact id."""
    _complete_phases_1_to_6(pid)
    card = research_work_items_repo.create(pid, "4", "Fees research")
    source, _ = evidence_repo.get_or_create_source(pid, "https://deakin.example/fees", "Deakin fees",
                                                   publisher="Deakin University")
    fact, _ = evidence_repo.get_or_create_fact(pid, "4", card, "Deakin charges $3,000", "deakin charges $3,000",
                                               cited_source_id=source, as_of="2026")
    insights_service.save_insights("P", {
        "generated_at": "2026-10-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
        "phases": {str(i): {"title": f"Phase {i}", "summary": f"Summary {i} [F{fact}]", "confidence": "high",
                             "evidence_sources": [], "gaps": [], "suggested_topics": [],
                             "linked_files": [], "linked_file_ids": []} for i in range(1, 8)},
    })
    return fact


def test_the_options_report_keeps_the_markers_and_its_saved_report_ends_with_its_sources(pid, monkeypatch):
    fact = _cited_phases(pid)
    seen = {}

    def fake_claude(system, user, max_tokens=4000):
        seen["system"], seen["user"] = system, user
        return f"Option A is cheap [F{fact}]."

    monkeypatch.setattr(llm_service, "prompt_completion", fake_claude)
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    research_execution_service.start_runs("P", [card_id])
    assert evidence_service.KEEP_MARKERS_INSTRUCTION in seen["system"]
    assert f"Summary 4 [F{fact}]" in seen["user"]
    assert research_runs_repo.find_latest_for_work_item(card_id)["output_text"] == (
        f"Option A is cheap [F{fact}].\n\n## Sources\n\n"
        f"- F{fact} — Deakin University — Deakin fees (2026) — https://deakin.example/fees")
    # Insights Phase 7 keeps the plain text: its card links the markers, and the Word report adds its own list.
    assert insights_service.load_current_insights("P")["phases"]["7"]["summary"] == f"Option A is cheap [F{fact}]."
    assert research_work_items_repo.get(card_id)["status"] == "COMPLETE"


def test_a_failed_sources_lookup_still_saves_the_options_report(pid, monkeypatch):
    fact = _cited_phases(pid)

    def broken(project_name):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(evidence_service, "citation_index", broken)
    monkeypatch.setattr(llm_service, "prompt_completion",
                        lambda system, user, max_tokens=4000: f"Option A [F{fact}].")
    card_id = _ready_card(pid, method="SYNTHESIS", phase="7", title="Options report")
    research_execution_service.start_runs("P", [card_id])
    assert research_runs_repo.find_latest_for_work_item(card_id)["output_text"] == f"Option A [F{fact}]."
    assert research_work_items_repo.get(card_id)["status"] == "COMPLETE"
```

(The existing `test_synthesis_writes_phase_7_and_completes_the_card` already pins that a report with no markers is saved exactly as written.)

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_research_execution_service.py -v`
Expected: `test_the_options_report_keeps_the_markers_and_its_saved_report_ends_with_its_sources` FAILS with `AttributeError: module 'services.evidence_service' has no attribute 'KEEP_MARKERS_INSTRUCTION'`. `test_a_failed_sources_lookup_still_saves_the_options_report` already passes, because nothing is appended yet. It is there to make sure the new list can never lose the report. The other tests still pass.

- [ ] **Step 3: Implement**

1. In `services/evidence_service.py`, after `NOT_CITED_NOTE = ...`, add:

```python
KEEP_MARKERS_INSTRUCTION = (
    "Keep the [F#] and [C#] citation markers from the phase write-ups exactly as written, "
    "next to each point you take from them."
)
```

2. In `services/research_execution_service.py`, replace

```python
from . import insights_service, llm_service, openai_service, research_run_service, research_task_service
```

with (the wrapped form is what ruff's isort wants once the line passes 120 characters)

```python
from . import (
    evidence_service,
    insights_service,
    llm_service,
    openai_service,
    research_run_service,
    research_task_service,
)
```

3. Replace

```python
    "Use the SO WHAT / NOW WHAT framework. Write in Australian English."
)
```

with

```python
    "Use the SO WHAT / NOW WHAT framework. Write in Australian English. "
    + evidence_service.KEEP_MARKERS_INSTRUCTION
)
```

4. Insert directly above `def run_synthesis(project_name, card_id, run_id):`

```python
def _options_sources(project_name, text):
    """The options report's Sources list as Markdown, or "" when it cites nothing. A failed lookup adds nothing:
    the report itself must never be lost to its list."""
    try:
        return evidence_service.sources_markdown(text, evidence_service.citation_index(project_name))
    except Exception as exc:
        print(f"[board] could not list the sources of the options report: {exc}")
        return ""


```

5. In `run_synthesis`, replace

```python
        text = llm_service.prompt_completion(SYNTHESIS_SYSTEM_PROMPT, user_message, max_tokens=SYNTHESIS_MAX_TOKENS)
```

with

```python
        text = llm_service.prompt_completion(SYNTHESIS_SYSTEM_PROMPT, user_message, max_tokens=SYNTHESIS_MAX_TOKENS)
        # The saved report gets its own Sources list so it stands alone (spec section 7). Insights Phase 7 keeps
        # the plain text: its card links the markers and the Word report adds the list itself.
        report = text + _options_sources(project_name, text)
```

and further down, replace

```python
            project_name, run_id, status="completed", output_text=text, completed_at=datetime.now().isoformat(),
        )
        research_task_service.transition_task(card_id, "COMPLETE")
```

with

```python
            project_name, run_id, status="completed", output_text=report, completed_at=datetime.now().isoformat(),
        )
        research_task_service.transition_task(card_id, "COMPLETE")
```

(`phase_7["summary"] = text` stays as it is.)

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run: `python -m pytest tests/test_research_execution_service.py -v` (expected: all pass). Then `python -m ruff check services/evidence_service.py services/research_execution_service.py tests/test_research_execution_service.py` (clean) and `python -m pytest tests/ -q` (all pass).

- [ ] **Step 5: Commit**

```bash
git add services/evidence_service.py services/research_execution_service.py tests/test_research_execution_service.py
git commit -m "Keep citation markers in the options report and list its sources" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BboZJ8FCLubxrmK1HY4ZeX"
```

---

### Task 5: Generate adds the facts briefing; Phase 7 keeps the markers

**Files:**
- Modify: `static/evidence.js`, `static/app.js` (`buildPriorPhaseContextForPhase7`, `generatePhaseInsight`), `templates/index.html` (script versions)
- Test: `tests/js/test_evidence_view.js` (append), `tests/test_routes_evidence.py` (append)

**Interfaces:**
- Consumes: `GET /api/evidence/brief` (Task 1); `evidence_service.KEEP_MARKERS_INSTRUCTION` (Task 4, for the test); `evidence_service` already imported in `tests/test_routes_evidence.py` (Task 3).
- Produces (`static/evidence.js`; Node exports and browser globals):
  - `briefPromptAddition(brief) -> string`: `'\n\n' + BRIEF_INTRO + '\n\n' + brief.text.trim()` when `Number(brief.fact_count) > 0` and the text is non-blank, else `''`.
  - `phaseBriefAddition(call, project, phaseKey) -> Promise<string>`: GETs `/api/evidence/brief?project=<enc>&phase=<enc>` via `call(method, url)`; any failure, or no project, gives `''`.
  - Browser: `window.evidencePhaseBriefAddition(project, phaseKey) -> Promise<string>`.
- Produces (`static/app.js`): `const KEEP_CITATION_MARKERS` (same words as `KEEP_MARKERS_INSTRUCTION`). `buildPriorPhaseContextForPhase7()` returns `''` as before when nothing is written, otherwise `KEEP_CITATION_MARKERS + '\n\n' + context`.
- Test helpers added to `tests/test_routes_evidence.py`: `_static(name) -> str`, `_between(source, start, end) -> str` (Task 6 reuses them).

- [ ] **Step 1: Write the failing tests**

In `tests/js/test_evidence_view.js`, insert before the runner block (the line `(async () => {` followed by `let failed = 0;`):

```js
// ---- Generate: the facts briefing (spec section 4) ----
const BRIEF = { text: '# CHECKED FACTS FOR PHASE 1 (newest first)\n[F1] A claim', fact_count: 1, conclusion_count: 0, shown_facts: 1 };
test('the briefing goes into the prompt only when the phase has checked facts', () => {
    const added = E.briefPromptAddition(BRIEF);
    assert(added.startsWith('\n\nFACTS BRIEFING'));
    assert(added.endsWith('\n\n' + BRIEF.text));
    assert.strictEqual(E.briefPromptAddition(Object.assign({}, BRIEF, { fact_count: 0 })), '');
    [null, undefined, {}, { fact_count: 2, text: '' }, { fact_count: 2, text: '   ' }, { fact_count: 2 }, { fact_count: 'none', text: 'x' }]
        .forEach(brief => assert.strictEqual(E.briefPromptAddition(brief), '', JSON.stringify(brief)));
});
test('Generate asks for the briefing of this project and phase', async () => {
    const asked = [];
    const call = (method, url) => { asked.push([method, url]); return Promise.resolve({ success: true, brief: BRIEF }); };
    assert.strictEqual(await E.phaseBriefAddition(call, 'My Project & Co', '4'), E.briefPromptAddition(BRIEF));
    assert.deepStrictEqual(asked, [['GET', '/api/evidence/brief?project=My%20Project%20%26%20Co&phase=4']]);
});
test('a failed, odd or empty briefing leaves the prompt as it was', async () => {
    const answers = [
        () => Promise.reject(new Error('Something went wrong (500).')),
        () => { throw new Error('offline'); },
        () => Promise.resolve(null),
        () => Promise.resolve({ success: true }),
        () => Promise.resolve({ success: true, brief: Object.assign({}, BRIEF, { fact_count: 0 }) }),
    ];
    for (const call of answers) assert.strictEqual(await E.phaseBriefAddition(call, 'P', '4'), '');
    let asked = false;
    assert.strictEqual(await E.phaseBriefAddition(() => { asked = true; return Promise.resolve({}); }, '', '4'), '');
    assert.strictEqual(asked, false);   // no project: nothing to ask for
});

```

Append to `tests/test_routes_evidence.py`:

```python


# ---- app.js glue (app.js cannot run under Node, so these check its source) ----

def _static(name):
    return (Path(__file__).resolve().parents[1] / "static" / name).read_text(encoding="utf-8")


def _between(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def test_generate_adds_the_briefing_after_the_guidance_and_before_the_users_instructions():
    body = _between(_static("app.js"), "async function generatePhaseInsight(", "async function handleInsightResponse(")
    added = body.index("prompt += await evidencePhaseBriefAddition(briefProject, phaseKey);")
    assert body.index("CRITICAL INSTRUCTIONS:") < added < body.index("USER INSTRUCTIONS:")
    assert "typeof evidencePhaseBriefAddition === 'function'" in body       # an old cached evidence.js adds nothing
    assert "if (currentProject !== briefProject) {" in body                  # a project switch while it loads stops
    assert added < body.index("getActiveGenerationForProject(currentProject)")


def test_phase_7_keeps_the_markers_word_for_word_with_the_options_report():
    source = _static("app.js")
    assert f"const KEEP_CITATION_MARKERS = '{evidence_service.KEEP_MARKERS_INSTRUCTION}';" in source
    body = _between(source, "function buildPriorPhaseContextForPhase7(", "function inferFieldConfidence(")
    assert "if (sections.length === 0) return '';" in body                   # still empty when there is nothing
    assert r"return `${KEEP_CITATION_MARKERS}\n\n${clipped}`;" in body
```

- [ ] **Step 2: Run them to verify they fail**

Run: `node tests/js/test_evidence_view.js`
Expected: 3 FAIL (`E.briefPromptAddition is not a function`, `E.phaseBriefAddition is not a function`), the other 31 pass, exit code 1.
Run: `python -m pytest tests/test_routes_evidence.py -v -k "generate or phase_7"`
Expected: 2 FAIL (`ValueError: substring not found` / assertion on `KEEP_CITATION_MARKERS`).

- [ ] **Step 3: Implement**

1. `static/evidence.js`: insert directly above `    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }`:

```js
    // ---- Generate: the phase's facts briefing (spec section 4) ----
    const BRIEF_INTRO = 'FACTS BRIEFING — the checked facts and conclusions saved for this phase. Build the write-up on these first and cite them as the rules below say; use the attached source files for colour and context.';

    // What Generate adds to a phase's prompt: the briefing when the phase has checked facts, otherwise nothing,
    // so a phase without facts is written exactly as before.
    function briefPromptAddition(brief) {
        if (!brief || !(Number(brief.fact_count) > 0) || typeof brief.text !== 'string' || !brief.text.trim()) return '';
        return `\n\n${BRIEF_INTRO}\n\n${brief.text.trim()}`;
    }
    // Fetches the phase's briefing and returns the prompt addition. Any failure returns '', so Generate never
    // fails because of the facts.
    async function phaseBriefAddition(call, project, phaseKey) {
        if (!project) return '';
        try {
            const data = await call('GET', `/api/evidence/brief?project=${encodeURIComponent(project)}&phase=${encodeURIComponent(phaseKey)}`);
            return briefPromptAddition(data && data.brief);
        } catch (err) {
            return '';
        }
    }

```

2. `static/evidence.js`: replace

```js
    const pure = { safeUrl, factNode, conclusionNode, phaseEvidenceNode, searchResultsNode, createOpenState, createController, openFoldedBoxes };
```

with

```js
    const pure = {
        safeUrl, factNode, conclusionNode, phaseEvidenceNode, searchResultsNode, createOpenState, createController, openFoldedBoxes,
        briefPromptAddition, phaseBriefAddition,
    };
```

3. `static/evidence.js`: replace `    window.evidenceResetSearch = screen.resetSearch;` with

```js
    window.evidenceResetSearch = screen.resetSearch;
    window.evidencePhaseBriefAddition = (project, phaseKey) => phaseBriefAddition(call, project, phaseKey);
```

4. `static/app.js`: replace `function buildPriorPhaseContextForPhase7() {` with

```js
// Phase 7 draws on the phase write-ups, which cite checked facts as [F#] / [C#]. Kept word for word in step with
// evidence_service.KEEP_MARKERS_INSTRUCTION (tests/test_routes_evidence.py checks).
const KEEP_CITATION_MARKERS = 'Keep the [F#] and [C#] citation markers from the phase write-ups exactly as written, next to each point you take from them.';

function buildPriorPhaseContextForPhase7() {
```

and, at the end of that function, replace

```js
    const merged = sections.join('\n\n---\n\n');
    const MAX_CONTEXT_CHARS = 120000;
    return merged.length > MAX_CONTEXT_CHARS ? merged.slice(0, MAX_CONTEXT_CHARS) : merged;
}
```

with

```js
    const merged = sections.join('\n\n---\n\n');
    const MAX_CONTEXT_CHARS = 120000;
    const clipped = merged.length > MAX_CONTEXT_CHARS ? merged.slice(0, MAX_CONTEXT_CHARS) : merged;
    return `${KEEP_CITATION_MARKERS}\n\n${clipped}`;
}
```

5. `static/app.js`, in `generatePhaseInsight`: replace

```js
4. If no reliable evidence exists in the provided sources, return exactly: MISSING`;

    if (userInstructions && userInstructions.trim()) {
```

with

```js
4. If no reliable evidence exists in the provided sources, return exactly: MISSING`;

    // Facts first (spec section 4): when this phase has checked facts, the server's facts briefing follows the
    // guidance. A phase without facts, or a failed request, leaves the prompt exactly as before.
    const briefProject = currentProject;
    if (typeof evidencePhaseBriefAddition === 'function') {
        prompt += await evidencePhaseBriefAddition(briefProject, phaseKey);
        if (currentProject !== briefProject) {  // the person switched project while the briefing loaded
            if (btn) { btn.innerText = "Generate"; btn.disabled = false; }
            hideInsightsIndicator();
            return;
        }
    }

    if (userInstructions && userInstructions.trim()) {
```

(`generatePhaseInsight` is already `async`; Refresh All calls it per phase, so it inherits this.)

6. `templates/index.html`: change `<script src="/static/app.js?v=13.6"></script>` to `?v=13.7`, and `<script src="/static/evidence.js?v=1"></script>` to `?v=2`.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `node tests/js/test_evidence_view.js` (expected: all 34 passed), `node --check static/app.js`, `node tests/js/test_board_view.js` (all 27 passed), `python -m pytest tests/test_routes_evidence.py tests/test_board_js.py -v` (all pass), `python -m ruff check tests/test_routes_evidence.py` (clean) and `python -m pytest tests/ -q` (all pass).

- [ ] **Step 5: Commit**

```bash
git add static/evidence.js static/app.js templates/index.html tests/js/test_evidence_view.js tests/test_routes_evidence.py
git commit -m "Write phase insights from the checked facts first, citing them" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BboZJ8FCLubxrmK1HY4ZeX"
```

---

### Task 6: Citations on the phase cards

**Files:**
- Modify: `static/evidence.js`, `static/app.js` (`renderInsights` phase card), `static/style.css`, `templates/index.html` (script versions)
- Test: `tests/js/test_evidence_view.js` (fake DOM extended; tests appended), `tests/test_routes_evidence.py` (append)

**Interfaces:**
- Consumes: `/api/evidence` answer `{phases: {key: {facts: [{id, status, …}], conclusions: [{id, status, …}]}}}` (existing); `openFoldedBoxes(node)` (existing); `evidence_service.NOT_CITED_NOTE` (Task 3, for the test); `_static`, `_between` (Task 5).
- Produces (`static/evidence.js`, Node exports):
  - `NOT_CITED_NOTE` = `'Not fact-cited — written from the reports only.'`
  - `hasCitationMarkers(text) -> boolean`
  - `notCitedNote(summary) -> string`: the note for a non-empty, non-`MISSING` write-up with no marker, else `''`.
  - `citationIndex(phases) -> {F: {id: status}, C: {id: status}}`
  - `linkCitations(doc, root, index)`:
    - Turns each known marker in `root`'s text nodes into `<a class="ev-cite[ rejected]" href="#ev-fact-<id>|#ev-concl-<id>" data-cite="F12">`, with `title="Rejected"` when rejected.
    - Skips `CODE`, `PRE` and existing `A` elements.
    - Re-marks links it made before (idempotent).
  - `handleCitationClick(doc, event) -> boolean`: for a click inside `a[data-cite]` it calls `event.stopPropagation()`, opens the folds around the target and returns true.
  - `conclusionNode` items now carry `id="ev-concl-<id>"`.
  - `createController(...).renderAll` links every `[data-cite-phase]` box after painting the slots.
  - Browser: a capture-phase click listener on `document`, and `window.evidenceNotCitedNote(summary)`.
- Produces (`static/app.js`): a phase card with content renders `<div class="phase-content markdown-body" data-cite-phase="<key>">`, preceded inside by `<p class="ev-not-cited">…</p>` when `evidenceNotCitedNote` returns text.

- [ ] **Step 1: Write the failing tests**

In `tests/js/test_evidence_view.js`:

1. Replace

```js
// A tiny DOM: enough for evidence.js, and it refuses innerHTML outright.
class FakeNode {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.className = ''; this.ownText = ''; }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
```

with

```js
// A tiny DOM: enough for evidence.js, and it refuses innerHTML outright. Text nodes exist so the marker linking on
// the phase cards can be tested; elements keep every child (text nodes too) in `children`.
class FakeText {
    constructor(text) { this.nodeType = 3; this.nodeValue = String(text); this.children = []; this.className = ''; }
    get textContent() { return this.nodeValue; }
    getAttribute() { return null; }
}
class FakeNode {
    constructor(tag) { this.nodeType = 1; this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.className = ''; this.ownText = ''; }
    get childNodes() { return this.children; }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    insertBefore(node, ref) { const at = this.children.indexOf(ref); node.parentNode = this; this.children.splice(at < 0 ? this.children.length : at, 0, node); return node; }
    removeAttribute(name) { delete this.attributes[name]; }
```

2. Replace `const doc = { createElement: tag => new FakeNode(tag) };` with

```js
const doc = { createElement: tag => new FakeNode(tag), createTextNode: text => new FakeText(text) };
```

3. Replace the whole `makePage` function (and its comment) with

```js
// A page for the controller: a search input and results box, a slot for each phase, and (optionally) the phase
// cards' rendered write-ups, each a [data-cite-phase] box.
function makePage(phases, summaries) {
    const page = { input: new FakeNode('input'), results: new FakeNode('div'), slots: phases.map(key => { const s = new FakeNode('div'); s.setAttribute('data-evidence-phase', key); return s; }),
                   summaries: summaries || [] };
    page.input.value = 'typed words';
    page.document = {
        createElement: tag => new FakeNode(tag),
        createTextNode: text => new FakeText(text),
        getElementById: id => (id === 'evidence-search-input' ? page.input : id === 'evidence-search-results' ? page.results
            : page.slots.map(slot => all(slot, n => n.getAttribute('id') === id)[0]).find(Boolean) || null),
        querySelectorAll: selector => (selector === '[data-cite-phase]' ? page.summaries : page.slots),
    };
    return page;
}
```

4. Insert before the runner block (`(async () => {` / `let failed = 0;`):

```js
// ---- citation markers on the phase cards (spec section 5) ----
// A rendered write-up: a box like the card's .phase-content, holding paragraphs (text nodes) or ready-made nodes.
function para(...parts) { const p = new FakeNode('p'); parts.forEach(x => p.appendChild(typeof x === 'string' ? new FakeText(x) : x)); return p; }
function wrap(tag, ...parts) { const n = new FakeNode(tag); parts.forEach(x => n.appendChild(typeof x === 'string' ? new FakeText(x) : x)); return n; }
function writeUp(...blocks) {
    const box = new FakeNode('div');
    box.setAttribute('data-cite-phase', '1');
    blocks.forEach(b => box.appendChild(typeof b === 'string' ? para(b) : b));
    return box;
}
const citeLinks = node => all(node, n => n.tagName === 'A' && n.getAttribute('data-cite') !== null);
const PHASES = { 1: { facts: [fact({ id: 12 }), fact({ id: 13, status: 'rejected' })], conclusions: [conclusion({ id: 3 })] },
                 4: { facts: [fact({ id: 40, phase_key: '4' })], conclusions: [conclusion({ id: 9, status: 'rejected' })] } };
const INDEX = E.citationIndex(PHASES);

test('the citation index holds every phase, facts and conclusions apart', () => {
    assert.deepStrictEqual([INDEX.F[12], INDEX.F[13], INDEX.F[40], INDEX.C[3], INDEX.C[9]], ['active', 'rejected', 'active', 'active', 'rejected']);
    assert.strictEqual(INDEX.F[3], undefined);       // C3 is a conclusion, not a fact
    assert.strictEqual(E.citationIndex(undefined).F[1], undefined);
});
test('markers for this project become in-page links, and the text around them stays as it was', () => {
    const box = writeUp('Fees rose [F12] [C3] and [F40].');
    E.linkCitations(doc, box, INDEX);
    assert.deepStrictEqual(citeLinks(box).map(a => [a.textContent, a.getAttribute('href'), a.className, a.getAttribute('target')]), [
        ['[F12]', '#ev-fact-12', 'ev-cite', null], ['[C3]', '#ev-concl-3', 'ev-cite', null], ['[F40]', '#ev-fact-40', 'ev-cite', null]]);
    assert.strictEqual(box.textContent, 'Fees rose [F12] [C3] and [F40].');
});
test('a marker for an id this project does not have stays plain text', () => {
    const box = writeUp('Gone [F999], [C77] and [f12].');
    const before = box.children[0].children[0];
    E.linkCitations(doc, box, INDEX);
    assert.strictEqual(citeLinks(box).length, 0);
    assert.strictEqual(box.children[0].children[0], before);        // the text node was not touched
    assert.strictEqual(box.textContent, 'Gone [F999], [C77] and [f12].');
});
test('a marker for a rejected fact or conclusion is struck through with a Rejected tooltip', () => {
    const box = writeUp('Old [F13] and [C9], current [F12].');
    E.linkCitations(doc, box, INDEX);
    assert.deepStrictEqual(citeLinks(box).map(a => [a.textContent, a.className, a.getAttribute('title')]), [
        ['[F13]', 'ev-cite rejected', 'Rejected'], ['[C9]', 'ev-cite rejected', 'Rejected'], ['[F12]', 'ev-cite', null]]);
});
test('markers in code, and inside existing links, are left alone', () => {
    const box = writeUp(wrap('pre', wrap('code', '[F12]')), para('Inline ', wrap('code', '[F12]')), para(wrap('a', '[F12]')));
    E.linkCitations(doc, box, INDEX);
    assert.strictEqual(citeLinks(box).length, 0);
    assert.strictEqual(box.textContent, '[F12]Inline [F12][F12]');
});
test('markers inside bold, lists and tables are linked too', () => {
    const box = writeUp(para('Lead ', wrap('strong', 'Deakin [F12]')), wrap('ul', wrap('li', 'Point [C3]')), wrap('table', wrap('tr', wrap('td', '[F40]'))));
    E.linkCitations(doc, box, INDEX);
    assert.deepStrictEqual(citeLinks(box).map(a => a.textContent), ['[F12]', '[C3]', '[F40]']);
});
test('linking twice does not wrap a link again, and brings the rejected state up to date', () => {
    const box = writeUp('Fees [F12].');
    E.linkCitations(doc, box, INDEX);
    const [link] = citeLinks(box);
    const rejected = E.citationIndex({ 1: { facts: [fact({ id: 12, status: 'rejected' })], conclusions: [] } });
    E.linkCitations(doc, box, rejected);
    assert.deepStrictEqual(citeLinks(box), [link]);
    assert.deepStrictEqual([link.className, link.getAttribute('title')], ['ev-cite rejected', 'Rejected']);
    E.linkCitations(doc, box, INDEX);                       // restored
    assert.deepStrictEqual([link.className, link.getAttribute('title')], ['ev-cite', null]);
    assert.strictEqual(box.textContent, 'Fees [F12].');
});
test('markup-looking text next to a marker stays text', () => {
    const box = writeUp('<img src=x onerror=alert(1)> [F12] <script>x</script>');
    E.linkCitations(doc, box, INDEX);
    assert.strictEqual(tagged(box, 'img').length + tagged(box, 'script').length, 0);
    assert.strictEqual(box.textContent, '<img src=x onerror=alert(1)> [F12] <script>x</script>');
    assert.strictEqual(citeLinks(box).length, 1);
});
test('a conclusion carries the anchor its markers link to', () => {
    assert.strictEqual(E.conclusionNode(doc, conclusion({ id: 3 })).getAttribute('id'), 'ev-concl-3');
});
test('a write-up with no markers gets the Not fact-cited note; one with markers, or none at all, does not', () => {
    assert.strictEqual(E.NOT_CITED_NOTE, 'Not fact-cited — written from the reports only.');
    assert.strictEqual(E.notCitedNote('Written from the reports.'), E.NOT_CITED_NOTE);
    assert.strictEqual(E.notCitedNote('Lowercase [f1] is not a marker.'), E.NOT_CITED_NOTE);
    ['Cited [F1].', 'Concluded [C2].', '', '   ', 'MISSING', null, undefined, 42].forEach(summary =>
        assert.strictEqual(E.notCitedNote(summary), '', String(summary)));
});
test('clicking a marker stops the click reaching the phase card and opens the fold its fact sits in', async () => {
    const box = writeUp('Old fact [F3].');
    const page = makePage(['1'], [box]); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('P');
    env.requests[0].resolve({ success: true, phases: { 1: { facts: manyFacts(25).map(f => Object.assign(f, { phase_key: '1' })), conclusions: [] } } });
    await flush();
    const [link] = citeLinks(box);
    let stopped = false;
    assert.strictEqual(E.handleCitationClick(page.document, { target: link, stopPropagation: () => { stopped = true; } }), true);
    assert.strictEqual(stopped, true);
    assert.strictEqual(byClass(page.slots[0], 'ev-more')[0].open, true);
    let other = false;
    assert.strictEqual(E.handleCitationClick(page.document, { target: box.children[0], stopPropagation: () => { other = true; } }), false);
    assert.strictEqual(other, false);                       // any other click on the card still opens the editor
});
test('the controller links the cards once the facts load, and re-marks them after a reject', async () => {
    const box = writeUp('Fees [F12] and [C3].');
    const page = makePage(['1'], [box]); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('P');
    assert.strictEqual(citeLinks(box).length, 0);           // nothing to link to until the anchors exist
    env.requests[0].resolve({ success: true, phases: PHASES }); await flush();
    assert.deepStrictEqual(citeLinks(box).map(a => a.className), ['ev-cite', 'ev-cite']);
    const rejected = JSON.parse(JSON.stringify(PHASES)); rejected[1].facts[0].status = 'rejected';
    screen.renderAll('P');
    env.requests[1].resolve({ success: true, phases: rejected }); await flush();
    assert.deepStrictEqual(citeLinks(box).map(a => a.className), ['ev-cite rejected', 'ev-cite']);
});
test('a past Insights version (no slots) or a failed load leaves the markers as plain text', async () => {
    const past = writeUp('Fees [F12].');
    const pastPage = makePage([], [past]); const pastEnv = makeEnv(pastPage);
    E.createController(pastEnv).renderAll('P');
    assert.strictEqual(pastEnv.requests.length, 0);
    assert.strictEqual(citeLinks(past).length, 0);
    const box = writeUp('Fees [F12].');
    const page = makePage(['1'], [box]); const env = makeEnv(page);
    E.createController(env).renderAll('P');
    env.requests[0].reject(new Error('Something went wrong (500).')); await flush();
    assert.strictEqual(citeLinks(box).length, 0);
});

```

Append to `tests/test_routes_evidence.py`:

```python


def test_a_phase_card_marks_its_write_up_for_linking_and_shows_the_note_as_text():
    body = _between(_static("app.js"), "function renderInsights(", "function renderConfidenceBadge(")
    assert ("const citeNote = hasContent && typeof evidenceNotCitedNote === 'function' "
            "? evidenceNotCitedNote(phase.summary) : '';") in body
    assert '<div class="phase-content markdown-body"${hasContent ? ` data-cite-phase="${escapeAttr(key)}"` : \'\'}>' in body
    assert "${citeNote ? `<p class=\"ev-not-cited\">${escapeHtml(citeNote)}</p>` : ''}" in body


def test_the_note_reads_the_same_on_screen_and_in_the_word_report():
    assert f"const NOT_CITED_NOTE = '{evidence_service.NOT_CITED_NOTE}';" in _static("evidence.js")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `node tests/js/test_evidence_view.js`
Expected: the 13 new tests FAIL (`E.citationIndex is not a function`, `E.linkCitations is not a function`, missing `ev-concl-3` id, …); the 34 earlier tests still pass (the fake DOM changes are backward compatible), exit code 1.
Run: `python -m pytest tests/test_routes_evidence.py -v -k "phase_card or note_reads"`
Expected: 2 FAIL.

- [ ] **Step 3: Implement**

1. `static/evidence.js`, in `conclusionNode`, replace

```js
        const item = el(doc, 'li', conclusion.status === 'rejected' ? 'ev-concl rejected' : 'ev-concl');
        item.appendChild(el(doc, 'p', 'ev-text', conclusion.text));
```

with

```js
        const item = el(doc, 'li', conclusion.status === 'rejected' ? 'ev-concl rejected' : 'ev-concl');
        item.setAttribute('id', `ev-concl-${Number(conclusion.id)}`);  // the target of a [C#] marker on a phase card
        item.appendChild(el(doc, 'p', 'ev-text', conclusion.text));
```

2. `static/evidence.js`: insert directly above `    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }` (below Task 5's Generate block):

```js
    // ---- Citation markers on the phase cards (spec section 5) ----
    const NOT_CITED_NOTE = 'Not fact-cited — written from the reports only.';

    function hasCitationMarkers(text) { return typeof text === 'string' && /\[[FC]\d+\]/.test(text); }
    // The note a phase card shows above a write-up that cites no facts ('' when it needs none).
    function notCitedNote(summary) {
        const text = typeof summary === 'string' ? summary.trim() : '';
        return text && text !== 'MISSING' && !hasCitationMarkers(text) ? NOT_CITED_NOTE : '';
    }
    // {F: {id: status}, C: {id: status}} for every fact and conclusion of the project, from the same /api/evidence
    // answer that fills the phase slots. A phase card may cite another phase's facts (Phase 7 does).
    function citationIndex(phases) {
        const index = { F: Object.create(null), C: Object.create(null) };
        Object.keys(phases || {}).forEach(key => {
            const phase = phases[key] || {};
            (phase.facts || []).forEach(f => { index.F[Number(f.id)] = f.status; });
            (phase.conclusions || []).forEach(c => { index.C[Number(c.id)] = c.status; });
        });
        return index;
    }
    function setCiteState(link, status) {
        if (status === 'rejected') {
            link.className = 'ev-cite rejected';
            link.setAttribute('title', 'Rejected');
        } else {
            link.className = 'ev-cite';
            link.removeAttribute('title');
        }
    }
    function citeLink(doc, kind, id, label, status) {
        const link = el(doc, 'a', null, label);
        link.setAttribute('href', `#${kind === 'F' ? 'ev-fact' : 'ev-concl'}-${id}`);
        link.setAttribute('data-cite', `${kind}${id}`);
        setCiteState(link, status);
        return link;
    }
    // One text node: each marker for a fact or conclusion this project has becomes an in-page link; a marker for
    // an id the project does not have stays plain text.
    function linkTextNode(doc, node, index) {
        const value = node.nodeValue || '';
        const markers = /\[([FC])(\d+)\]/g;
        const parts = [];
        let last = 0;
        let match;
        while ((match = markers.exec(value)) !== null) {
            const id = Number(match[2]);
            const status = index[match[1]][id];
            if (status === undefined) continue;
            if (match.index > last) parts.push(doc.createTextNode(value.slice(last, match.index)));
            parts.push(citeLink(doc, match[1], id, match[0], status));
            last = match.index + match[0].length;
        }
        if (!parts.length) return;
        if (last < value.length) parts.push(doc.createTextNode(value.slice(last)));
        const parent = node.parentNode;
        parts.forEach(part => parent.insertBefore(part, node));
        parent.removeChild(node);
    }
    // Walks a rendered (and sanitised) write-up. Code and existing links are left alone. A marker linked by an
    // earlier pass (the card is not rebuilt when a fact is rejected or restored) only has its rejected state updated.
    function linkCitations(doc, root, index) {
        Array.from(root.childNodes || []).forEach(node => {
            if (node.nodeType === 3) { linkTextNode(doc, node, index); return; }
            if (node.nodeType !== 1) return;
            const tag = String(node.tagName || '').toUpperCase();
            if (tag === 'A') {
                const cite = /^([FC])(\d+)$/.exec(node.getAttribute('data-cite') || '');
                const status = cite ? index[cite[1]][Number(cite[2])] : undefined;
                if (status !== undefined) setCiteState(node, status);
                return;
            }
            if (tag === 'CODE' || tag === 'PRE') return;
            linkCitations(doc, node, index);
        });
    }
    function citationFromTarget(node) {
        for (let n = node; n; n = n.parentNode) {
            if (n.nodeType === 1 && String(n.tagName || '').toUpperCase() === 'A' && n.getAttribute('data-cite')) return n;
        }
        return null;
    }
    // A click on a marker: keep it from reaching the phase card (whose own click opens the editor) and open the
    // folded box its fact or conclusion sits in. The browser then follows the #anchor.
    function handleCitationClick(doc, event) {
        const link = citationFromTarget(event && event.target);
        if (!link) return false;
        event.stopPropagation();
        openFoldedBoxes(doc.getElementById((link.getAttribute('href') || '').slice(1)));
        return true;
    }

```

3. `static/evidence.js`, in `renderAll`, replace

```js
                slot.appendChild(phaseEvidenceNode(doc, data.phases[phase], ev.rejectedOpen.get(phase), ev.moreOpen.get(phase)));
            });
        }
```

with

```js
                slot.appendChild(phaseEvidenceNode(doc, data.phases[phase], ev.rejectedOpen.get(phase), ev.moreOpen.get(phase)));
            });
            // The anchors exist now, so the markers on the phase cards can link to them.
            const index = citationIndex(data.phases);
            Array.from(doc.querySelectorAll('[data-cite-phase]')).forEach(box => linkCitations(doc, box, index));
        }
```

4. `static/evidence.js`: in the `pure` object (as left by Task 5), replace

```js
        briefPromptAddition, phaseBriefAddition,
    };
```

with

```js
        briefPromptAddition, phaseBriefAddition,
        NOT_CITED_NOTE, hasCitationMarkers, notCitedNote, citationIndex, linkCitations, handleCitationClick,
    };
```

5. `static/evidence.js`: replace `    window.evidenceRenderAll = screen.renderAll;` with

```js
    // Capture phase: this runs before the phase card's own onclick, so stopping the click here keeps the editor shut.
    document.addEventListener('click', event => { handleCitationClick(document, event); }, true);

    window.evidenceRenderAll = screen.renderAll;
```

and replace `    window.evidencePhaseBriefAddition = (project, phaseKey) => phaseBriefAddition(call, project, phaseKey);` with

```js
    window.evidencePhaseBriefAddition = (project, phaseKey) => phaseBriefAddition(call, project, phaseKey);
    window.evidenceNotCitedNote = notCitedNote;
```

6. `static/app.js`, in `renderInsights` (phase cards loop), replace

```js
            const gapsHtml = renderGaps(phase.gaps);
            const topicsHtml = renderSuggestedTopics(phase.suggested_topics);
```

with

```js
            const gapsHtml = renderGaps(phase.gaps);
            const topicsHtml = renderSuggestedTopics(phase.suggested_topics);
            // A write-up that cites no facts says so above its text (spec section 5). evidence.js links the markers.
            const citeNote = hasContent && typeof evidenceNotCitedNote === 'function' ? evidenceNotCitedNote(phase.summary) : '';
```

and replace

```js
                    <div class="phase-content markdown-body">
                        ${summaryHtml}
```

with

```js
                    <div class="phase-content markdown-body"${hasContent ? ` data-cite-phase="${escapeAttr(key)}"` : ''}>
                        ${citeNote ? `<p class="ev-not-cited">${escapeHtml(citeNote)}</p>` : ''}
                        ${summaryHtml}
```

7. `static/style.css`: append

```css
/* Citation markers on the phase cards (evidence.js) */
.ev-not-cited { font-size: 0.8rem; font-style: italic; color: #8a6d00; margin: 0 0 0.75rem; }
a.ev-cite { color: var(--oes-ink); font-size: 0.85em; font-weight: 600; text-decoration: none; border-bottom: 1px dotted currentColor; white-space: nowrap; }
a.ev-cite:hover { color: var(--oes-valencia); }
a.ev-cite.rejected { color: #999; text-decoration: line-through; border-bottom: none; }
```

8. `templates/index.html`: `app.js?v=13.7` → `?v=13.8`, `evidence.js?v=2` → `?v=3`.

- [ ] **Step 4: Run the tests, ruff and the full suite**

Run `node tests/js/test_evidence_view.js` (expected: all 47 passed; this includes the existing "the source never uses HTML-string APIs" check), `node --check static/app.js`, `node tests/js/test_board_view.js` (all 27 passed), `python -m pytest tests/test_routes_evidence.py tests/test_board_js.py -v` (all pass), `python -m ruff check tests/test_routes_evidence.py` (clean) and `python -m pytest tests/ -q` (all pass).

- [ ] **Step 5: Commit**

```bash
git add static/evidence.js static/app.js static/style.css templates/index.html tests/js/test_evidence_view.js tests/test_routes_evidence.py
git commit -m "Link citation markers on the phase cards to their facts and conclusions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BboZJ8FCLubxrmK1HY4ZeX"
```

---

### Task 7: Docs, browser check and one real Generate

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above. Produces documentation only.

- [ ] **Step 1: Document**

In `CLAUDE.md`:

1. Under **Backend → Services**, directly after the bullet that starts `  - Facts and conclusions (Phase 4b):`, add:

```markdown
  - Fact-cited write-ups: Generate on Insights first fetches `GET /api/evidence/brief` (`evidence_service.phase_brief`, capped at `MAX_PHASE_BRIEF_CHARS` 12,000 characters: the phase's facts newest first, its conclusions, the rejected claims not to use, and the citation rules). When the phase has active facts, the briefing goes into the prompt after the phase guidance. A phase without facts, or a failed request, leaves the prompt as before. Write-ups cite `[F#]` (fact) and `[C#]` (conclusion) markers, and mark claims taken only from the reports "(not fact-checked)". One builder, `evidence_service.sources_lines`, makes the Sources list that the Word report (`/api/insights/report`) adds after each cited phase. The Phase 7 options report appends the same list to its saved report (the run's text; Insights Phase 7 keeps the plain text). Phase 7 keeps the markers (`KEEP_MARKERS_INSTRUCTION`, repeated word for word in `app.js`). No migration: "fact-cited" means the text has a marker.
```

2. Under **Frontend**, replace the end of the **Insights → facts** bullet, `Built as DOM nodes with `textContent` only; links only for http/https.`, with:

```markdown
Built as DOM nodes with `textContent` only; links only for http/https, plus in-page links for citation markers. On each phase card, `[F#]` / `[C#]` markers become links to `#ev-fact-<id>` / `#ev-concl-<id>` (struck through when rejected; unknown ids stay text; markers in code are left alone), and a write-up without markers shows "Not fact-cited — written from the reports only."
```

- [ ] **Step 2: Run the full checks**

Run `python -m pytest tests/ -q` (all pass), `node tests/js/test_evidence_view.js` (all 47 passed), `node tests/js/test_board_view.js` (all 27 passed), and `python -m ruff check services/evidence_service.py services/docx_service.py services/research_execution_service.py routes/evidence.py routes/ai.py tests/test_evidence_citations.py tests/test_docx_report.py tests/test_routes_evidence.py tests/test_research_execution_service.py` (clean).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Document fact-cited phase write-ups" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BboZJ8FCLubxrmK1HY4ZeX"
```

- [ ] **Step 4 (controller, with the user): Browser check on BoardTest**

Run the app only from the main checkout (`vantage-fresh` merged there, or the main folder checked out on the branch), never from a worktree. Hard-refresh so `app.js?v=13.8` and `evidence.js?v=3` load. On **BoardTest → Insights**:
- A phase write-up written before this feature shows "Not fact-cited — written from the reports only." above its text. A phase with no write-up shows no note.
- In the browser's network tab, `GET /api/evidence/brief?project=BoardTest&phase=1` returns a brief with `fact_count` > 0 and the four sections; a phase with no facts returns `fact_count: 0`.
- With the user's OK, open Phase 1 in the phase editor and add `[F<id>]` for a real fact id shown below the card, plus `[F99999]` and `` `[F<id>]` `` in backticks. Save, then check:
  - The real marker is a link; `[F99999]` and the code one stay plain text.
  - Clicking the link scrolls to the fact (opening "Show n more" if it is folded) and does **not** open the phase editor.
  - Reject that fact: the marker turns struck through, with a "Rejected" tooltip. Restore it.
- Download the Word report: Phase 1 ends with a **Sources** list in the expected format, and the other phases with text show the "Not fact-cited" line.
- Afterwards, restore Phase 1's previous text with the user (phase editor or Insights version history).

Any failure goes back through the normal fix loop.

- [ ] **Step 5 (controller, with the user's OK): One real Generate**

Ask the user before running it (it costs a few cents). Then **Refresh** Phase 1 on BoardTest and check:
- The `/api/chat` request body contains `FACTS BRIEFING` and `# CHECKED FACTS FOR PHASE 1`.
- The new write-up cites `[F#]` / `[C#]` markers, and they link on the card.
- Any claim taken only from the reports reads "(not fact-checked)".
- No rejected claim appears.
- The Word report lists the cited sources.

Note the extra input size (up to about 3,000 tokens) and anything the model did that the rules did not cover.
