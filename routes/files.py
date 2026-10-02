from flask import Blueprint, jsonify, request, session

from services.artifact_service import load_artifacts, save_artifacts
from services.docx_service import read_docx
from services.file_index_service import (
    ensure_file_id,
    remove_file_from_index,
    rename_file_in_index,
    toggle_selected_file,
)
from services.file_service import (
    delete_project_file,
    rename_project_file,
    save_project_file,
)
from services.project_service import normalize_project_name, project_exists
from services.reference_integrity_service import (
    remove_file_references,
    replace_file_references,
)

files_bp = Blueprint("files", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


@files_bp.route("/api/files", methods=["POST"])
def save_file():
    # For JSON requests, prefer the explicit project in the body over the
    # Flask session — the session can hold a stale project after switching.
    data = request.get_json(silent=True) if request.is_json else None
    project = _existing_project_name(
        (data or {}).get("project")
        or request.form.get("project")
        or session.get("current_project")
    )

    if not project:
        return jsonify({"success": False, "error": "No project"}), 400

    if "file" in request.files:
        file = request.files["file"]
        filename = file.filename
        if not filename:
            return jsonify({"success": False, "error": "Missing file name"}), 400

        if filename.endswith(".docx"):
            content = read_docx(file)
        else:
            content = file.read().decode("utf-8", errors="ignore")

        try:
            save_project_file(project, filename, content)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        ensure_file_id(project, filename)
        return jsonify({"success": True})

    if data is not None:
        filename = data.get("filename")
        try:
            save_project_file(project, filename, data.get("content"))
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        ensure_file_id(project, filename)
        return jsonify({"success": True})

    return jsonify({"success": False}), 400


@files_bp.route("/api/files/<filename>", methods=["DELETE"])
def delete_file(filename):
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400
    file_id = remove_file_from_index(project, filename)
    delete_project_file(project, filename)
    remove_file_references(project, filename, file_id=file_id)
    return jsonify({"success": True})


@files_bp.route("/api/files/rename", methods=["POST"])
def rename_file():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    old_name = data.get("old_name")
    new_name = data.get("new_name")

    if not project:
        return jsonify({"success": False, "error": "No project"}), 400

    if rename_project_file(project, old_name, new_name):
        rename_file_in_index(project, old_name, new_name)
        replace_file_references(project, old_name, new_name)

        # Update the display name of any artifact whose backing file was renamed.
        # Note: rename_file_in_index() above already renamed the linked `sources`
        # row, and load_artifacts() reconstructs each artifact's "filename" live
        # from that row via the source_id FK -- so by the time we load here, an
        # artifact that *was* backed by old_name already reports new_name, not
        # old_name. Match on new_name (not old_name) to find the artifact(s) whose
        # backing source was just renamed.
        artifacts = load_artifacts(project)
        changed = False
        for art in artifacts.values():
            if art.get("filename") == new_name:
                # Derive a display name from the new filename (strip extension)
                stem = new_name.rsplit(".", 1)[0] if "." in new_name else new_name
                if art.get("name") != stem:
                    art["name"] = stem
                    changed = True
        if changed:
            save_artifacts(project, artifacts)

        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Rename failed. Check the file name and try again."}), 400


@files_bp.route("/api/files/toggle", methods=["POST"])
def toggle_file_selection_route():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    filename = data.get("filename")
    file_id = data.get("file_id")
    selected = toggle_selected_file(project, filename=filename, file_id=file_id)
    if selected is None:
        return jsonify({"success": False, "error": "File not found"}), 404
    return jsonify({"success": True, "selected": selected})
