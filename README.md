# Research Architect

Local-first Flask app for managing research projects, source files, chats, artifacts, and AI-assisted outputs.

## Quick Start

### Windows

1. Double-click `run.bat`.
2. Wait for dependency installation to complete.
3. Open `http://localhost:5000`.

`run.bat` creates a local `.venv` automatically if one does not exist.

### Manual Setup

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements_flask.txt
.\.venv\Scripts\python app.py
```

## Runtime Notes

- The app stores local project data under `projects/`.
- The default port is `5000`.
- Set `PORT` to change the port.
- Set `FLASK_DEBUG=1` only when you explicitly want debug mode.

## Pinned Dependencies

Runtime versions are pinned in `requirements_flask.txt` so another local user gets the same SDK behavior you tested against.
