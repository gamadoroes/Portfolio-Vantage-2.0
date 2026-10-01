from flask import Blueprint, jsonify, request, session

from services.artifact_service import load_artifacts
from services.research_run_service import load_runs
from services.chat_service import load_chat_sessions
from services.file_service import load_project_files
from services.reference_integrity_service import reconcile_project_references
from services.file_index_service import (
    reconcile_file_index,
    reconcile_selected_file_ids,
)
from services.project_service import (
    create_project,
    normalize_project_name,
    project_exists,
    list_projects_with_metadata,
    load_project_prompt,
    save_project_metadata,
    get_project_metadata,
    list_projects,
)

projects_bp = Blueprint("projects", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


@projects_bp.route("/api/projects/list", methods=["GET"])
def api_list_projects():
    current_project = _existing_project_name(session.get("current_project"))
    projects = list_projects_with_metadata()
    return jsonify({"projects": projects, "current": current_project})


@projects_bp.route("/api/projects/metadata", methods=["GET", "POST"])
def api_project_metadata():
    project_name = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project_name:
        return jsonify({"error": "No project specified"}), 400

    if request.method == "POST":
        save_project_metadata(project_name, request.get_json(silent=True) or {})
        return jsonify({"success": True})

    return jsonify(get_project_metadata(project_name))


@projects_bp.route("/api/projects", methods=["POST"])
def new_project():
    data = request.get_json(silent=True) or {}
    project_name = normalize_project_name(data.get("name"))
    if not project_name:
        return jsonify({"success": False, "error": "Invalid project name."}), 400

    try:
        created = create_project(project_name)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    if not created:
        return jsonify({"success": False, "error": "A project with that name already exists."}), 409

    session["current_project"] = project_name
    return jsonify({"success": True, "project": project_name})


@projects_bp.route("/api/projects/<project_name>", methods=["GET"])
def get_project(project_name):
    project_name = _existing_project_name(project_name)
    if not project_name:
        return jsonify({"error": "Project not found"}), 404

    set_current = request.args.get("set_current", "1").strip().lower()
    if set_current not in {"0", "false", "no"}:
        session["current_project"] = project_name

    # Keep selected_files and phase linked_files aligned with the real files on disk.
    reconcile_project_references(project_name)
    index_entries = reconcile_file_index(project_name)
    selection_state = reconcile_selected_file_ids(project_name, index_entries)

    files = load_project_files(project_name)
    chats = load_chat_sessions(project_name)
    artifacts = load_artifacts(project_name)
    research_runs = load_runs(project_name)
    system_prompt = load_project_prompt(project_name, "system_prompt")
    project_prompt = load_project_prompt(project_name, "project_prompt")
    file_index = {
        file_id: filename
        for file_id, filename in selection_state.get("file_index", {}).items()
        if filename in files
    }
    selected_file_ids = [
        file_id
        for file_id in selection_state.get("selected_file_ids", [])
        if file_id in file_index
    ]
    selected_files = [file_index[file_id] for file_id in selected_file_ids]

    return jsonify(
        {
            "files": files,
            "chats": chats,
            "artifacts": artifacts,
            "research_runs": research_runs,
            "system_prompt": system_prompt,
            "project_prompt": project_prompt,
            "selected_files": selected_files,
            "selected_file_ids": selected_file_ids,
            "file_index": file_index,
        }
    )


@projects_bp.route("/api/switch-project", methods=["POST"])
def switch_project():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project"))
    if not project:
        return jsonify({"error": "Invalid project"}), 400
    session["current_project"] = project
    return jsonify({"success": True, "project": project})
