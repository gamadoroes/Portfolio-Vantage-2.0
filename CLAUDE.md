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

### Frontend

- **Single-page app** — `templates/index.html` + `static/app.js` + `static/style.css`
- No framework; vanilla JS with Marked.js (markdown), DOMPurify (sanitization), Turndown (HTML→MD)
- 6 tabs: Builder, Prompt Developer, Insights, Methodology, Objective, Artifacts
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

## Key Conventions

- **ID formats:** files `f_{8-byte hex}`, chats `{YYYYMMDD}_{HHMMSS}`, artifacts `art_{timestamp}_{4-byte hex}`, research runs `run_{timestamp}_{4-byte hex}`
- **Atomic writes** in `storage.py`: temp file → lock → `os.replace()` → retry with backoff (handles OneDrive locks)
- **API endpoints** follow `/api/{resource}` pattern
- **Project context** stored in Flask session (`current_project`) and client-side localStorage
- **7 research phases** (Landscape, Student, Marketing, Product, Academic, Industry, Options) are hard-coded **client-side only**, in `static/app.js`'s `PHASE_DEFINITIONS` (plus the per-phase prompt/guidance/boundary text also in app.js). The Python backend has no knowledge of phase identity or meaning — it only hides `insights.json` from file listings and prunes dangling references in it. See `docs/ARCHITECTURE.md` §7-10 before moving any of this server-side.

## Configuration

Settings are read in `config.py` from environment variables, or from a `.env` file in the repo root (gitignored). **A real Windows environment variable beats `.env`** (python-dotenv does not override), so a stale variable in Windows can silently win over what `.env` says.
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- `ANTHROPIC_MODEL` (default: `claude-sonnet-4-5-20250929`)
- `OPENAI_DEEP_RESEARCH_MODEL` (default: `o4-mini-deep-research`)
- `FLASK_SECRET_KEY`
- `DATABASE_PATH` and `BACKUP_DIR` (see below)

## Database and backups

- **Where the database lives.** `DATABASE_PATH`, default `C:\Users\<you>\PortfolioVantage\app.db` on Windows (`~/.local/share/PortfolioVantage/app.db` elsewhere). It is deliberately **not** inside OneDrive: syncing a live SQLite file can corrupt it. It is also deliberately **not** under `AppData`: the Microsoft Store build of Python silently redirects writes there into a private package folder that Explorer, backup tools and every other Python cannot see. The path is absolute, so it does not depend on the folder the app is launched from.
- **Schema.** Migrations in `db/migrations/` are applied automatically on the first request after startup (not at import time).
- **Daily snapshots.** Once a day, on a request, the app writes `app-YYYYMMDD-HHMMSS.db` into `BACKUP_DIR` (default `backups/` in the repo, gitignored) and keeps the newest 7. Each is a single, closed, consistent file written with SQLite's backup API, so it is safe to let OneDrive sync them. Only files with that exact name pattern are ever pruned. Code: `db/backup.py`.
- **Restore.** Stop the app, copy a snapshot over the file at `DATABASE_PATH`, delete any `app.db-wal` / `app.db-shm` beside it, start the app.
- **Tests** point `BACKUP_DIR` at a throwaway folder (`tests/conftest.py`) so a test run can never write to or prune real backups.
