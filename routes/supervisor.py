import json

from flask import Blueprint, jsonify, request, session

from db.repositories import agent_decisions_repo, projects_repo
from services.project_service import normalize_project_name, project_exists
from services.supervisor_service import run_supervisor_cycle

supervisor_bp = Blueprint("supervisor", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


@supervisor_bp.route("/api/supervisor/run", methods=["POST"])
def run_supervisor_route():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400

    try:
        result = run_supervisor_cycle(project)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify(result)


@supervisor_bp.route("/api/supervisor/decisions", methods=["GET"])
def list_decisions_route():
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400

    project_id = projects_repo.get_or_create_id(project)
    rows = agent_decisions_repo.list_for_project(project_id)
    decisions = [
        {
            "id": row["id"],
            "decision_type": row["decision_type"],
            "detail": json.loads(row["detail"]) if row["detail"] else {},
            "research_work_item_id": row["research_work_item_id"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return jsonify({"decisions": decisions})
