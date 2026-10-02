import secrets
from datetime import datetime

from db.repositories import chat_repo, projects_repo

from .project_service import normalize_project_name


def _session_for_project(project_name, chat_id):
    """Return the chat_sessions row only if chat_id exists AND belongs to project_name.

    Resolving by chat_id alone would let a chat_id from a different project
    succeed against the wrong project, since chat IDs are globally unique
    strings, not scoped per project the way chat_history.json files used to be.
    """
    session_row = chat_repo.get_session(chat_id)
    if session_row is None:
        return None
    project_id = projects_repo.get_id(project_name)
    if project_id is None or session_row["project_id"] != project_id:
        return None
    return session_row


def _session_to_dict(session_row, messages):
    return {
        "name": session_row["name"],
        "created": session_row["created_at"],
        "messages": [
            {"role": m["role"], "content": m["content"], "artifact_id": m["artefact_id"]}
            for m in messages
        ],
    }


def load_chat_sessions(project_name):
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    sessions = {}
    for session_row in chat_repo.list_sessions_for_project(project_id):
        messages = chat_repo.list_messages(session_row["id"])
        sessions[session_row["id"]] = _session_to_dict(session_row, messages)
    return sessions


def load_chat_sessions_locked(project_name):
    """SQLite transactions already give a consistent read; kept for call-site compatibility."""
    return load_chat_sessions(project_name)


def save_chat_sessions(project_name, sessions):
    # No longer used for bulk writes now that each mutation (create/append) writes
    # directly through chat_repo; kept only so any lingering caller doesn't crash.
    pass


def create_new_chat(project_name):
    normalized = normalize_project_name(project_name)
    if normalized is None:
        raise ValueError("Invalid project name.")
    project_id = projects_repo.get_or_create_id(normalized)
    chat_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    while chat_repo.get_session(chat_id) is not None:
        chat_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    chat_repo.create_session(chat_id, project_id, f"Research {datetime.now().strftime('%b %d, %H:%M:%S')}")
    return chat_id


def append_message_to_chat(project_name, chat_id, message):
    if _session_for_project(project_name, chat_id) is None:
        return False
    chat_repo.append_message(
        chat_id, message.get("role", "user"), message.get("content", ""),
        artefact_id=message.get("artifact_id"),
    )
    return True


def delete_chat(project_name, chat_id):
    if _session_for_project(project_name, chat_id) is None:
        return False
    chat_repo.delete_session(chat_id)
    return True


def rename_chat(project_name, chat_id, new_name):
    if _session_for_project(project_name, chat_id) is None:
        return False
    chat_repo.rename_session(chat_id, new_name)
    return True


def link_artifact_to_message_by_index(project_name, chat_id, index, artifact_id):
    if _session_for_project(project_name, chat_id) is None:
        return "not_found"
    seq = index + 1
    messages = chat_repo.list_messages(chat_id)
    if index < 0 or index >= len(messages):
        return "out_of_range"
    chat_repo.link_artefact_to_message(chat_id, seq, artifact_id)
    return "ok"
