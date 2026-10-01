from flask import Blueprint, jsonify, request, session

from services.project_service import normalize_project_name, project_exists, save_project_prompt

prompts_bp = Blueprint("prompts", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


@prompts_bp.route("/api/prompts/<prompt_type>", methods=["POST"])
def save_prompt(prompt_type):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    save_project_prompt(project, prompt_type, data.get("content"))
    return jsonify({"success": True})
