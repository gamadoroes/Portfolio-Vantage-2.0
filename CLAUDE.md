# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Research Architect — a Flask single-page app for competitive landscape analysis in Australian higher education. Integrates Anthropic Claude (chat/edit) and OpenAI deep research APIs. All data is file-based (no database); projects live under `projects/{name}/` as JSON and plain text files.

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
- **7 research phases** are hard-coded: Landscape, Student, Marketing, Product, Academic, Industry, Options

## Configuration

API keys and model names are in `config.py`. Currently hardcoded — should use environment variables:
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- `ANTHROPIC_MODEL` (default: `claude-sonnet-4-5-20250929`)
- `OPENAI_DEEP_RESEARCH_MODEL` (default: `o4-mini-deep-research`)
- `FLASK_SECRET_KEY`
