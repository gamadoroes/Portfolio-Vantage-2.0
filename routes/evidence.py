from flask import Blueprint, jsonify, request

from routes.board import _no_project, _request_project
from services import evidence_service, tools
from services.phases import PHASE_DEFINITIONS

evidence_bp = Blueprint("evidence", __name__)
KINDS = ("fact", "conclusion")
STATUS_ACTIONS = ("reject", "restore")
SEARCH_CHARS = 200


def _phase_filter():
    """The optional ?phase= as (phase or None, error response or None)."""
    phase = (request.args.get("phase") or "").strip() or None
    if phase is not None and phase not in PHASE_DEFINITIONS:
        return None, (jsonify({"success": False, "error": "Choose a phase from 1 to 7."}), 400)
    return phase, None


def _tool_response(result):
    if result.ok:
        return jsonify({"success": True, **result.data})
    return jsonify({"success": False, "error": result.error["message"]}), tools.HTTP_STATUS.get(result.error["code"], 500)


@evidence_bp.route("/api/evidence", methods=["GET"])
def list_evidence():
    project, _ = _request_project()
    if not project:
        return _no_project()
    phase, bad_phase = _phase_filter()
    if bad_phase:
        return bad_phase
    return jsonify({"success": True, "phases": evidence_service.list_evidence(project, phase)})


@evidence_bp.route("/api/evidence/search", methods=["GET"])
def search_evidence():
    project, _ = _request_project()
    if not project:
        return _no_project()
    phase, bad_phase = _phase_filter()
    if bad_phase:
        return bad_phase
    inputs = {"words": (request.args.get("q") or "")[:SEARCH_CHARS], "limit": 20}
    if phase is not None:
        inputs["phase_key"] = phase
    return _tool_response(tools.run_tool("search_existing_evidence", "user", project, inputs))


@evidence_bp.route("/api/evidence/<kind>/<int:item_id>/<action>", methods=["POST"])
def set_status(kind, item_id, action):
    project, _ = _request_project()
    if not project:
        return _no_project()
    if kind not in KINDS or action not in STATUS_ACTIONS:
        return jsonify({"success": False, "error": "Unknown action."}), 404
    return _tool_response(tools.run_tool("update_evidence_status", "user", project,
                                         {"kind": kind, "id": item_id, "action": action}))
