# Portfolio Vantage 2.0 — Current Architecture

> Written for the team reworking this app. Describes the system **as it exists today** on branch `vantage-fresh`, not as aspirationally described elsewhere. Where documentation (including the repo's own `CLAUDE.md`) and the actual code disagree, this document follows the code and calls out the discrepancy explicitly.

## 1. What this app is

A Flask single-page app for competitive landscape research in Australian higher education. A user picks/creates a "project" (one per academic program being researched), uploads source documents, chats with Claude about them, and optionally kicks off OpenAI "deep research" jobs that search the web and return long-form reports. Everything is stored as JSON/text files under `projects/{name}/` — there is no database.

## 2. Request flow

```
Browser (templates/index.html + static/app.js)
    │  fetch() / EventSource (SSE)
    ▼
Flask route (routes/*.py, registered as blueprints in app.py)
    │
    ▼
Service (services/*.py)
    │
    ▼
File system: projects/{project}/*.json, *.txt, files/*
```

All six route blueprints are registered in `app.py:18-24`: `main`, `projects`, `files`, `chats`, `artifacts`, `prompts`, `ai`.

## 3. Routes (by blueprint)

### `routes/main.py` — `main_bp`
- `GET /` (main.py:8-19) — lists projects (`project_service.list_projects`), reads/sets `session["current_project"]`, renders `templates/index.html`.
- `GET /api/config/status` (main.py:22-32) — reports whether `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are configured, and the configured deep-research model.

### `routes/projects.py` — `projects_bp`
- `GET /api/projects/list` — `project_service.list_projects_with_metadata()`.
- `GET|POST /api/projects/metadata` — get/save `metadata.json` (`{description, archived}`).
- `POST /api/projects` — creates `projects/{name}/` with `files/`, `outputs/`, `config.json`, `artifacts.json`.
- `GET /api/projects/<project_name>` — **the main "load project" endpoint**, called by the frontend on project switch/startup. Calls `reference_integrity_service.reconcile_project_references`, `file_index_service.reconcile_file_index` + `reconcile_selected_file_ids`, then returns a combined payload of files/chats/artifacts/research_runs/prompts.
- `POST /api/switch-project` — sets `session["current_project"]`.

### `routes/files.py` — `files_bp`
- `POST /api/files` — upload (multipart or JSON `{filename, content}`); `.docx` goes through `docx_service.read_docx`; saved via `file_service.save_project_file`; gets a stable ID via `file_index_service.ensure_file_id`.
- `DELETE /api/files/<filename>` — removes from index, deletes from disk, scrubs stale refs via `reference_integrity_service.remove_file_references`.
- `POST /api/files/rename` — renames on disk + index + references; patches any artifact pointing at the old filename.
- `POST /api/files/toggle` — toggles a file's selected/source status (`file_index_service.toggle_selected_file`).

### `routes/chats.py` — `chats_bp`
- `POST /api/chats` — `chat_service.create_new_chat`.
- `DELETE /api/chats/<chat_id>`, `POST /api/chats/<chat_id>/rename`, `POST /api/chats/<chat_id>/append`, `POST /api/chats/<chat_id>/link-artifact` — all do direct read-modify-write on `chat_history.json` via `storage.update_json` with inline updater closures (bypassing `chat_service` for these specific ops — worth normalizing in a rework).

### `routes/artifacts.py` — `artifacts_bp`
- `POST /api/artifacts` — create/update an artifact. ID format `art_{epoch}_{4-byte hex}`. Persists content as a backing `.md` file in `files/` plus an entry in `artifacts.json`. Uses a **per-process, per-project `threading.Lock`** (artifacts.py:19-28) to serialize concurrent saves — does not hold across multiple worker processes.
- `DELETE /api/artifacts/<artifact_id>` — deletes backing file + index entry + references + artifact entry.
- `POST /api/artifacts/migrate` — one-off migration for legacy artifacts with no backing file.

### `routes/prompts.py` — `prompts_bp`
- `POST /api/prompts/<prompt_type>` — `project_service.save_project_prompt`, writes `{prompt_type}.txt` (e.g. `system_prompt.txt`, `project_prompt.txt`).

### `routes/ai.py` — `ai_bp` (largest file; LLM + deep research orchestration)
- `POST /api/chat` (ai.py:318-471) — SSE streaming chat. Builds a multi-block Anthropic `system` array with `cache_control: ephemeral` per block (role/tone, objective, files-context, custom rules, prior-phase-insights — ai.py:392-444). Streams via `llm_service.stream_chat_completion`; persists via `chat_service.append_message_to_chat`. Supports `stateless` mode (one-shot phase/insight generation, not saved to history) and `insight_type` (`"competitors"`, `"phase-N"`) which tunes `_max_tokens_for_request`/`_temperature_for_request` (ai.py:84-106).
- `POST /api/edit` (ai.py:474-559) — non-streaming rewrite/compose, via `llm_service.edit_completion`.
- `POST /api/prompt-dev` (ai.py:562-589) — backs the Prompt Developer tab; strips "token budget" sections from generated prompts (`_strip_prompt_budget_sections`, ai.py:109-141).
- `POST /api/deep-research/start` (ai.py:592-638) — creates a deep research run (see §6).
- `GET /api/deep-research/status/<response_id>` (ai.py:641-698) — polling endpoint (see §6).
- `GET /api/research-runs` (ai.py:701-707) — lists runs for a project.
- `POST /api/research-runs/<run_id>/cancel` (ai.py:710-717).
- `POST /api/research-runs/<run_id>/complete` (ai.py:720-730) — links a run to an artifact ID after the frontend has already created the artifact (see §6, step 5).
- `POST /api/insights/populate-competitor` (ai.py:733-812) — one-shot Claude call constrained to supplied source data, returns strict JSON for the competitor-table UI.
- `POST /api/insights/report` (ai.py:815-835) — **Phase 7 consolidation**: takes the full `insights` object from the frontend, calls `docx_service.generate_insights_report`, streams back a `.docx`.

`routes/__init__.py` is empty — blueprints are imported directly by `app.py`.

## 4. Services

| Service | Responsibility | Store touched |
|---|---|---|
| `storage.py` | Atomic JSON/text I/O primitive. Per-path `threading.Lock` registry. `write_json`/`write_text` do `tempfile.mkstemp` → `os.replace()` with retry/backoff for OneDrive sync-lock `PermissionError`s, falling back to a direct write if replace keeps failing. `update_json(path, updater, default)` is the read-modify-write primitive used everywhere a file needs atomic mutation. | any path |
| `project_service.py` | Project/path resolution and validation. `normalize_project_name`/`normalize_filename` reject path traversal, invalid/control chars, trailing space/dot, Windows-reserved device names (CON, PRN, COM1-9, LPT1-9). All path getters verify the resolved path stays inside the parent dir — this is the app's path-traversal guard. Owns `config.json`, `metadata.json`, `{prompt_type}.txt`. | `config.json`, `metadata.json`, `*.txt` |
| `file_service.py` | Thin wrapper over `files/` dir contents (load/save/delete/rename). `rename_project_file` uses `Path.replace()`, not `rename()`, for Windows overwrite semantics. | `files/*` |
| `file_index_service.py` | Stable file IDs (`f_{16-hex}`) decoupled from filenames. `reconcile_file_index` diffs the index against disk. Resolution helpers filter out `HIDDEN_SOURCE_FILES`. `reconcile_selected_file_ids` self-heals `config.json`'s selected-file list against the index. | `file_index.json`, `config.json` |
| `reference_integrity_service.py` | Cleans dangling references on delete/rename, across `config.json.selected_files` **and** `insights.json.phases[*].linked_files`/`linked_file_ids`. `reconcile_project_references` runs on every project load. Note: this service only **prunes** phase links — nothing in the backend ever **adds** a phase link. | `config.json`, `insights.json` (prune only) |
| `chat_service.py` | `chat_history.json` CRUD. IDs `{YYYYMMDD}_{HHMMSS}_{4-byte hex}`. `load_chat_sessions_locked` gives `/api/chat` a consistent snapshot to avoid racing a concurrent append from another tab. | `chat_history.json` |
| `artifact_service.py` | Trivial load/save of `artifacts.json`. | `artifacts.json` |
| `llm_service.py` | Anthropic wrapper: `stream_chat_completion`, `edit_completion`, `prompt_completion`. `_sanitize_messages` strips non-API fields (e.g. `artifact_id`) before sending to Anthropic. | — (API client only) |
| `openai_service.py` | OpenAI wrapper, 2 functions: `start_deep_research(prompt)` (Responses API, `background=True`, `store=True`, `tools=[{"type":"web_search_preview"}]`); `retrieve_deep_research(response_id)`. **No polling loop lives here** — see §5. | — (API client only) |
| `research_run_service.py` | `research_runs.json` CRUD — the run-state tracker. `create_run` → `run_{epoch}_{4-byte hex}`. `is_duplicate_run`: 30s debounce against resubmitting an identical prompt while a run is `queued`/`running`. | `research_runs.json` |
| `docx_service.py` (~850 lines) | `read_docx()` extracts text from uploaded `.docx` source files. The rest is a private Markdown→DOCX renderer (OES branding: Valencia #FF8A00, Ink #001738, Sky #82CBD4 — cover page, competitor table, rating dots, confidence mapping) behind the single public entry point `generate_insights_report(data, project_name)`, used by Phase 7 consolidation. | — (stateless renderer) |

## 5. Data model

Each `projects/{name}/` directory (confirmed against real project data, not just spec):

```
projects/{name}/
├── config.json           # {name, created, selected_files[], selected_file_ids[]}
├── metadata.json          # {description, archived}
├── chat_history.json      # all chat sessions
├── artifacts.json         # generated output documents, keyed by art_{epoch}_{4hex}
├── file_index.json        # {version, files: {f_{16hex}: {filename, created_at, updated_at}}}
├── research_runs.json     # keyed by run_{epoch}_{4hex}, see §6
├── system_prompt.txt       # optional
├── project_prompt.txt      # optional
├── files/                 # uploaded + generated source documents (incl. artifact backing .md files)
└── outputs/
```

**`insights.json`, `insights_history.json`, `excluded_competitors.json`** are documented as "hidden source files" managed internally (`HIDDEN_SOURCE_FILES`, independently duplicated as a literal set in **three** places: `ai.py:30`, `file_service.py:7`, `file_index_service.py` — not shared, worth consolidating in a rework). **None of the 7 real project directories inspected on disk currently contain an `insights.json`** — see §7 for what that implies.

**ID formats:** files `f_{8-byte hex}`; chats `{YYYYMMDD}_{HHMMSS}_{4-byte hex}`; artifacts `art_{epoch}_{4-byte hex}`; research runs `run_{epoch}_{4-byte hex}`.

## 6. Deep Research lifecycle (confirmed end-to-end, backend + frontend)

1. **Request creation** — triggered by `runDeepResearchFromPrompt()` (app.js:3379-3483), called after a prompt has been generated into the Prompt Developer editor. Client-side sequence:
   1. `POST /api/chats` — creates a **brand-new chat session for this run** (deliberate, per an inline comment: keeps artefact-linking clean and stops runs stacking in one session).
   2. `POST /api/chats/{id}/append` — records the user's prompt text in that chat; has an inline 404-recovery path (app.js:3415-3437) that silently creates a new chat and retries if the chat turned out stale/deleted.
   3. `POST /api/deep-research/start` (ai.py:592-638) with `{prompt, project, chat_id}`. Backend checks `research_run_service.is_duplicate_run` (30s debounce on identical prompt text while a run is `queued`/`running`), calls `openai_service.start_deep_research` (OpenAI Responses API, `background=True` — returns immediately with a `response.id` and initial status), then `research_run_service.create_run` persists a run record in `research_runs.json` keyed by a locally-generated `run_id`. Response to frontend: `{response_id, run_id, status}`.
   4. Frontend writes an **optimistic, not-yet-server-confirmed** local run record into `projectData.research_runs[run_id]` (app.js:3458-3470) and renders it immediately.
2. **Polling** — **no backend poller or webhook exists; polling is entirely client-driven and client-owned.** `startRunPolling(run_id, response_id, project, chat_id)` (app.js:3489-3616) sets a `setInterval` ticking every 5000ms, with: a `busy` guard against overlapping ticks if a request is slow; a 1-hour wall-clock timeout that force-finalizes the run as timed out; a consecutive-failure counter capped at 10 (shows "Reconnecting... (n/10)" before giving up). Each tick calls `GET /api/deep-research/status/<response_id>?run_id=...&project=...` (ai.py:641-698), which re-fetches the full OpenAI response object fresh, remaps OpenAI's status vocabulary (`queued`/`in_progress`/`completed`/`failed`/`cancelled`/`incomplete`) to internal values (`running`/`completed`/`failed`/`cancelled`) via `STATUS_MAP` (ai.py:670-678), and — only if `run_id`+`project` were passed — persists the new status into `research_runs.json`. **The live polling-interval registry (`activeResearchRuns`, app.js:38) is an in-memory, per-browser-tab JS object — it does not survive a page reload, and nothing stops two open tabs from independently polling the same run.**
3. **Result retrieval** — `_extract_deep_research_output` (ai.py:266-315) walks the Responses API's `output[]` array for message content, builds both plain text and a citation-annotated Markdown copy (resolves `url_citation`/`file_citation`/`container_file_citation`/`file_path` annotations into numbered footnotes, dedupes by `(type, url_or_file_id)`), and appends a generated `## Sources` section via `_append_deep_research_sources` (ai.py:238-263) if one isn't already present. On the frontend, when `status === 'completed'`, `output_markdown || output` is pulled from the response; empty output is treated as a failure case ("Completed but returned no output").
4. **Artefact creation** — **not backend-automated**, happens inside the same `'completed'` branch of `startRunPolling`: builds an artefact name `Deep Research - {ISO timestamp, minute precision}`, calls `saveArtifactToServer()` → `POST /api/artifacts` (artifacts.py:71-128) → returns `artifact_id`. The output is also appended as an assistant message onto the same chat via `/api/chats/{chat_id}/append`, but that call's failure is only `console.warn`'d — artefact save is authoritative, chat-append is best-effort.
5. **Phase linking** — two distinct things get called "linking" here and they are **not the same mechanism**:
   - *Run→artefact linking*: `finalizeRun(runId, project, artifactId, null)` → `POST /api/research-runs/<run_id>/complete` (ai.py:720-730), which only stamps `research_runs.json` with `{status:"completed", artifact_id}`.
   - *Chat-message→artefact linking*: looked up by **message index** (not content matching) and sent via `/api/chats/{chat_id}/link-artifact` — deliberately replacing an older, more fragile exact-content-string-matching approach (`findLinkedArtifact`, app.js:388-395, which is still used elsewhere in the app for non-deep-research messages).
   - *Phase linking proper* (associating the resulting artefact/file with one of the 7 `insights.json` phases) is a **fully separate, manual, human-driven step**: `linkFileToPhase()` (app.js:2466-2516), triggered from the Insights tab UI, not from anywhere in the deep-research completion flow. **No code path automatically associates a completed deep-research run with a `PHASE_DEFINITIONS` key** — `triggerDeepResearch(topic)` only pre-fills the Builder prompt text, it sets no phase association.
   - Only after artefact-save → chat-append → finalize → `loadProject({reloadInsights:false})` (full reload) → chat-artifact-link **all** succeed does the code `clearInterval` to stop polling (app.js:3580-3583). If any step throws, the interval is left running and the entire completion sequence retries on the next 5s tick — **there is no idempotency key, so artefact-save/finalize could in principle run twice on a slow partial failure.**
   - **Continuity across reloads**: `resumeActiveRuns()` (app.js:3658-3670) runs at the end of every `loadProject()` call (app.js:4461, i.e. on every project switch and page load). It walks the server's `research_runs.json` view, and for every non-terminal run not already in the in-memory `activeResearchRuns`, calls `startRunPolling` again — this is the *only* recovery mechanism if a tab was closed mid-run. If no browser tab for that project is ever reopened, a completed OpenAI job is **never finalized**, even though OpenAI itself finished it.
6. **Phase 7 consolidation** — a **separate, parallel synthesis path**, not part of the deep-research completion flow above. The frontend assembles the complete insights object (all 7 phases, built client-side — see §9) and `POST`s it to `/api/insights/report` (ai.py:815-835), which is a pure function: `docx_service.generate_insights_report(data, project_name)` → streamed `.docx`. The backend never reads `insights.json` for this step; it only receives whatever JSON the frontend currently holds in memory/localStorage. Deep-research artefacts only enter this pipeline if a human manually links them to a phase via `linkFileToPhase` first (see step 5 above).

## 7. ⚠️ Discrepancy: CLAUDE.md vs. actual code

`CLAUDE.md` describes "6 tabs: Builder, Prompt Developer, **Insights**, Methodology, Objective, Artifacts" and "7 research phases are hard-coded". In the actual `templates/index.html` (line 68), the **Insights tab button exists** (`onclick="showTab('insights')"`), so the tab is real in the UI shell — but:

- Grepping the entire Python backend (`routes/`, `services/`) for `phase`, `insights`, `Landscape`/`Marketing`/`Academic`/etc. returns **zero matches** outside of `ai.py`'s `insight_type` prefix-match on the literal strings `"phase-"` and `"phase-7"` (ai.py:90-93, 102-104), used only to tune token/temperature — the backend has no knowledge of what phase 1–6 *mean*.
- **None of the 7 real project directories on disk contain an `insights.json` file at all** (only `config.json`, `chat_history.json`, `artifacts.json`, `file_index.json`, `research_runs.json`). Either the Insights tab/phase feature isn't being used in practice, or `insights.json` is created lazily by the frontend only once a user interacts with that tab and none of these sample projects have done so.
- The phase names (Landscape, Student, Marketing, Product, Academic, Industry, Options) are not defined anywhere in Python — confirmed they live in `static/app.js`'s `PHASE_DEFINITIONS` object (L66-95), the single source of truth for phase identity; nothing about phases is ever fetched from the backend.
- `insights.json` is written lazily, client-side, the first time a user generates or edits a phase — this is exactly why none of the 7 sample project directories inspected on disk contain one: those projects simply haven't used the Insights tab yet.

**Conclusion: the entire Phase 1–7 workflow — including phase definitions, prompt copy, phase-boundary rules, `insights.json` read/write, phase-to-artifact linking, and Phase 7 data assembly — currently lives in browser JavaScript, not the Python backend.** The backend's only touchpoints with `insights.json` are (a) hiding it from the general file list, and (b) pruning dangling file references from it on delete. §8 and §9 detail what the client-side implementation actually does; §10 lists the specific pieces of business logic this implies need a new backend home.

## 8. Frontend architecture (static/app.js)

A single flat file, **4,835 lines, no ES modules, no build step.** All functions are implicitly global (attached to `window`), wired up via inline `onclick="..."` attributes in HTML strings the JS itself generates. Organized only by `// -----` comment dividers (not enforced boundaries — several "sections" mix unrelated concerns):

| Lines | Section (per in-file comment) | Actually contains |
|---|---|---|
| 1-65 | — | Tab-state persistence (`sessionStorage`), global mutable state (`currentProject`, `currentChat`, `projectData`, `activeResearchRuns`, `activeInsightGenerations`) |
| 66-169 | — | `PHASE_DEFINITIONS` registry + insights-data helpers (`createEmptyInsightsData`, `hasMeaningfulInsightsContent`, `buildPriorPhaseContextForPhase7`) |
| 171-343 | — | Text-parsing helpers for AI output (`inferFieldConfidence`, `parseSuggestedTopicsFromSection`, `parseCompetitorsFromMarkdown` — a hand-rolled markdown table/section parser) |
| 344-440 | "CHAT RENDERING" | `renderChat`, `findLinkedArtifact`, `appendMessage` |
| 442-1043 | "ARTIFACT MANAGEMENT" (mislabeled) | Artifact CRUD + rich-text output editor + an in-browser spreadsheet-like table editor |
| 1045-2167 | (unlabeled) | Insights cache (localStorage), insights history, chat-session bootstrap, **the entire insight-generation / Phase 1-7 engine** (`generateInsights`, `generatePhaseInsight`, `handleInsightResponse`, `normalizeInsightsData`, `renderInsights`) |
| 2168-2215 | "PHASE NAVIGATION BAR" | Scrollspy pill nav via `IntersectionObserver` — pure UI |
| 2217-2297 | — | Per-phase instruction prompt UI + "Refresh All" modal |
| 2299-2458 | — | Competitor detail modal + exclusion list (persisted as `excluded_competitors.json`) |
| 2459-2555 | — | Phase→source-file linking UI (`linkFileToPhase`, `unlinkFileFromPhase`, `renderPhaseLinkedFiles`) |
| 2558-3377 | "STANDARD LOGIC" | `PROMPT_TEMPLATES` registry (Builder-tab prompt forms) — includes the **full text of the Phase 1–7 deep-research prompt templates**, ~750 lines of hardcoded prompt copy (L2569-3301) |
| 3379-3485 | — | Deep-research kickoff (`runDeepResearchFromPrompt`) |
| 3487-3685 | "RESEARCH RUN BACKGROUND POLLING" / "RESEARCH RUNS UI" | `startRunPolling`, `finalizeRun`, `cancelResearchRun`, `resumeActiveRuns`, `renderResearchRuns` |
| 3776-~4380 | — | `sendMessage` (SSE chat/insight streaming consumer), DOCX/report download, editor helpers |
| 4386-~4470 | — | `loadProject` — central project-switch bootstrap; calls `resumeActiveRuns()` at the end |
| 4470-4835 | — | Tab switching, sidebar resize, inline-edit chat (`sendEditMessage` → `/api/edit`), misc UI glue |

There is no separation between rendering, state, and network/orchestration logic anywhere in the file — e.g. a single function like `generatePhaseInsight` builds a prompt string, decides data scoping, mutates global state, *and* manipulates DOM button text all in one body.

## 9. Phase 1–7 workflow (client-side)

- **Registry**: `PHASE_DEFINITIONS` (app.js:66-95) — hardcoded object, keys `"1"`–`"7"`, each `{title, description}`.
- **Data shape**: `currentInsightsData.phases[key] = {title, summary, confidence, evidence_sources, gaps, suggested_topics, linked_files, linked_file_ids}`, seeded empty by `createEmptyInsightsData` (app.js:97-117). This whole object round-trips to disk as `insights.json` via `saveInsightsToFile` (app.js:1919) — persistence is "write the entire JSON blob back," not incremental.
- **Per-phase generation** — `generatePhaseInsight(phaseKey, userInstructions)` (app.js:1429-1787) is the core function:
  - Holds a hardcoded `phaseGuidance` dictionary (app.js:1438-1539, one prompt-focus block of copy per phase) and a hardcoded `phaseBoundaries` dictionary (app.js:1543-1664, `in_scope`/`out_scope`/`required_headings` per phase) — both enforced **only as prompt text**; nothing validates the model's response against this contract.
  - Picks a length target per phase (app.js:1666-1668, a literal ternary chain on `phaseKey`).
  - Assembles one long prompt (role/system framing + guidance + boundary contract + length target + user instructions), stuffs it into the chat input box, and calls `sendMessage(genId)` — **phase generation is implemented as "fake-type a mega-prompt into the chat box and submit it," not a dedicated backend endpoint.**
  - Enforces client-side (app.js:1716-1763): phases 1–6 require ≥1 linked source file (blocks with an `alert` otherwise); phase 7 requires `buildPriorPhaseContextForPhase7()` to return non-empty content. Phase 7 explicitly sets `overrideFileIds = []` / `overrideFiles = []` so the backend does **not** fall back to the project's selected source files — Phase 7 is raw-file-blind and context-only by design.
- **Phase 7 consolidation input**: `buildPriorPhaseContextForPhase7()` (app.js:150-169) concatenates phases 1–6's `summary` text (skipping empty/`"MISSING"` ones) into one markdown blob, **hard-truncated client-side to 120,000 characters** with no backend awareness that truncation happened.
- **Linking phase→source files**: `linkFileToPhase`/`unlinkFileFromPhase`/`renderPhaseLinkedFiles` (app.js:2466-2516) maintain `phase.linked_files` (names) and `phase.linked_file_ids` (stable IDs) in parallel, immediately PATCHing the whole `insights.json` back.
- **"Refresh All" batch orchestration**: `generateInsights(userInstructions)` (app.js:1316-1367) — the closest thing this app has to a workflow engine. Filters phases 1–6 down to those with linked files, runs `generatePhaseInsight` **sequentially in a for-loop with `await`** (one phase at a time, no parallelism), then conditionally runs phase 7. This entire multi-phase pipeline exists only in the open browser tab and is silently abandoned if the tab closes mid-run.
- **Response parsing/merge**: `handleInsightResponse(text, genId)` (app.js:1789-1917) — after the SSE stream completes, extracts a JSON object from the model's free-text (fenced ```json block, else brace-scan fallback), tolerates non-JSON phase output by wrapping raw markdown into a synthetic insight object, re-injects the previously-linked file references (so a model response can't blow away link state), then persists and re-renders. Also guards a project-switch race: if the user navigated away mid-generation, the result saves to the **originating** project, not whatever is currently on screen.
- **Concurrency guard**: `activeInsightGenerations` (app.js:38-55) — "one insight generation per project at a time," enforced purely in the initiating browser tab; a second tab/user on the same project is **not** blocked by this at all.
- **Normalization**: `normalizeInsightsData(data, validFiles, fileIndexMap)` (app.js:1937+) runs on every save and every phase-7 kickoff: fills missing arrays, derives `linked_file_ids` ↔ `linked_files` by cross-lookup when one is missing, strips links to files no longer present in the project.

## 10. Orchestration currently in browser JS (and why it doesn't belong there)

| # | Function / location | What it does | Why it's orchestration, not rendering |
|---|---|---|---|
| 1 | `generateInsights` app.js:1316-1367 | Sequences phases 1–6 then conditionally phase 7, one LLM call at a time | A crash or closed tab mid-sequence silently abandons the batch with no resumable server-side state |
| 2 | `generatePhaseInsight` app.js:1429-1787 | Owns all phase-prompt construction, file-linking fallbacks, phase-7's "no raw files" override | Prompt assembly + business rules (phase boundaries, scoping) are hardcoded client-side; any other client (mobile, API consumer, CLI) would have to reimplement this verbatim |
| 3 | `PROMPT_TEMPLATES` app.js:2569-3301 + `phaseGuidance`/`phaseBoundaries` app.js:1438-1664 | ~900 lines of hardcoded prompt copy and per-phase scope contracts | Domain/product knowledge (what each phase analyzes, in what order) is inseparable from UI code and un-versioned outside git diffs on app.js |
| 4 | `buildPriorPhaseContextForPhase7` app.js:150-169 | Decides which phase summaries are usable, concatenates and hard-truncates at 120k chars | A silent data-aggregation/truncation policy; the backend never knows truncation happened |
| 5 | `handleInsightResponse` app.js:1789-1917 | Parses model free-text for embedded JSON, synthesizes a fallback object on parse failure, decides save target on project-mismatch | Data-integrity / error-recovery logic that determines what gets persisted to `insights.json` |
| 6 | `normalizeInsightsData` app.js:1937+ | Repairs/derives `linked_file_ids` ↔ `linked_files`, strips dangling links | Referential-integrity logic, duplicated client-side with no guarantee it agrees with `reference_integrity_service.py`'s server-side version |
| 7 | `startRunPolling` app.js:3489-3616 | Owns the polling interval, backoff/retry counting, timeout, and the entire completion state machine (save artifact → append chat → link artifact → finalize) | A workflow/state-machine that should live server-side — today a completed OpenAI job is never finalized if the browser tab is closed |
| 8 | `resumeActiveRuns` app.js:3658-3670 + `activeResearchRuns` (app.js:38) | Client-side registry of "which runs am I currently polling" | Run-liveness tracking is per-browser-tab, not server-side — two open tabs double-poll the same run independently |
| 9 | Chat-recovery block in `runDeepResearchFromPrompt`, app.js:3415-3437 | Retries chat creation/append on a 404 by creating a new chat | Error-recovery/retry orchestration across two API calls, done blind to server-side session state |
| 10 | `finalizeRun`'s completion sequence, app.js:3540-3578 | Multi-step, partially non-idempotent write sequence across 4 backend resources, with "leave the interval running to retry on throw" as its only failure strategy | Exactly the kind of multi-resource write sequencing a single backend endpoint should own transactionally instead |
| 11 | `getActiveGenerationForProject` / `activeInsightGenerations`, app.js:38-55 | "One insight generation per project at a time" concurrency lock | Enforced only in the initiating tab — not a real server-side lock, so a second tab/user isn't blocked at all |
| 12 | `parseCompetitorsFromMarkdown` app.js:216-347 | Hand-rolled markdown table/section parser with confidence-inference heuristics | Output-schema enforcement for LLM responses — arguably belongs server-side as structured output/JSON mode rather than client-side regex scraping |

## 11. Orphaned branch: `services/deep_research_service.py`

This file is **not** a stray deleted/untracked file in the current branch — `git status` is clean and `git log` on `vantage-fresh`/`master` never contained it. It exists only on an **unmerged remote branch**: `origin/pre-drastic-changes-2026-02-13`, commit `6de8e00` ("Added ability to run multiple deep research requests"), which forked off an older `origin/master` state (`867ae76`) than the one `vantage-fresh` was later built from.

That commit implements a **job queue** for deep research (`deep_research_jobs.json`, max 5 concurrently `running`, FIFO-started from `queued`), as an alternative/evolution of the single-run-at-a-time model in the current `research_run_service.py`. It was never merged forward, so **the current codebase does not support running more than one deep research request concurrently in a coordinated way** (nothing stops a user from firing several, but there's no queue/cap/sequencing).

**This needs a decision, not a unilateral fix:**
- **Restore it** — port `services/deep_research_service.py` and the matching `routes/ai.py`/`static/app.js` changes from `6de8e00` forward onto current `research_run_service.py`'s data model (the two diverged — `research_runs.json` vs `deep_research_jobs.json` — so this is a merge/reconcile job, not a cherry-pick).
- **Discard it** — if multi-job queuing isn't a requirement for the rework, delete the branch (`origin/pre-drastic-changes-2026-02-13`) deliberately rather than leaving it dangling.

Full diff: `git show 6de8e00`. Full file content: `git show 6de8e00:services/deep_research_service.py`.

## 12. Reusable backend functions (candidates to carry into the rework as-is)

**Already server-side, keep as-is:**
- `storage.py` — the entire atomic-write primitive (`update_json`, `_replace_with_retry`) is storage-backend-agnostic and worth keeping regardless of whether the rework stays file-based.
- `project_service.py`'s path-traversal/name-validation guards (`normalize_project_name`, `normalize_filename`) — pure functions, no Flask dependency beyond `current_app` for path roots.
- `docx_service.py`'s `generate_insights_report` — pure function of `(data, project_name)`, already decoupled from where `data` comes from; this is the piece that should **not** change if phase-assembly moves server-side, since it has no knowledge of `insights.json` already.
- `services/openai_service.py` / `services/llm_service.py` — thin, stateless API wrappers; portable as-is.
- `_extract_deep_research_output` / `_append_deep_research_sources` (ai.py:238-315) — currently route-local helper functions with no Flask dependency; should be promoted into a proper service module since they're the one piece of "real" deep-research business logic currently stranded in a route file.

**Currently client-side JS, but pure (no DOM) and directly portable to Python:**
- `PHASE_DEFINITIONS` (app.js:66-95) — pure data; trivially becomes a backend constant/config.
- `createEmptyInsightsData` (app.js:97-117) — pure data-shape constructor.
- `hasMeaningfulInsightsContent` (app.js:119-128) — pure predicate.
- `buildPriorPhaseContextForPhase7` (app.js:150-169) — pure string-building over the insights object; only needs it passed as a parameter instead of read off a module global.
- `inferFieldConfidence` (app.js:171-187), `parseSuggestedTopicsFromSection` (app.js:189-214), `parseCompetitorsFromMarkdown` (app.js:216-~340) — pure text parsers, no DOM; directly re-implementable in Python and arguably *should* move server-side (see finding #12 above).
- `normalizeInsightsData` (app.js:1937+) — pure data-repair function over `(data, validFiles, fileIndexMap)`; already written with only 3 explicit params, no global/DOM reads — near drop-in portable today.
- `phaseGuidance`/`phaseBoundaries` dictionaries + the prompt-assembly logic inside `generatePhaseInsight` (app.js:1438-1707) — pure string templating keyed by `phaseKey`; the DOM-touching lines (status text, button state) are easily separable from prompt-string construction, making this a good candidate for a `build_phase_prompt(phase_key, user_instructions)` backend function.
- The **shape** (not the DOM-coupled implementation) of the `startRunPolling`/`finalizeRun` state machine — the sequence of status checks and terminal-state handling is portable logic; a backend poller/worker replicating it would eliminate findings #7 and #8 in §10.

## 13. Repo hygiene (checked against current `vantage-fresh` HEAD)

- `.gitignore` already excludes `.venv/`, `venv/`, `ENV/`, `.env`/`.env.*`, and `projects/` (user data). **Confirmed via full history scan (`git log --all --diff-filter=A`) that `.venv` and `.env` have never been committed to this branch's history** — nothing to remove.
- `git status` is clean; no untracked or modified files at HEAD.
- `projects/` (containing real, non-public program-research data for Bach Food & Nutrition, International Student MBAs, several MOL/Mast programs, etc.) is correctly untracked.
- No deleted-but-still-tracked files under `services/` on this branch (see §11 for the one *unmerged-branch* service file, which is a different situation — never committed here, not a local deletion).
