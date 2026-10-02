from db.repositories import artefacts_repo, projects_repo, sources_repo

from .project_service import get_project_file_path
from .storage import read_text


def _resolve_filename(project_id, source_id):
    if source_id is None:
        return None
    match = next(
        (s for s in sources_repo.list_for_project(project_id) if s["id"] == source_id), None
    )
    return match["filename"] if match else None


def load_artifacts(project_name):
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    result = {}
    for row in artefacts_repo.list_for_project(project_id):
        filename = _resolve_filename(project_id, row["source_id"])
        content = ""
        if filename:
            path = get_project_file_path(project_name, filename)
            if path is not None:
                content = read_text(path, "")
        result[row["id"]] = {
            "id": row["id"],
            "name": row["name"],
            "filename": filename,
            "content": content,
            "created_at": row["created_at"],
            "type": "text",
        }
    return result


def save_artifacts(project_name, artifacts):
    project_id = projects_repo.get_or_create_id(project_name)
    artifacts = artifacts or {}

    # save_artifacts must fully replace the project's artefact set, matching the old
    # write_json(artifacts.json, artifacts) semantics it replaces: routes/artifacts.py's
    # delete_artifact_route loads the dict, `del`s one entry, and calls save_artifacts
    # expecting that entry gone from storage -- not merely left with a stale row. Delete
    # any existing row whose id is no longer a key in the incoming dict before upserting.
    existing_ids = {row["id"] for row in artefacts_repo.list_for_project(project_id)}
    for stale_id in existing_ids - set(artifacts.keys()):
        artefacts_repo.delete(stale_id)

    for art_id, art in artifacts.items():
        source_id = None
        filename = art.get("filename")
        if filename:
            source_row = sources_repo.get_by_filename(project_id, filename)
            if source_row is not None:
                source_id = source_row["id"]
        artefacts_repo.upsert(art_id, project_id, art.get("name", art_id), source_id=source_id)
