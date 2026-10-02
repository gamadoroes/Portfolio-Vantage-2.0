from flask import Blueprint, jsonify, request, session

from services.chat_service import (
    append_message_to_chat,
    create_new_chat,
    delete_chat,
    link_artifact_to_message_by_index,
    rename_chat,
)
from services.project_service import normalize_project_name, project_exists

chats_bp = Blueprint("chats", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


@chats_bp.route("/api/chats", methods=["POST"])
def new_chat():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    chat_id = create_new_chat(project)
    if not chat_id:
        return jsonify({"success": False, "error": "Invalid project"}), 400
    return jsonify({"success": True, "chat_id": chat_id})


@chats_bp.route("/api/chats/<chat_id>", methods=["DELETE"])
def delete_chat_route(chat_id):
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    if delete_chat(project, chat_id):
        return jsonify({"success": True})
    return jsonify({"success": False}), 400


@chats_bp.route("/api/chats/<chat_id>/rename", methods=["POST"])
def rename_chat_route(chat_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    new_name = data.get("name")
    if not project:
        return jsonify({"error": "No project"}), 400
    if rename_chat(project, chat_id, new_name):
        return jsonify({"success": True})
    return jsonify({"success": False}), 400


@chats_bp.route("/api/chats/<chat_id>/append", methods=["POST"])
def append_chat_message(chat_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    role = data.get("role")
    content = data.get("content")
    if not project or not role or content is None:
        return jsonify({"success": False, "error": "Missing fields"}), 400
    if append_message_to_chat(project, chat_id, {"role": role, "content": content}):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Chat not found"}), 404


@chats_bp.route("/api/chats/<chat_id>/link-artifact", methods=["POST"])
def link_artifact_to_message(chat_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    index = data.get("index")
    artifact_id = data.get("artifact_id")
    if project is None or index is None or not artifact_id:
        return jsonify({"success": False, "error": "Missing fields"}), 400

    try:
        idx = int(index)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid index"}), 400

    result = link_artifact_to_message_by_index(project, chat_id, idx, artifact_id)
    if result == "not_found":
        return jsonify({"success": False, "error": "Chat not found"}), 400
    if result == "out_of_range":
        return jsonify({"success": False, "error": "Index out of range"}), 400
    return jsonify({"success": True})
