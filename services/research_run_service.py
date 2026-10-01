import secrets
from datetime import datetime, timedelta

from .project_service import get_project_path
from .storage import read_json, update_json, write_json


def _runs_path(project_name):
    return get_project_path(project_name, "research_runs.json")


def load_runs(project_name):
    runs_path = _runs_path(project_name)
    if runs_path is None:
        return {}
    return read_json(runs_path, {})


def save_runs(project_name, runs):
    runs_path = _runs_path(project_name)
    if runs_path is None:
        raise ValueError("Invalid project name.")
    write_json(runs_path, runs)


def create_run(project_name, response_id, chat_id, prompt_text):
    runs_path = _runs_path(project_name)
    if runs_path is None:
        return None

    created_run_id = [None]
    prompt_preview = (prompt_text[:200] + "...") if len(prompt_text) > 200 else prompt_text

    def updater(runs):
        run_id = f"run_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}"
        while run_id in runs:
            run_id = f"run_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}"

        now = datetime.now().isoformat()
        runs[run_id] = {
            "id": run_id,
            "project": project_name,
            "response_id": response_id,
            "chat_id": chat_id,
            "prompt_preview": prompt_preview,
            "status": "running",
            "artifact_id": None,
            "created_at": now,
            "completed_at": None,
            "error": None,
        }
        created_run_id[0] = run_id

    update_json(runs_path, updater, default={})
    return created_run_id[0]


def update_run(project_name, run_id, **fields):
    runs_path = _runs_path(project_name)
    if runs_path is None:
        return False

    found = [False]

    def updater(runs):
        if run_id not in runs:
            return
        runs[run_id].update(fields)
        runs[run_id]["updated_at"] = datetime.now().isoformat()
        found[0] = True

    update_json(runs_path, updater, default={})
    return found[0]


def complete_run(project_name, run_id, artifact_id=None):
    return update_run(
        project_name,
        run_id,
        status="completed",
        artifact_id=artifact_id,
        completed_at=datetime.now().isoformat(),
    )


def fail_run(project_name, run_id, error_message):
    return update_run(
        project_name,
        run_id,
        status="failed",
        error=error_message,
        completed_at=datetime.now().isoformat(),
    )


def cancel_run(project_name, run_id):
    return update_run(
        project_name,
        run_id,
        status="cancelled",
        completed_at=datetime.now().isoformat(),
    )


def is_duplicate_run(project_name, prompt_text, debounce_seconds=30):
    """Check if the same prompt was already submitted recently.

    Prevents accidental double-clicks while allowing intentional parallel
    runs with different prompts from different tabs.
    """
    runs = load_runs(project_name)
    cutoff = datetime.now() - timedelta(seconds=debounce_seconds)
    # Normalize to the same preview format used by create_run
    preview = (prompt_text[:200] + "...") if len(prompt_text) > 200 else prompt_text

    for r in runs.values():
        if r.get("status") not in ("queued", "running"):
            continue
        created = r.get("created_at", "")
        try:
            if datetime.fromisoformat(created) < cutoff:
                continue  # older than debounce window
        except (ValueError, TypeError):
            pass
        if r.get("prompt_preview") == preview:
            return True
    return False
