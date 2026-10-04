import json

from flask import Blueprint, jsonify, request, session

from db.repositories import projects_repo, research_work_items_repo
from services import research_task_service
from services.project_service import normalize_project_name, project_exists

research_tasks_bp = Blueprint("research_tasks", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


def _serialize_task(row):
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "phase_key": row["phase_key"],
        "title": row["title"],
        "objective": row["objective"],
        "status": row["status"],
        "priority": row["priority"],
        "research_method": row["research_method"],
        "entities": json.loads(row["entities_json"]) if row["entities_json"] else [],
        "expected_output": row["expected_output"],
        "source_requirements": json.loads(row["source_requirements_json"]) if row["source_requirements_json"] else [],
        "completeness_score": row["completeness_score"],
        "evidence_score": row["evidence_score"],
        "identified_gaps": json.loads(row["identified_gaps_json"]) if row["identified_gaps_json"] else [],
        "retry_count": row["retry_count"],
        "max_retries": row["max_retries"],
        "human_review_required": bool(row["human_review_required"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@research_tasks_bp.route("/api/research-tasks", methods=["POST"])
def create_task_route():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "error": "Title is required"}), 400

    project_id = projects_repo.get_or_create_id(project)
    try:
        task_id = research_task_service.create_task(
            project_id,
            data.get("phase_key"),
            title,
            objective=data.get("objective"),
            priority=data.get("priority"),
            research_method=data.get("research_method"),
            entities=data.get("entities"),
            expected_output=data.get("expected_output"),
            source_requirements=data.get("source_requirements"),
            human_review_required=bool(data.get("human_review_required", False)),
            max_retries=data.get("max_retries", 3),
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "id": task_id})


@research_tasks_bp.route("/api/research-tasks", methods=["GET"])
def list_tasks_route():
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400
    project_id = projects_repo.get_or_create_id(project)
    phase_key = request.args.get("phase_key")
    if phase_key:
        rows = research_work_items_repo.list_for_phase(project_id, phase_key)
    else:
        rows = research_work_items_repo.list_for_project(project_id)
    return jsonify({"tasks": [_serialize_task(r) for r in rows]})


@research_tasks_bp.route("/api/research-tasks/<int:task_id>/transition", methods=["POST"])
def transition_task_route(task_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"success": False, "error": "Missing status"}), 400
    try:
        research_task_service.transition_task(task_id, new_status)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True})


@research_tasks_bp.route("/api/research-tasks/<int:task_id>/dependencies", methods=["POST"])
def add_dependency_route(task_id):
    data = request.get_json(silent=True) or {}
    depends_on_id = data.get("depends_on_task_id")
    if not depends_on_id:
        return jsonify({"success": False, "error": "Missing depends_on_task_id"}), 400
    try:
        research_task_service.add_dependency(task_id, depends_on_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True})


@research_tasks_bp.route(
    "/api/research-tasks/<int:task_id>/dependencies/<int:depends_on_id>", methods=["DELETE"]
)
def remove_dependency_route(task_id, depends_on_id):
    research_work_items_repo.remove_dependency(task_id, depends_on_id)
    return jsonify({"success": True})
