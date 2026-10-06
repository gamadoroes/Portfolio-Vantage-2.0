# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Research Architect — a Flask single-page app for competitive landscape analysis in Australian higher education. Integrates Anthropic Claude (chat/edit) and OpenAI deep research APIs. Most application state (chats, artefacts, research runs and tasks, insights history, sources) lives in a SQLite database (see "Database and backups" below); uploaded source documents and some per-project settings remain as files under `projects/{name}/`.

For a full breakdown of routes/services/data model, the Deep Research lifecycle, and which parts of the Phase 1–7 workflow currently live in the backend vs. the browser, see `docs/ARCHITECTURE.md`.

## Commands

```bash
# Install dependencies
pip install -r requirements_flask.txt

# Run the app (starts on http://localhost:5000, debug mode)
python app.py

# Lint (ruff — config in pyproject.toml)
python -m ruff check .          # report issues
python -m ruff check . --fix    # auto-fix safe issues

# Tests (pytest — targets storage, file_index_service, reference_integrity_service)
python -m pytest tests/ -v
```

## Architecture

**Request flow:** Browser (app.js) → Flask route (routes/*.py) → Service (services/*.py) → File system (projects/{project}/) → JSON response or SSE stream back to browser.

### Backend

- **Routes** (`routes/`): Flask blueprints. `ai.py` handles LLM streaming and deep research. `files.py`, `projects.py`, `chats.py`, `artifacts.py` handle CRUD. `prompts.py` manages prompt templates.
- **Services** (`services/`): Business logic layer. Key services:
  - `llm_service.py` — Anthropic Claude streaming with system prompt caching (`cache_control: ephemeral`)
  - `openai_service.py` — OpenAI deep research (async polling model)
  - `storage.py` — Thread-safe atomic JSON/text I/O with retry logic for OneDrive sync locks
  - `file_index_service.py` — Stable file IDs (`f_{hex}`) decoupled from filenames
  - `reference_integrity_service.py` — Cleans stale file references on delete
  - `research_run_service.py` — Tracks deep research run state
  - `docx_service.py` — DOCX report generation with OES branding (Valencia #FF8A00, Ink #001738, Sky #82CBD4)
  - `tools/` — the Supervisor tool layer: every Supervisor action, and the board's status/run actions, go through `tools.run_tool(name, caller, project, inputs)` — pydantic-validated inputs, structured results, a per-tool list of allowed callers (`supervisor` / `user` / `system`) and a log row in `tool_calls`. The Supervisor can only use the tools on its menus (`supervisor_service.DRAFTING_MENU`, `REVIEW_MENU`, `EXTRACT_MENU`); anything that spends money or changes what runs is user/system-only. To let the Supervisor use a tool later, add `"supervisor"` to that tool's `callers` (and to a menu).
  - Facts and conclusions (Phase 4b): right after each review (any outcome but FAILED; never the options report), the system runs `extract_research_facts`. That is one more Claude call, reading up to 60,000 characters of the report (the review itself still sees 8,000, unchanged). It saves the report's key facts (each with the page it came from and the date it applies to) and up to 3 conclusions per phase through `record_research_facts` (`services/tools/research_facts.py`; the smaller `save_source` / `save_evidence` / `create_finding` / `search_existing_evidence` / `update_evidence_status` tools are in `services/tools/evidence.py`). A failed extraction never changes the review. The extraction call has its own time limit (`EXTRACT_TIMEOUT_SECONDS` 240 and `EXTRACT_MAX_RETRIES` 1 in `supervisor_service`; the SDK defaults could outlast `research_runs_repo.STALE_EXTRACTION_MINUTES`), so it always ends well inside the window after which the button comes back. Cards without facts (older reports, or a failed or empty extraction) get an "Extract facts" button: the same tool, called by the user. `research_runs.facts_extracted_at` stops a report being paid for twice. Drafting sees a capped `# KNOWN FACTS` section (`MAX_KNOWN_FACTS_CHARS` in `supervisor_service`). Nothing is deleted; the person can reject and restore facts and conclusions on the Insights tab.

### Frontend

- **Single-page app** — `templates/index.html` + `static/app.js` + `static/style.css`
- No framework; vanilla JS with Marked.js (markdown), DOMPurify (sanitization), Turndown (HTML→MD)
- 6 tabs: Builder, Research, Insights, Methodology, Objective, Artifacts
- **Research** tab (`static/board.js`, `routes/board.py`, `services/board_service.py`): the Supervisor drafts research cards with prompts built from the phase frameworks (`services/prompt_frameworks.json`; a batch's cards are created first, then their prompts are drafted at the same time, up to 4 Claude calls at once, by `prompt_drafting_service.draft_prompts`); the user edits, approves and runs them; finished reports are reviewed automatically and saved as phase-linked sources. Nothing runs without the user's approval.
- **Insights → facts** (`static/evidence.js`, `routes/evidence.py`, `services/evidence_service.py`): below each phase write-up, the phase's conclusions and facts with their sources, Reject / Restore, and a fact search box. Facts show newest first, the newest 10 in view and the rest folded under "Show n more" (a "Based on facts" link opens the fold it needs). Built as DOM nodes with `textContent` only; links only for http/https.
- Chat uses Server-Sent Events (`text/event-stream`) for real-time LLM streaming

### Data Model

Each project directory contains:
- `config.json` — selected files, system/project prompts
- `metadata.json` — description, archived status
- `chat_history.json` — all chat sessions
- `artifacts.json` — generated output documents
- `file_index.json` — stable file ID ↔ filename mapping
- `insights.json` — 7-phase strategic research data (hidden from file list)
- `files/` — user-uploaded source documents

**Hidden source files** (managed internally, excluded from user file lists): `insights.json`, `insights_history.json`, `excluded_competitors.json`.

**Facts and conclusions** live in the database (migration `0007`): `cited_sources`, `facts`, `conclusions`, `conclusion_facts`. These are separate from the older `evidence` / `findings` tables, which back the Insights phase write-ups.

## Key Conventions

- **ID formats:** files `f_{8-byte hex}`, chats `{YYYYMMDD}_{HHMMSS}`, artifacts `art_{timestamp}_{4-byte hex}`, research runs `run_{timestamp}_{4-byte hex}`
- **Atomic writes** in `storage.py`: temp file → lock → `os.replace()` → retry with backoff (handles OneDrive locks)
- **API endpoints** follow `/api/{resource}` pattern
- **Project context** stored in Flask session (`current_project`) and client-side localStorage
- **7 research phases:** definitions are in `services/phases.py` (server) and `PHASE_DEFINITIONS` in `static/app.js` (client, for Insights). The phase research frameworks live server-side in `services/prompt_frameworks.json`.

## Configuration

Settings are read in `config.py` from environment variables, or from a `.env` file in the repo root (gitignored). **A real Windows environment variable beats `.env`** (python-dotenv does not override), so a stale variable in Windows can silently win over what `.env` says.
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- `ANTHROPIC_MODEL` (default: `claude-sonnet-4-5-20250929`)
- `OPENAI_DEEP_RESEARCH_MODEL` (default: `o4-mini-deep-research`)
- `FLASK_SECRET_KEY`
- `DATABASE_PATH` and `BACKUP_DIR` (see below)
- `PROJECTS_DIR` — where project folders live; default `projects/` beside `app.py` (resolved from the app's own location, like the database, so the start folder does not matter). Tests and `scripts/run_board_demo.py` point it at temporary folders.

## Database and backups

- **Where the database lives.** `DATABASE_PATH`, default `C:\Users\<you>\PortfolioVantage\app.db` on Windows (`~/.local/share/PortfolioVantage/app.db` elsewhere). It is deliberately **not** inside OneDrive: syncing a live SQLite file can corrupt it. It is also deliberately **not** under `AppData`: the Microsoft Store build of Python silently redirects writes there into a private package folder that Explorer, backup tools and every other Python cannot see. The path is absolute, so it does not depend on the folder the app is launched from.
- **Schema.** Migrations in `db/migrations/` are applied automatically on the first request after startup (not at import time).
- **Daily snapshots.** Once a day, on a request, the app writes `app-YYYYMMDD-HHMMSS.db` into `BACKUP_DIR` (default `backups/` in the repo, gitignored) and keeps the newest 7. Each is a single, closed, consistent file written with SQLite's backup API, so it is safe to let OneDrive sync them. Only files with that exact name pattern are ever pruned. Code: `db/backup.py`.
- **Restore.** Stop the app, copy a snapshot over the file at `DATABASE_PATH`, delete any `app.db-wal` / `app.db-shm` beside it, start the app.
- **Tests** point `BACKUP_DIR` at a throwaway folder (`tests/conftest.py`) so a test run can never write to or prune real backups.
