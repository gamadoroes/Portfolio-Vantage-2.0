import secrets
from datetime import datetime

from .project_service import get_project_path
from .storage import read_json, update_json, write_json


def _chats_path(project_name):
    return get_project_path(project_name, "chat_history.json")


def load_chat_sessions(project_name):
    chats_path = _chats_path(project_name)
    if chats_path is None:
        return {}
    return read_json(chats_path, {})


def load_chat_sessions_locked(project_name):
    """Read chat sessions while holding the file lock.

    Use this when the caller needs a consistent snapshot that won't be stale
    due to a concurrent ``update_json`` write (e.g. when building LLM context
    while another request is appending messages).
    """
    if not project_name:
        return {}
    from .storage import _get_lock, _read_json_reliable
    chats_file = _chats_path(project_name)
    if chats_file is None:
        return {}
    lock = _get_lock(chats_file)
    with lock:
        return _read_json_reliable(chats_file, {})


def save_chat_sessions(project_name, sessions):
    chats_file = _chats_path(project_name)
    if chats_file is None:
        raise ValueError("Invalid project name.")
    write_json(chats_file, sessions)


def create_new_chat(project_name):
    """Atomically create a new chat session and return its ID."""
    chats_file = _chats_path(project_name)
    if chats_file is None:
        return None
    new_id = [None]

    def updater(sessions):
        chat_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
        while chat_id in sessions:
            chat_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
        sessions[chat_id] = {
            "name": f"Research {datetime.now().strftime('%b %d, %H:%M:%S')}",
            "messages": [],
            "created": datetime.now().isoformat(),
        }
        new_id[0] = chat_id

    update_json(chats_file, updater, default={})
    return new_id[0]


def append_message_to_chat(project_name, chat_id, message):
    """Atomically append a single message to a chat session.

    Returns True if the message was appended, False if the chat was not found.
    """
    chats_file = _chats_path(project_name)
    if chats_file is None:
        return False
    found = [False]

    def updater(sessions):
        if chat_id in sessions:
            sessions[chat_id]["messages"].append(message)
            found[0] = True

    update_json(chats_file, updater, default={})
    return found[0]
