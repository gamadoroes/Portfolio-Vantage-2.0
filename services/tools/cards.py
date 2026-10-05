# services/tools/cards.py
"""Tools that create draft cards or flag a card for the user. Nothing here approves or runs research."""
from typing import Literal

from pydantic import Field

from db.repositories import research_work_items_repo

from .. import research_task_service
from ..prompt_drafting_service import create_drafted_card
from ..prompt_frameworks import resolve_framework
from .activity import record_user_action
from .registry import Tool, ToolError, register
from .types import CardId, Text, Title, ToolInput, text_list

DraftPhase = Literal["1", "2", "3", "4", "5", "6"]
DraftMethod = Literal["TARGETED_WEB", "FILE_ANALYSIS"]
Priority = Literal["low", "medium", "high"]
MAX_TASKS_PER_CALL = 10
SUPERVISOR_AND_USER = frozenset({"supervisor", "user"})
SUPERVISOR_ONLY = frozenset({"supervisor"})


class NewTask(ToolInput):
    phase_key: DraftPhase = Field(description="Phase '1' to '6'. Phase 7 is never drafted here.")
    title: Title
    research_method: DraftMethod = Field(
        description="TARGETED_WEB for public web information; FILE_ANALYSIS when the answer is in the project's uploaded files.")
    focus: text_list(10, 120) = []
    rationale: Text(1000) = Field(default="", description="One or two plain sentences on why this research is needed now.")
    framework_key: Text(60) | None = Field(default=None, description="One of the frameworks listed for this phase.")
    priority: Priority | None = None
    prompt_text: Text(30000) | None = Field(default=None, description="Leave out to have the prompt drafted from the phase framework.")
    draft_prompt: bool = True
    depends_on_existing_ids: list[CardId] = Field(default_factory=list, max_length=20)
    depends_on_batch_indices: list[int] = Field(default_factory=list, max_length=20,
                                                description="Zero-based indices into this same tasks array.")


class CreateResearchTaskInput(ToolInput):
    tasks: list[NewTask] = Field(min_length=1, max_length=MAX_TASKS_PER_CALL)
    reason: Text(2000) = ""


def _has_loop(tasks):
    edges = {i: list(t.depends_on_batch_indices) for i, t in enumerate(tasks)}
    state = {}

    def visit(i):
        if state.get(i) == "visiting":
            return True
        if state.get(i) == "done":
            return False
        state[i] = "visiting"
        if any(visit(j) for j in edges[i]):
            return True
        state[i] = "done"
        return False

    return any(visit(i) for i in edges)


def _check_dependencies(ctx, tasks):
    for t in tasks:
        for dep_id in t.depends_on_existing_ids:
            row = research_work_items_repo.get(dep_id)
            if row is None or row["project_id"] != ctx.project_id:
                raise ToolError("invalid_input", f"depends_on_existing_ids value {dep_id} is not a research in this project",
                                ["depends_on_existing_ids"])
        for index in t.depends_on_batch_indices:
            if index < 0 or index >= len(tasks):
                raise ToolError("invalid_input", f"depends_on_batch_indices value {index} is out of range for a batch of {len(tasks)}",
                                ["depends_on_batch_indices"])
    if _has_loop(tasks):
        raise ToolError("invalid_input", "These researches depend on each other in a loop", ["depends_on_batch_indices"])


def create_research_task(ctx, inputs):
    tasks = inputs.tasks
    _check_dependencies(ctx, tasks)  # everything is checked before anything is created
    new_ids = []
    drafted = 0
    for t in tasks:
        framework = resolve_framework(t.phase_key, t.framework_key)
        if t.prompt_text or not t.draft_prompt:
            card_id = research_task_service.create_task(
                ctx.project_id, t.phase_key, t.title, priority=t.priority, research_method=t.research_method,
                entities=t.focus or None, rationale=t.rationale or None, framework_key=framework,
                prompt_text=t.prompt_text or None,
            )
        else:
            created = create_drafted_card(
                ctx.project_name, t.phase_key, t.title, research_method=t.research_method, focus=t.focus or None,
                rationale=t.rationale or None, framework_key=framework, priority=t.priority,
            )
            card_id = created["card_id"]
            drafted += 1 if created["drafted"] else 0
        new_ids.append(card_id)
        if ctx.caller == "user":
            record_user_action(ctx, "user_add", card_id)
    for i, t in enumerate(tasks):
        for dep_id in t.depends_on_existing_ids:
            research_task_service.add_dependency(new_ids[i], dep_id)
        for index in t.depends_on_batch_indices:
            research_task_service.add_dependency(new_ids[i], new_ids[index])
    return {"created_task_ids": new_ids, "drafted_from_framework": drafted}


class FollowupInput(ToolInput):
    task_id: CardId
    title: Title
    focus: text_list(10, 120) = []
    research_method: DraftMethod | None = None
    rationale: Text(1000) = ""


def create_followup_task(ctx, inputs):
    original = research_work_items_repo.get(inputs.task_id)
    method = inputs.research_method or (
        original["research_method"] if original["research_method"] in ("TARGETED_WEB", "FILE_ANALYSIS") else "TARGETED_WEB"
    )
    created = create_drafted_card(
        ctx.project_name, original["phase_key"], inputs.title, research_method=method, focus=inputs.focus or None,
        rationale=inputs.rationale or f"Fills gaps found in \"{original['title']}\".",
        framework_key=original["framework_key"], suggested_from_work_item_id=original["id"],
    )
    return {"followup_task_id": created["card_id"]}


class HumanReviewInput(ToolInput):
    task_id: CardId
    reason: Text(1000) = ""


def request_human_review(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    research_task_service.flag_for_human_review(inputs.task_id)
    if card["status"] == "REVIEWING":
        research_task_service.transition_task(inputs.task_id, "WAITING_FOR_HUMAN")
        return {"task_id": inputs.task_id, "new_status": "WAITING_FOR_HUMAN"}
    return {"task_id": inputs.task_id, "new_status": card["status"]}


class NoActionInput(ToolInput):
    reason: Text(2000) = ""


def no_action(ctx, inputs):
    return {}


register(Tool(
    name="create_research_task",
    description="Draft one or more researches. Each becomes a card that waits for the person's approval before it can run.",
    input_model=CreateResearchTaskInput, handler=create_research_task, callers=SUPERVISOR_AND_USER,
))
register(Tool(
    name="create_followup_task",
    description="Draft a follow-up research that fills gaps in an existing one. It waits for the person's approval.",
    input_model=FollowupInput, handler=create_followup_task, callers=SUPERVISOR_AND_USER, id_fields=("task_id",),
))
register(Tool(
    name="request_human_review",
    description="Flag a research as needing the person's judgement before it can be accepted.",
    input_model=HumanReviewInput, handler=request_human_review, callers=SUPERVISOR_ONLY, id_fields=("task_id",),
))
register(Tool(
    name="no_action",
    description="Nothing useful can be done right now.",
    input_model=NoActionInput, handler=no_action, callers=SUPERVISOR_ONLY,
))
