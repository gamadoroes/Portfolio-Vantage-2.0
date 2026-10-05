import secrets
from datetime import datetime, timedelta

from db.repositories import projects_repo, research_runs_repo

from .project_service import normalize_project_name


def _run_for_project(project_name, run_id):
    """Return the research_runs row only if run_id exists AND belongs to project_name.

    Resolving by run_id alone would let a run_id from a different project
    succeed against the wrong project, since run IDs are globally unique
    strings, not scoped per project the way research_runs.json files used to be.
    """
    run_row = research_runs_repo.get(run_id)
    if run_row is None:
        return None
    project_id = projects_repo.get_id(project_name)
    if project_id is None or run_row["project_id"] != project_id:
        return None
    return run_row


def create_run(project_name, response_id, chat_id, prompt_text):
    normalized = normalize_project_name(project_name)
    if normalized is None:
        raise ValueError("Invalid project name.")
    project_id = projects_repo.get_or_create_id(normalized)
    prompt_preview = (prompt_text[:200] + "...") if len(prompt_text) > 200 else prompt_text
    run_id = f"run_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}"
    while research_runs_repo.get(run_id) is not None:
        run_id = f"run_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}"
    research_runs_repo.create(run_id, project_id, response_id, chat_id, prompt_preview)
    return run_id


def load_runs(project_name):
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    return {
        row["id"]: {
            "id": row["id"], "project": project_name, "response_id": row["response_id"],
            "chat_id": row["chat_session_id"], "prompt_preview": row["prompt_preview"],
            "status": row["status"], "artifact_id": row["artefact_id"],
            "created_at": row["created_at"], "completed_at": row["completed_at"],
            "error": row["error"], "updated_at": row["updated_at"],
            "research_work_item_id": row["research_work_item_id"],
        }
        for row in research_runs_repo.list_for_project(project_id)
    }


def update_run(project_name, run_id, **fields):
    if _run_for_project(project_name, run_id) is None:
        return False
    column_map = {"chat_id": "chat_session_id", "artifact_id": "artefact_id"}
    mapped = {column_map.get(k, k): v for k, v in fields.items()}
    return research_runs_repo.update(run_id, **mapped)


def complete_run(project_name, run_id, artifact_id=None):
    return update_run(
        project_name, run_id, status="completed", artifact_id=artifact_id,
        completed_at=datetime.now().isoformat(),
    )


def fail_run(project_name, run_id, error_message):
    return update_run(
        project_name, run_id, status="failed", error=error_message,
        completed_at=datetime.now().isoformat(),
    )


def cancel_run(project_name, run_id):
    return update_run(project_name, run_id, status="cancelled", completed_at=datetime.now().isoformat())


def is_duplicate_run(project_name, prompt_text, debounce_seconds=30):
    """Check if the same prompt was already submitted recently.

    Prevents accidental double-clicks while allowing intentional parallel
    runs with different prompts from different tabs.
    """
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return False
    preview = (prompt_text[:200] + "...") if len(prompt_text) > 200 else prompt_text
    cutoff = (datetime.now() - timedelta(seconds=debounce_seconds)).isoformat()
    return research_runs_repo.find_recent_running_or_queued_with_preview(project_id, preview, cutoff) is not None
