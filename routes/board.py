from flask import Blueprint, jsonify, request, session

from services import board_service, supervisor_service
from services.project_service import normalize_project_name, project_exists

board_bp = Blueprint("board", __name__)


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


def _request_project():
    data = request.get_json(silent=True) or {}
    raw = data.get("project") or request.args.get("project") or session.get("current_project")
    return _existing_project_name(raw), data


def _no_project():
    return jsonify({"success": False, "error": "No project selected."}), 400


def _error(exc):
    if isinstance(exc, board_service.CardNotFound):
        code = 404
    elif isinstance(exc, board_service.BoardStateError):
        code = 409
    elif isinstance(exc, board_service.DraftingUnavailable):
        code = 503
    elif isinstance(exc, ValueError):
        code = 400
    else:
        code = 500
    message = exc.args[0] if exc.args else str(exc)
    return jsonify({"success": False, "error": str(message)}), code


def _ok(project, **extra):
    return jsonify({"success": True, "board": board_service.get_board_state(project), **extra})


def _draft_note(result):
    execution = result["execution"]
    if result["action"] == "no_action":
        reason = (result["input"] or {}).get("reason")
        return f"Nothing new to propose: {reason}" if reason else "Nothing new to propose right now."
    if not execution["success"]:
        return f"The Supervisor's draft was rejected: {execution['error']}"
    n = len(execution["result"]["created_task_ids"])
    return f"Drafted {n} new {'research' if n == 1 else 'researches'}. Nothing runs until you approve."


@board_bp.route("/api/board", methods=["GET"])
def get_board():
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        return _ok(project)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/refresh", methods=["POST"])
def refresh_board():
    project, data = _request_project()
    if not project:
        return _no_project()
    try:
        state = board_service.refresh(project, defer_linking=bool(data.get("defer_linking")))
        return jsonify({"success": True, "board": state})
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/draft", methods=["POST"])
def draft_researches():
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        result = supervisor_service.draft_researches(project)
        return _ok(project, result=result, note=_draft_note(result))
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards", methods=["POST"])
def create_card():
    project, data = _request_project()
    if not project:
        return _no_project()
    try:
        card_id = board_service.create_card(project, data)
        return _ok(project, card_id=card_id)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards/<int:card_id>", methods=["PATCH"])
def edit_card(card_id):
    project, data = _request_project()
    if not project:
        return _no_project()
    editable = {k: v for k, v in data.items() if k in ("title", "prompt_text", "focus", "research_method", "rationale")}
    try:
        board_service.edit_card(project, card_id, editable)
        return _ok(project)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards/<int:card_id>/extract-facts", methods=["POST"])
def extract_facts(card_id):
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        result = board_service.extract_facts(project, card_id)
        return _ok(project, result=result, note=board_service.extract_note(result))
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards/<int:card_id>/<action>", methods=["POST"])
def card_action(card_id, action):
    project, _ = _request_project()
    if not project:
        return _no_project()
    handler = board_service.ACTIONS.get(action)
    if handler is None:
        return jsonify({"success": False, "error": f"Unknown action: {action}"}), 404
    try:
        handler(project, card_id)
        return _ok(project)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/run", methods=["POST"])
def run_cards():
    project, data = _request_project()
    if not project:
        return _no_project()
    card_ids = data.get("card_ids")
    if not isinstance(card_ids, list):
        return jsonify({"success": False, "error": "card_ids must be a list of research ids"}), 400
    try:
        result = board_service.run(project, card_ids)
        return _ok(project, result=result)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/cards/<int:card_id>/report", methods=["GET"])
def card_report(card_id):
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        return jsonify({"success": True, "report": board_service.get_report(project, card_id)})
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/phase7/draft", methods=["POST"])
def draft_options_report():
    project, _ = _request_project()
    if not project:
        return _no_project()
    try:
        card_id = board_service.draft_options_report(project)
        return _ok(project, card_id=card_id)
    except Exception as exc:
        return _error(exc)


@board_bp.route("/api/board/objective", methods=["PUT"])
def save_objective():
    project, data = _request_project()
    if not project:
        return _no_project()
    try:
        board_service.save_objective(project, data.get("objective") or "")
        return _ok(project)
    except Exception as exc:
        return _error(exc)
