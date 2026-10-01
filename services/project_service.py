from datetime import datetime
from pathlib import Path

from .storage import read_json, write_json, read_text, write_text

_INVALID_PATH_CHARS = set('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def get_projects_dir():
    projects_dir = Path("projects")
    projects_dir.mkdir(exist_ok=True)
    return projects_dir


def _contains_control_chars(value):
    return any(ord(ch) < 32 for ch in value)


def _is_reserved_windows_name(value):
    stem = value.split(".", 1)[0].rstrip(" .").upper()
    return stem in _WINDOWS_RESERVED_NAMES


def _normalize_path_component(value):
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized or normalized in {".", ".."}:
        return None
    if any(ch in _INVALID_PATH_CHARS for ch in normalized):
        return None
    if "\x00" in normalized or _contains_control_chars(normalized):
        return None
    if normalized.endswith((" ", ".")):
        return None
    if _is_reserved_windows_name(normalized):
        return None
    return normalized


def normalize_project_name(project_name):
    return _normalize_path_component(project_name)


def normalize_filename(filename):
    return _normalize_path_component(filename)


def is_valid_project_name(project_name):
    return normalize_project_name(project_name) is not None


def list_projects():
    projects_dir = get_projects_dir()
    projects_dir.mkdir(parents=True, exist_ok=True)
    return sorted(d.name for d in projects_dir.iterdir() if d.is_dir())


def get_project_dir(project_name):
    normalized = normalize_project_name(project_name)
    if normalized is None:
        return None
    projects_dir = get_projects_dir()
    projects_dir.mkdir(parents=True, exist_ok=True)
    projects_dir = projects_dir.resolve()
    project_dir = (projects_dir / normalized).resolve()
    try:
        project_dir.relative_to(projects_dir)
    except ValueError:
        return None
    return project_dir


def get_project_path(project_name, *parts):
    project_dir = get_project_dir(project_name)
    if project_dir is None:
        return None
    target_path = project_dir.joinpath(*parts).resolve()
    try:
        target_path.relative_to(project_dir)
    except ValueError:
        return None
    return target_path


def get_project_files_dir(project_name):
    return get_project_path(project_name, "files")


def get_project_file_path(project_name, filename):
    normalized = normalize_filename(filename)
    files_dir = get_project_files_dir(project_name)
    if normalized is None or files_dir is None:
        return None
    file_path = (files_dir / normalized).resolve()
    try:
        file_path.relative_to(files_dir)
    except ValueError:
        return None
    return file_path


def project_exists(project_name):
    project_dir = get_project_dir(project_name)
    return bool(project_dir and project_dir.is_dir())


def create_project(project_name):
    normalized = normalize_project_name(project_name)
    if normalized is None:
        raise ValueError("Invalid project name.")

    project_dir = get_project_dir(normalized)
    if project_dir is None:
        raise ValueError("Invalid project name.")
    if project_dir.exists():
        return False

    project_dir.parent.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(exist_ok=False)
    (project_dir / "files").mkdir(exist_ok=True)
    (project_dir / "outputs").mkdir(exist_ok=True)

    config = {
        "name": normalized,
        "created": datetime.now().isoformat(),
        "selected_files": [],
        "selected_file_ids": [],
    }
    save_project_config(normalized, config)
    write_json(project_dir / "artifacts.json", {})
    return True


def load_project_config(project_name):
    config_path = get_project_path(project_name, "config.json")
    if config_path is None:
        return {}
    return read_json(config_path, {})


def save_project_config(project_name, config):
    config_path = get_project_path(project_name, "config.json")
    if config_path is None:
        raise ValueError("Invalid project name.")
    write_json(config_path, config)


def get_project_metadata(project_name):
    metadata_file = get_project_path(project_name, "metadata.json")
    if metadata_file is None:
        return {"description": "", "archived": False}
    return read_json(metadata_file, {"description": "", "archived": False})


def save_project_metadata(project_name, metadata):
    metadata_file = get_project_path(project_name, "metadata.json")
    if metadata_file is None:
        raise ValueError("Invalid project name.")
    write_json(metadata_file, metadata)


def list_projects_with_metadata():
    projects = []
    for project_name in list_projects():
        metadata = get_project_metadata(project_name)
        projects.append(
            {
                "name": project_name,
                "description": metadata.get("description", ""),
                "archived": metadata.get("archived", False),
            }
        )
    return projects


def load_project_prompt(project_name, prompt_type):
    prompt_file = get_project_path(project_name, f"{prompt_type}.txt")
    if prompt_file is None:
        return ""
    return read_text(prompt_file, "")


def save_project_prompt(project_name, prompt_type, content):
    prompt_file = get_project_path(project_name, f"{prompt_type}.txt")
    if prompt_file is None:
        raise ValueError("Invalid project name.")
    write_text(prompt_file, content if isinstance(content, str) else str(content or ""))
