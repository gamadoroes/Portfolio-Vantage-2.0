# services/tools/state.py
"""Read-only tools: the project as the Supervisor sees it, and one card in detail."""
from db.repositories import agent_decisions_repo, research_runs_repo, research_work_items_repo

from .. import insights_service, research_task_service
from ..phases import PHASE_DEFINITIONS
from ..project_service import load_project_prompt
from ..prompt_frameworks import FRAMEWORK_LABELS, frameworks_for_phase
from ..review_limits import MAX_RUN_OUTPUT_REVIEW_CHARS
from ..text_utils import clip_text
from .registry import Tool, register
from .types import CardId, ToolInput

RECENT_DECISIONS = 10
ALL_CALLERS = frozenset({"supervisor", "user", "system"})


def card_dict(row):
    card = dict(row)
    card["dependency_ids"] = [
        r["depends_on_work_item_id"] for r in research_work_items_repo.list_dependencies(row["id"])
    ]
    return card


def _run_dict(run):
    if run is None:
        return None
    return {k: run[k] for k in ("id", "status", "error", "output_text", "prompt_text", "created_at", "completed_at")}


class ProjectStateInput(ToolInput):
    pass


def get_project_state(ctx, inputs):
    current = insights_service.load_current_insights(ctx.project_name)
    summaries = {
        key: phase["summary"] for key, phase in current["phases"].items()
        if phase.get("summary") and phase["summary"] != "MISSING"
    }
    cards = []
    for row in research_work_items_repo.list_for_project(ctx.project_id):
        card = card_dict(row)
        card["latest_run"] = _run_dict(research_runs_repo.find_latest_for_work_item(row["id"]))
        cards.append(card)
    decisions = [
        {"decision_type": d["decision_type"], "detail": d["detail"], "created_at": d["created_at"]}
        for d in agent_decisions_repo.list_for_project(ctx.project_id, limit=RECENT_DECISIONS)
    ]
    return {
        "objective": load_project_prompt(ctx.project_name, "project_prompt") or "",
        "phases": [
            {"key": key, "title": definition["title"],
             "frameworks": [{"key": fk, "label": FRAMEWORK_LABELS[fk]} for fk in frameworks_for_phase(key)]}
            for key, definition in PHASE_DEFINITIONS.items()
        ],
        "phase_summaries": summaries,
        "cards": cards,
        "phase7": research_task_service.phase7_readiness(ctx.project_id),
        "recent_decisions": decisions,
    }


class TaskInput(ToolInput):
    task_id: CardId


def get_task(ctx, inputs):
    row = research_work_items_repo.get(inputs.task_id)
    run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    latest = None
    if run is not None:
        output = run["output_text"] or ""
        latest = {k: run[k] for k in ("id", "status", "error", "prompt_text", "created_at", "completed_at")}
        latest["report_excerpt"] = clip_text(output, MAX_RUN_OUTPUT_REVIEW_CHARS) if output else ""
        latest["report_chars_total"] = len(output)
    return {"card": card_dict(row), "latest_run": latest}


register(Tool(
    name="get_project_state",
    description="Read the whole project: objective, phases and their frameworks, every research card "
                "with its latest run, existing phase findings, Phase 7 readiness and recent decisions.",
    input_model=ProjectStateInput, handler=get_project_state, callers=ALL_CALLERS,
))
register(Tool(
    name="get_task",
    description="Read one research card in detail, with its latest run and the start of its report.",
    input_model=TaskInput, handler=get_task, callers=ALL_CALLERS, id_fields=("task_id",),
))
