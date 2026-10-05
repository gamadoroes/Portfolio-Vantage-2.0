# services/tools/runs.py
"""Starting research (the user's Run) and checking web runs (the system's refresh).
The Supervisor cannot call any of these."""
from pydantic import Field

from db.repositories import research_runs_repo, research_work_items_repo

from .. import research_execution_service
from .registry import Tool, ToolError, register
from .types import CardId, ToolInput

SYNTHESIS_ELSEWHERE = "The options report is started with its own button."


class LaunchInput(ToolInput):
    task_ids: list[CardId] = Field(min_length=1, max_length=20)


def launch_deep_research(ctx, inputs):
    synthesis, others = [], []
    for card_id in inputs.task_ids:
        (synthesis if research_work_items_repo.get(card_id)["research_method"] == "SYNTHESIS" else others).append(card_id)
    result = (research_execution_service.start_runs(ctx.project_name, others) if others
              else {"started": [], "not_ready": [], "failed": []})
    result["failed"].extend({"id": card_id, "error": SYNTHESIS_ELSEWHERE} for card_id in synthesis)
    return result


class TaskInput(ToolInput):
    task_id: CardId


def generate_synthesis(ctx, inputs):
    if research_work_items_repo.get(inputs.task_id)["research_method"] != "SYNTHESIS":
        raise ToolError("conflict", "This research isn't the options report.")
    return research_execution_service.start_runs(ctx.project_name, [inputs.task_id])


def check_research_run(ctx, inputs):
    run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    if run is None:
        raise ToolError("not_found", "This research has not been run.")
    if research_execution_service.needs_web_sync(run):
        research_execution_service.sync_web_research_run(ctx.project_name, run)
        run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    return {"task_id": inputs.task_id, "run_status": run["status"]}


USER_ONLY = frozenset({"user"})
register(Tool(
    name="launch_deep_research",
    description="Start web or 'my files' research on cards the person has approved. Costs money.",
    input_model=LaunchInput, handler=launch_deep_research, callers=USER_ONLY, id_fields=("task_ids",),
))
register(Tool(
    name="generate_synthesis",
    description="Start the Phase 7 options report on the approved options card.",
    input_model=TaskInput, handler=generate_synthesis, callers=USER_ONLY, id_fields=("task_id",),
))
register(Tool(
    name="check_research_run",
    description="Ask OpenAI whether a web research run has finished, and collect its report if so.",
    input_model=TaskInput, handler=check_research_run, callers=frozenset({"system"}), id_fields=("task_id",),
))
