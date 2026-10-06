# Fact-cited phase write-ups — Design

**Date:** 2026-10-06
**Status:** Approved in conversation; awaiting written-spec review
**Builds on:** Phase 4b-1 evidence and findings (`2026-10-05-evidence-and-findings-design.md`): `facts`, `conclusions`, `conclusion_facts`, `cited_sources`, `evidence_repo`, `evidence_service`, `static/evidence.js`.

## 1. Goal

The Insights phase write-ups are built from each phase's **checked facts and conclusions**, with citations, so every claim can be traced to a source and date and rejected facts stay out. The citations carry through to the Word report and the Phase 7 options report.

### Decisions already made (by the user)

| Topic | Decision |
|---|---|
| What Generate works from | **Facts first, reports for colour.** Every factual claim cites an active fact or conclusion; the linked reports stay attached for context; anything taken only from the reports is marked "(not fact-checked)"; rejected facts are listed as "do not use". |
| Word report | **Numbered markers + a Sources list** at the end of each phase. |
| Phases with no facts | **Work as today, flagged.** Written from the linked reports exactly as now, with a visible note "Not fact-cited — written from the reports only." |
| Approach | **A: add a server-prepared facts briefing to today's Generate.** Generate's flow (prompt built in the browser, sent through the existing chat route with the linked files, answer saved as the phase write-up) is unchanged. |
| Sample | The user reviewed a sample briefing, write-up, card view and Sources list built from BoardTest Phase 1 and approved it. |

### Out of scope

- Power BI (on hold), targeted search.
- Extracting facts from files the user uploaded (facts still come only from Research-tab reports).
- Moving phase generation server-side (approach B).
- Any change to `services/review_limits.py`, migrations 0001–0007, or how facts are extracted.
- Footnote-style Word citations.

## 2. Citation markers

- `[F<id>]` cites a fact; `[C<id>]` cites a conclusion. `<id>` is the stable database id (the same number the Insights facts list shows as `#12`).
- Several markers may sit together: `[F26] [F28] [F4]`.
- A write-up is **fact-cited** when its summary contains at least one marker. No new column or migration: this is derived from the text.
- The phrase `(not fact-checked)` marks a claim taken only from the attached reports.

## 3. The facts briefing (server)

- New function in `services/evidence_service.py`: `phase_brief(project_id, phase_key) -> {"text": str, "fact_count": int, "conclusion_count": int, "shown_facts": int}`.
- Content, in this order:
  1. `# CHECKED FACTS FOR PHASE <n> (newest first)` — each active fact as `[F<id>] <claim> — <page title>, <publisher if known>, as of <as_of if known>`. Newest first.
  2. `# CONCLUSIONS` — each active conclusion as `[C<id>] <text> (based on F<a>, F<b>, …)` listing its active facts.
  3. `# REJECTED — DO NOT USE` — each rejected fact's claim (no marker), or "(none)".
  4. `RULES:` — cite every factual claim as `[F#]` or `[C#]`; mark anything taken only from the attached reports "(not fact-checked)"; never use a rejected fact; keep markers exactly as written.
- Budget: `MAX_PHASE_BRIEF_CHARS = 12000`. Conclusions and the rejected list are always included first (clipped if they alone exceed the budget); facts fill the rest newest-first; when facts are cut the section ends with `… <n> more facts not shown`.
- Text is flattened to one line per item (no item can start a new heading).
- Route: `GET /api/evidence/brief?project=&phase=` → `{success, brief: {text, fact_count, conclusion_count, shown_facts}}`. Project scoping and the "No project selected." 400 follow `routes/evidence.py`; unknown phase → 400.

## 4. Generate and Refresh All (browser)

- `generatePhaseInsight(phaseKey, …)` in `static/app.js` fetches the brief before building its request.
  - `fact_count > 0`: the brief text is added to the prompt in a clearly headed block, after the phase guidance and before user instructions.
  - `fact_count == 0` or the fetch fails: the prompt is exactly as today (a failed fetch must never block Generate).
- Refresh All calls `generatePhaseInsight` per phase, so it inherits this.
- Phase 7 inside Insights (`buildPriorPhaseContextForPhase7`) gets one extra instruction: keep the `[F#]`/`[C#]` markers from the phase write-ups it draws on.
- Nothing else in the flow changes (linked files, streaming, saving).

## 5. Citations on the phase card

- After the write-up's Markdown is rendered and sanitised, walk its text nodes and turn each `[F<id>]` / `[C<id>]` into a link to `#ev-fact-<id>` / `#ev-concl-<id>` (conclusions get stable ids too), built as DOM nodes (no HTML strings). Markers inside code blocks are left alone.
- Clicking a marker scrolls to the target and opens the fold it is in (reusing `openFoldedBoxes` from `evidence.js`).
- A marker for a fact or conclusion that is now **rejected** shows struck through with a "rejected" tooltip; a marker for an id this project doesn't have stays plain text.
- A write-up with no markers and a non-empty summary shows a small note above the text: "Not fact-cited — written from the reports only."
- Markers must not trigger the card's `openPhaseEditor` click (stop propagation).

## 6. Word report

- `docx_service.generate_insights_report` (called by `/api/insights/report`) adds, after each phase that has markers, a **Sources** heading and one line per distinct cited item, in order of first appearance:
  - fact: `F<id> — <publisher or page title> — <page title> (<as_of>) — <url>`; `(since rejected)` appended for a rejected fact; `(not found)` for an id the project doesn't have.
  - conclusion: `C<id> — Conclusion: <text, clipped to 200 chars> (based on F…)`.
- The lookup is server-side from the database for the report's project.
- Phases without markers get the "Not fact-cited" note line and no Sources list.
- Markers stay in the body text.

## 7. Phase 7 options report (Research tab)

- Its drafting instruction gains: keep the `[F#]`/`[C#]` markers from the phase write-ups it draws on.
- When the options report is saved as a project file, the same Sources list (built by one shared function) is appended at the end, so the saved file stands alone.

## 8. Cost

The brief adds up to ~12,000 characters (~3,000 tokens) of input per Generate — roughly 1–2 cents per phase; Refresh All (7 phases) under 15 cents extra. No new Claude calls.

## 9. Safety

- All model and web text on screen goes in as text (`textContent`/DOM nodes); links only to in-page anchors for markers.
- The brief and Sources lookups are scoped to the current project.
- No change to who may call which tool; no Supervisor menu changes.

## 10. Testing

- **Brief:** layout and order; newest-first; budget with "… n more facts not shown"; conclusions and rejected list always present; rejected facts never in the facts section; one line per item; project scoping; route 400s.
- **Generate:** a pure helper decides the prompt addition — brief added only when `fact_count > 0`; unchanged prompt when 0 or on fetch failure (Node tests).
- **Card:** markers become links for known ids, plain text for unknown, struck through for rejected; markers in code blocks untouched; no HTML strings; the "Not fact-cited" note logic (Node tests with the fake DOM).
- **Word report:** Sources list per phase with markers, order of first appearance, "(since rejected)", "(not found)", conclusion lines; none for phases without markers.
- **Options report:** Sources appended to the saved file.
- **End to end:** browser check on BoardTest, then one real Generate on Phase 1 with the user's OK (a few cents).

## 11. Build order (for the plan)

1. `phase_brief` + route.
2. Shared Sources-list builder (used by the Word report and the options report).
3. Word report Sources lists + "Not fact-cited" line.
4. Options report: marker instruction + appended Sources.
5. Generate: fetch the brief, prompt helper, Phase 7 marker instruction.
6. Phase card: marker links, rejected styling, the note; conclusion anchors.
7. Docs (CLAUDE.md), browser check, real Generate.
