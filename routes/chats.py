from flask import Blueprint, jsonify, request, session

from services.chat_service import create_new_chat
from services.project_service import get_project_path, normalize_project_name, project_exists
from services.storage import update_json

chats_bp = Blueprint("chats", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


def _chats_path(project):
    return get_project_path(project, "chat_history.json")


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
def delete_chat(chat_id):
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    chats_path = _chats_path(project)
    if chats_path is None:
        return jsonify({"success": False}), 400

    found = [False]

    def updater(sessions):
        if chat_id in sessions:
            del sessions[chat_id]
            found[0] = True

    update_json(chats_path, updater, default={})
    if found[0]:
        return jsonify({"success": True})
    return jsonify({"success": False}), 400


@chats_bp.route("/api/chats/<chat_id>/rename", methods=["POST"])
def rename_chat(chat_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    new_name = data.get("name")
    if not project:
        return jsonify({"error": "No project"}), 400
    chats_path = _chats_path(project)
    if chats_path is None:
        return jsonify({"error": "No project"}), 400

    found = [False]

    def updater(sessions):
        if chat_id in sessions:
            sessions[chat_id]["name"] = new_name
            found[0] = True

    update_json(chats_path, updater, default={})
    if found[0]:
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
    chats_path = _chats_path(project)
    if chats_path is None:
        return jsonify({"success": False, "error": "Missing fields"}), 400

    found = [False]

    def updater(sessions):
        if chat_id in sessions:
            sessions[chat_id]["messages"].append({"role": role, "content": content})
            found[0] = True

    update_json(chats_path, updater, default={})
    if found[0]:
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
    chats_path = _chats_path(project)
    if chats_path is None:
        return jsonify({"success": False, "error": "Missing fields"}), 400

    try:
        idx = int(index)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid index"}), 400

    error = [None]

    def updater(sessions):
        if chat_id not in sessions:
            error[0] = "Chat not found"
            return
        messages = sessions[chat_id].get("messages", [])
        if idx < 0 or idx >= len(messages):
            error[0] = "Index out of range"
            return
        messages[idx]["artifact_id"] = artifact_id
        sessions[chat_id]["messages"] = messages

    update_json(chats_path, updater, default={})
    if error[0]:
        return jsonify({"success": False, "error": error[0]}), 400
    return jsonify({"success": True})
