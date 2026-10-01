import re
import secrets
import threading
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from services.artifact_service import load_artifacts, save_artifacts
from services.file_index_service import ensure_file_id, remove_file_from_index, rename_file_in_index
from services.file_service import delete_project_file, rename_project_file, save_project_file
from services.project_service import get_project_files_dir, normalize_project_name, project_exists
from services.reference_integrity_service import (
    remove_file_references,
    replace_file_references,
)

artifacts_bp = Blueprint("artifacts", __name__)

# Per-project locks to serialize artifact saves
_artifact_locks = {}
_artifact_locks_lock = threading.Lock()


def _get_artifact_lock(project):
    with _artifact_locks_lock:
        if project not in _artifact_locks:
            _artifact_locks[project] = threading.Lock()
        return _artifact_locks[project]


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project

def _safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9 _-]", "", name or "").strip()
    if not base:
        base = "artifact"
    base = re.sub(r"\s+", " ", base).strip()
    return base


def _unique_md_filename(project: str, base: str, reserve: bool = True) -> str:
    """Find a unique .md filename. If reserve=True, atomically creates a zero-byte
    placeholder (using exclusive create) to prevent two threads from choosing the same name."""
    files_dir = get_project_files_dir(project)
    if files_dir is None:
        raise ValueError("Invalid project name.")
    files_dir.mkdir(parents=True, exist_ok=True)
    stem = base
    candidate = f"{stem}.md"
    i = 2
    if reserve:
        # Use open(..., 'x') for atomic check-and-create — raises FileExistsError on collision
        while True:
            try:
                (files_dir / candidate).open("x").close()
                return candidate
            except FileExistsError:
                candidate = f"{stem}_{i}.md"
                i += 1
    else:
        while (files_dir / candidate).exists():
            candidate = f"{stem}_{i}.md"
            i += 1
        return candidate


@artifacts_bp.route("/api/artifacts", methods=["POST"])
def save_artifact_route():
    try:
        data = request.get_json(silent=True) or {}
        project = _existing_project_name(data.get("project") or session.get("current_project"))
        if not project:
            return jsonify({"success": False, "error": "No project selected"}), 400

        with _get_artifact_lock(project):
            artifacts = load_artifacts(project)

            artifact_id = data.get("id")
            if not artifact_id:
                artifact_id = f"art_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}"

            existing = artifacts.get(artifact_id)
            name = data.get("name", "Untitled Artifact")
            content = data.get("content", "")

            if existing and existing.get("filename"):
                filename = existing["filename"]
                # Rename file if name changed
                if existing.get("name") != name:
                    new_base = _safe_filename(name)
                    # Use reserve=False first to compute the target name without
                    # creating a zero-byte placeholder that becomes orphaned when
                    # the rename fails (e.g. backing file was already moved).
                    new_filename = _unique_md_filename(project, new_base, reserve=False)
                    if rename_project_file(project, filename, new_filename):
                        rename_file_in_index(project, filename, new_filename)
                        replace_file_references(project, filename, new_filename)
                        filename = new_filename
                    else:
                        # Old file is gone (e.g. renamed via file browser).
                        # Assign the new filename directly — save_project_file
                        # below will create it.
                        filename = new_filename
            else:
                base = _safe_filename(name)
                filename = _unique_md_filename(project, base)

            save_project_file(project, filename, content)
            ensure_file_id(project, filename)

            artifacts[artifact_id] = {
                "id": artifact_id,
                "name": name,
                "content": content,
                "created_at": data.get("created_at", datetime.now().isoformat()),
                "type": data.get("type", "text"),
                "filename": filename,
            }

            save_artifacts(project, artifacts)
        return jsonify({"success": True, "artifact_id": artifact_id})
    except Exception as e:
        print(f"[artifact] save error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@artifacts_bp.route("/api/artifacts/<artifact_id>", methods=["DELETE"])
def delete_artifact_route(artifact_id):
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400

    artifacts = load_artifacts(project)
    if artifact_id in artifacts:
        filename = artifacts[artifact_id].get("filename")
        if filename:
            file_id = remove_file_from_index(project, filename)
            delete_project_file(project, filename)
            remove_file_references(project, filename, file_id=file_id)
        del artifacts[artifact_id]
        save_artifacts(project, artifacts)
        return jsonify({"success": True})
    return jsonify({"success": False}), 400


@artifacts_bp.route("/api/artifacts/migrate", methods=["POST"])
def migrate_artifacts_to_files():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400

    artifacts = load_artifacts(project)
    updated = 0
    for artifact_id, art in artifacts.items():
        if art.get("filename"):
            continue
        name = art.get("name", "Untitled Artifact")
        content = art.get("content", "")
        base = _safe_filename(name)
        filename = _unique_md_filename(project, base)
        save_project_file(project, filename, content)
        ensure_file_id(project, filename)
        art["filename"] = filename
        artifacts[artifact_id] = art
        updated += 1

    save_artifacts(project, artifacts)
    return jsonify({"success": True, "migrated": updated})
