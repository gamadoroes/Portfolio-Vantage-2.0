# services/supervisor_service.py
from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo

from . import insights_service, research_task_service
from .phases import PHASE_DEFINITIONS
from .project_service import load_project_prompt

MAX_CONTEXT_CHARS = 60000


def _format_work_item(item, dependencies):
    dep_ids = [d["depends_on_work_item_id"] for d in dependencies]
    dep_text = f" | depends on: {dep_ids}" if dep_ids else ""
    return (
        f"- id={item['id']} phase={item['phase_key']} title=\"{item['title']}\" "
        f"status={item['status']} priority={item['priority']} "
        f"completeness={item['completeness_score']} evidence={item['evidence_score']} "
        f"retry={item['retry_count']}/{item['max_retries']} "
        f"human_review_required={bool(item['human_review_required'])}{dep_text}"
    )


def build_context(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    blocks = []

    objective = load_project_prompt(project_name, "project_prompt")
    blocks.append(f"# PROJECT OBJECTIVE\n\n{objective or '(none set)'}")

    blocks.append(
        "# PHASE DEFINITIONS\n\n"
        + "\n".join(f"- {key}: {defn['title']}" for key, defn in PHASE_DEFINITIONS.items())
    )

    current = insights_service.load_current_insights(project_name)
    phase_lines = []
    for key, phase in current["phases"].items():
        summary = phase.get("summary", "MISSING")
        if summary and summary != "MISSING":
            phase_lines.append(f"- Phase {key} ({PHASE_DEFINITIONS[key]['title']}): {summary}")
    blocks.append(
        "# EXISTING PHASE FINDINGS (read-only; you never modify these directly)\n\n"
        + ("\n".join(phase_lines) if phase_lines else "(no phase findings established yet)")
    )

    items = research_work_items_repo.list_for_project(project_id)
    item_lines = [_format_work_item(item, research_work_items_repo.list_dependencies(item["id"])) for item in items]
    blocks.append(
        "# RESEARCH TASKS\n\n" + ("\n".join(item_lines) if item_lines else "(no research tasks yet)")
    )

    decisions = agent_decisions_repo.list_for_project(project_id, limit=10)
    decision_lines = [f"- {d['decision_type']}: {d['detail']}" for d in decisions]
    blocks.append(
        "# RECENT SUPERVISOR DECISIONS (most recent first)\n\n"
        + ("\n".join(decision_lines) if decision_lines else "(no prior decisions)")
    )

    text = "\n\n".join(blocks)
    if len(text) > MAX_CONTEXT_CHARS:
        # Drop the oldest decisions first (least useful context), then hard-clip.
        decisions = agent_decisions_repo.list_for_project(project_id, limit=3)
        decision_lines = [f"- {d['decision_type']}: {d['detail']}" for d in decisions]
        blocks[-1] = (
            "# RECENT SUPERVISOR DECISIONS (most recent first, truncated)\n\n"
            + ("\n".join(decision_lines) if decision_lines else "(no prior decisions)")
        )
        text = "\n\n".join(blocks)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n\n[...context truncated for length...]"

    return text


TOOL_SCHEMAS = [
    {
        "name": "propose_tasks",
        "description": "Create one or more new research tasks. Use this for the initial research plan, or to fill a gap identified later. Supports dependencies on existing tasks (by their real id) and on other tasks proposed in this same call (by their zero-based index in the tasks array).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "phase_key": {"type": "string", "description": "One of '1' through '7', matching the fixed phase definitions."},
                            "title": {"type": "string"},
                            "objective": {"type": "string"},
                            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                            "entities": {"type": "array", "items": {"type": "string"}},
                            "expected_output": {"type": "string"},
                            "depends_on_existing_ids": {"type": "array", "items": {"type": "integer"}},
                            "depends_on_batch_indices": {"type": "array", "items": {"type": "integer"}, "description": "Zero-based indices into this same tasks array."},
                        },
                        "required": ["phase_key", "title"],
                    },
                },
                "reason": {"type": "string", "description": "Why these tasks are needed now."},
            },
            "required": ["tasks", "reason"],
        },
    },
    {
        "name": "mark_ready",
        "description": "Transition a PROPOSED or FOLLOW_UP_REQUIRED task to READY, making it eligible for dispatch. Only legal if all of its dependencies are COMPLETE or SKIPPED -- the backend re-checks this regardless of what you believe.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
    },
    {
        "name": "dispatch_task",
        "description": "Transition a READY task to RUNNING and start the chosen research method. FILE_ANALYSIS runs synchronously against the project's uploaded source files and completes before this call returns. TARGETED_WEB starts an async web-research job -- its outcome is reviewed on a LATER supervisor call via review_outcome, not this one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "research_method": {"type": "string", "enum": ["FILE_ANALYSIS", "TARGETED_WEB"]},
                "reason": {"type": "string", "description": "Why this method was chosen over the other."},
            },
            "required": ["task_id", "research_method", "reason"],
        },
    },
    {
        "name": "review_outcome",
        "description": "Review a RUNNING task whose research has finished (its linked run has status completed or failed) and record the outcome. Do not call this for a task whose run is still in progress.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "completeness_score": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence_score": {"type": "number", "minimum": 0, "maximum": 1},
                "identified_gaps": {"type": "array", "items": {"type": "string"}},
                "outcome": {"type": "string", "enum": ["COMPLETE", "FOLLOW_UP_REQUIRED", "FAILED"]},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "outcome", "reason"],
        },
    },
    {
        "name": "create_followup_task",
        "description": "Mark a task as needing follow-up research and create a new dependent task to fill the gap. The original task will become eligible for READY again once the new follow-up task completes or is skipped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "The task that needs follow-up."},
                "followup_title": {"type": "string"},
                "followup_objective": {"type": "string"},
                "research_method": {"type": "string", "enum": ["FILE_ANALYSIS", "TARGETED_WEB"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "followup_title", "reason"],
        },
    },
    {
        "name": "request_human_review",
        "description": "Flag a task as requiring human judgment before it can be marked complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
    },
    {
        "name": "skip_task",
        "description": "Abandon a task permanently. Use when it's no longer useful to pursue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
    },
    {
        "name": "trigger_synthesis",
        "description": "Request final Phase 7 (Options for OES) synthesis across all prior phases. Only call this when you believe every phase has sufficient completed research. The backend will verify this independently and reject the request if prerequisites are not actually met.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "no_action",
        "description": "Nothing useful can be done right now (e.g. all eligible tasks are already running, or everything is blocked on an async result). Use this instead of forcing an action that doesn't make sense.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
]


def handle_mark_ready(project_name, tool_input):
    task_id = tool_input["task_id"]
    research_task_service.transition_task(task_id, "READY")
    return {"task_id": task_id, "new_status": "READY"}


def handle_skip_task(project_name, tool_input):
    task_id = tool_input["task_id"]
    research_task_service.transition_task(task_id, "SKIPPED")
    return {"task_id": task_id, "new_status": "SKIPPED"}


def handle_no_action(project_name, tool_input):
    return {"message": "No action taken."}


def handle_request_human_review(project_name, tool_input):
    task_id = tool_input["task_id"]
    item = research_work_items_repo.get(task_id)
    if item is None:
        raise ValueError(f"No such research task: {task_id}")
    research_task_service.flag_for_human_review(task_id)
    if item["status"] == "REVIEWING":
        research_task_service.transition_task(task_id, "WAITING_FOR_HUMAN")
        return {"task_id": task_id, "new_status": "WAITING_FOR_HUMAN"}
    return {"task_id": task_id, "new_status": item["status"], "flagged_only": True}


TOOL_HANDLERS = {
    "mark_ready": handle_mark_ready,
    "skip_task": handle_skip_task,
    "no_action": handle_no_action,
    "request_human_review": handle_request_human_review,
}


def handle_propose_tasks(project_name, tool_input):
    project_id = projects_repo.get_or_create_id(project_name)
    tasks_input = tool_input["tasks"]
    new_ids = []
    for t in tasks_input:
        task_id = research_task_service.create_task(
            project_id,
            t["phase_key"],
            t["title"],
            objective=t.get("objective"),
            priority=t.get("priority"),
            entities=t.get("entities"),
            expected_output=t.get("expected_output"),
        )
        new_ids.append(task_id)

    for i, t in enumerate(tasks_input):
        task_id = new_ids[i]
        for dep_id in t.get("depends_on_existing_ids") or []:
            research_task_service.add_dependency(task_id, dep_id)
        for dep_index in t.get("depends_on_batch_indices") or []:
            if not isinstance(dep_index, int) or dep_index < 0 or dep_index >= len(tasks_input):
                raise ValueError(
                    f"depends_on_batch_indices value {dep_index!r} is out of range "
                    f"for a batch of {len(tasks_input)} tasks"
                )
            research_task_service.add_dependency(task_id, new_ids[dep_index])

    return {"created_task_ids": new_ids}


def handle_create_followup_task(project_name, tool_input):
    project_id = projects_repo.get_or_create_id(project_name)
    original_id = tool_input["task_id"]
    original = research_work_items_repo.get(original_id)
    if original is None:
        raise ValueError(f"No such research task: {original_id}")

    research_task_service.transition_task(original_id, "FOLLOW_UP_REQUIRED")
    new_id = research_task_service.create_task(
        project_id,
        original["phase_key"],
        tool_input["followup_title"],
        objective=tool_input.get("followup_objective"),
        priority=tool_input.get("priority"),
        research_method=tool_input.get("research_method"),
    )
    research_task_service.add_dependency(original_id, new_id)

    return {"original_task_id": original_id, "followup_task_id": new_id}


TOOL_HANDLERS["propose_tasks"] = handle_propose_tasks
TOOL_HANDLERS["create_followup_task"] = handle_create_followup_task
