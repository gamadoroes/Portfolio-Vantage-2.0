# services/tools/status.py
"""Status changes the user makes on the board, and the one the system makes when a run fails.
The Supervisor cannot call this tool at all."""
import json
from typing import Literal

from db.repositories import research_work_items_repo

from .. import research_execution_service, research_task_service
from ..prompt_drafting_service import FALLBACK_MARKER
from ..text_utils import as_text_list
from .activity import record_user_action
from .registry import Tool, ToolError, register, run_tool
from .types import CardId, ToolInput

MIN_PROMPT_CHARS = 40
USER_ACTIONS = ("approve", "back_to_draft", "skip", "restore", "retry", "accept", "mark_failed", "needs_followup")
SYSTEM_ACTIONS = ("run_failed",)
CHANGED = "This research has changed since the board was loaded. The board has been refreshed."
WAITS = "This research waits for another research to finish first."
_FRIENDLY = (
    ("dependency", WAITS),
    ("Retry limit", "This research has failed too many times to retry. Move it back to draft to change it, or skip it."),
)
# Limits of create_followup_task's input, so the child call after the status change cannot fail validation.
FOCUS_ITEMS = 10
FOCUS_ITEM_CHARS = 120
TITLE_CHARS = 200
RATIONALE_CHARS = 1000


class StatusInput(ToolInput):
    task_id: CardId
    action: Literal["approve", "back_to_draft", "skip", "restore", "retry", "accept", "mark_failed",
                    "needs_followup", "run_failed"]


def _require(card, allowed):
    if card["status"] not in allowed:
        raise ToolError("conflict", CHANGED)


def _transition(card_id, to_status):
    try:
        research_task_service.transition_task(card_id, to_status)
    except ValueError as exc:
        for needle, message in _FRIENDLY:
            if needle in str(exc):
                raise ToolError("conflict", message) from exc
        raise ToolError("conflict", str(exc)) from exc


def _claim(card_id, from_status, to_status):
    if not research_task_service.claim_transition(card_id, from_status, to_status):
        raise ToolError("conflict", CHANGED)


def _approve(ctx, card):
    _require(card, ("PROPOSED",))
    prompt = (card["prompt_text"] or "").strip()
    if not (card["title"] or "").strip():
        raise ToolError("invalid_input", "Add a title before approving.")
    if card["research_method"] not in research_execution_service.RUNNABLE_METHODS:
        raise ToolError("invalid_input", "Choose how it runs before approving.")
    if FALLBACK_MARKER in prompt:
        raise ToolError("invalid_input", "This prompt is a placeholder because drafting failed. Edit it, or re-draft it from the framework, before approving.")
    if len(prompt) < MIN_PROMPT_CHARS:
        raise ToolError("invalid_input", "The research prompt is too short to send. Describe what the research should find out.")
    if card["research_method"] == "SYNTHESIS" and not research_task_service.phase7_readiness(card["project_id"])["unlocked"]:
        raise ToolError("conflict", research_execution_service.PHASE7_LOCKED_MESSAGE)
    if not research_task_service.dependencies_satisfied(card["id"]):
        raise ToolError("conflict", WAITS)
    # Only approve the exact card that was validated above; any edit since then wins.
    if not research_work_items_repo.claim_status_if_unchanged(card["id"], "PROPOSED", "READY", card["updated_at"]):
        raise ToolError("conflict", CHANGED)
    record_user_action(ctx, "user_approve", card["id"])
    return "READY"


def _back_to_draft(ctx, card):
    _require(card, ("READY", "FAILED"))
    if card["status"] == "READY":
        _claim(card["id"], "READY", "PROPOSED")
    else:
        _transition(card["id"], "PROPOSED")
    record_user_action(ctx, "user_back_to_draft", card["id"])
    return "PROPOSED"


def _simple(allowed, to_status, decision):
    def action(ctx, card):
        _require(card, allowed)
        _transition(card["id"], to_status)
        record_user_action(ctx, decision, card["id"])
        return to_status
    return action


def _json_text_list(raw):
    try:
        return as_text_list(json.loads(raw) if raw else [])
    except ValueError:
        return []


def _needs_followup(ctx, card):
    _require(card, ("WAITING_FOR_HUMAN",))
    _transition(card["id"], "REVIEWING")
    _transition(card["id"], "FOLLOW_UP_REQUIRED")
    gaps = _json_text_list(card["identified_gaps_json"])
    rationale = "You asked for a follow-up." + (f" Gaps found: {'; '.join(gaps)}" if gaps else "")
    focus = [item[:FOCUS_ITEM_CHARS] for item in _json_text_list(card["entities_json"])][:FOCUS_ITEMS]
    child = run_tool("create_followup_task", ctx.caller, ctx.project_name,
                     {"task_id": card["id"], "title": f"Follow-up: {card['title']}"[:TITLE_CHARS], "focus": focus,
                      "rationale": rationale[:RATIONALE_CHARS]}, parent_call_id=ctx.call_id)
    if not child.ok:
        raise ToolError(child.error["code"], child.error["message"])
    record_user_action(ctx, "user_needs_followup", card["id"])
    return "FOLLOW_UP_REQUIRED"


def _run_failed(ctx, card):
    _require(card, ("RUNNING",))
    _transition(card["id"], "FAILED")
    return "FAILED"


_ACTIONS = {
    "approve": _approve,
    "back_to_draft": _back_to_draft,
    "skip": _simple(("PROPOSED", "READY", "FAILED"), "SKIPPED", "user_skip"),
    "restore": _simple(("SKIPPED",), "PROPOSED", "user_restore"),
    "retry": _simple(("FAILED",), "READY", "user_retry"),
    "accept": _simple(("WAITING_FOR_HUMAN",), "COMPLETE", "user_accept"),
    "mark_failed": _simple(("WAITING_FOR_HUMAN",), "FAILED", "user_mark_failed"),
    "needs_followup": _needs_followup,
    "run_failed": _run_failed,
}


def update_task_status(ctx, inputs):
    allowed = USER_ACTIONS if ctx.caller == "user" else SYSTEM_ACTIONS
    if inputs.action not in allowed:
        raise ToolError("not_allowed", f"{inputs.action} cannot be used by the {ctx.caller}.")
    card = research_work_items_repo.get(inputs.task_id)
    return {"task_id": inputs.task_id, "new_status": _ACTIONS[inputs.action](ctx, card)}


register(Tool(
    name="update_task_status",
    description="Change a research card's status (the person's board actions, or the system marking a failed run).",
    input_model=StatusInput, handler=update_task_status,
    callers=frozenset({"user", "system"}), id_fields=("task_id",),
))
