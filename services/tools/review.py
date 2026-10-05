# services/tools/review.py
"""The Supervisor's review of one finished research. Creating a follow-up or asking for the
person's review happen as child tool calls, so they are checked and logged the same way."""
from typing import Literal

from pydantic import Field

from db.repositories import research_work_items_repo

from .. import research_task_service
from .registry import Tool, ToolError, register, run_tool
from .types import CardId, Text, Title, ToolInput, text_list

Outcome = Literal["COMPLETE", "FOLLOW_UP_REQUIRED", "NEEDS_HUMAN", "FAILED"]


class FollowupSuggestion(ToolInput):
    title: Title
    focus: text_list(10, 120) = []
    research_method: Literal["TARGETED_WEB", "FILE_ANALYSIS"] | None = None
    rationale: Text(1000) = ""


class EvaluateInput(ToolInput):
    task_id: CardId
    completeness_score: float = Field(ge=0, le=1, description="Did it answer the prompt? 0 to 1.")
    evidence_score: float = Field(ge=0, le=1, description="Are its claims sourced? 0 to 1.")
    identified_gaps: text_list(20, 500) = []
    outcome: Outcome = Field(description="COMPLETE if good enough; FOLLOW_UP_REQUIRED if specific gaps need another "
                                         "research (describe it in followup); NEEDS_HUMAN if a person must judge it; "
                                         "FAILED if it produced nothing usable.")
    reason: Text(2000) = ""
    followup: FollowupSuggestion | None = None


def _child(ctx, name, inputs):
    result = run_tool(name, ctx.caller, ctx.project_name, inputs, parent_call_id=ctx.call_id)
    if not result.ok:
        raise ToolError(result.error["code"], result.error["message"], result.error.get("fields"))
    return result.data


def evaluate_research_output(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    if card["status"] != "REVIEWING":
        raise ToolError("conflict", "This research is not being reviewed right now.")
    research_task_service.set_review_scores(
        inputs.task_id, completeness_score=inputs.completeness_score,
        evidence_score=inputs.evidence_score, identified_gaps=inputs.identified_gaps,
    )
    followup_id = None
    if inputs.outcome == "FOLLOW_UP_REQUIRED" and inputs.followup is not None:
        followup_id = _child(ctx, "create_followup_task",
                             {"task_id": inputs.task_id, **inputs.followup.model_dump()})["followup_task_id"]
    if inputs.outcome == "NEEDS_HUMAN" or (inputs.outcome == "COMPLETE" and card["human_review_required"]):
        # request_human_review's reason is shorter than this tool's; clip so a long reason can't fail the review.
        new_status = _child(ctx, "request_human_review",
                            {"task_id": inputs.task_id, "reason": inputs.reason[:1000]})["new_status"]
    else:
        research_task_service.transition_task(inputs.task_id, inputs.outcome)
        new_status = inputs.outcome
    return {"task_id": inputs.task_id, "outcome": inputs.outcome, "new_status": new_status,
            "followup_task_id": followup_id}


register(Tool(
    name="evaluate_research_output",
    description="Record your review of the finished research you were shown.",
    input_model=EvaluateInput, handler=evaluate_research_output,
    callers=frozenset({"supervisor"}), id_fields=("task_id",),
))
