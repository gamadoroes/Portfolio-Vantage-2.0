from .project_service import get_project_path
from .storage import read_json, write_json


def load_artifacts(project_name):
    artifacts_file = get_project_path(project_name, "artifacts.json")
    if artifacts_file is None:
        return {}
    return read_json(artifacts_file, {})


def save_artifacts(project_name, artifacts):
    artifacts_file = get_project_path(project_name, "artifacts.json")
    if artifacts_file is None:
        raise ValueError("Invalid project name.")
    write_json(artifacts_file, artifacts)
