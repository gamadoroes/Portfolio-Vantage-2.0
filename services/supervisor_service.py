# services/supervisor_service.py
import json
from datetime import datetime

import anthropic
from flask import current_app

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo

from . import insights_service, llm_service, openai_service, research_run_service, research_task_service
from .file_index_service import HIDDEN_SOURCE_FILES, reconcile_file_index, reconcile_selected_file_ids
from .file_service import load_project_files
from .phases import PHASE_DEFINITIONS
from .project_service import load_project_prompt

MAX_CONTEXT_CHARS = 60000
MAX_FILE_ANALYSIS_TOTAL_CHARS = 60000
MAX_FILE_ANALYSIS_PER_FILE_CHARS = 15000
# A task awaiting review needs enough of its run output for the supervisor to
# score completeness/evidence; every other task only needs a short reminder.
MAX_RUN_OUTPUT_PREVIEW_CHARS = 300
MAX_RUN_OUTPUT_REVIEW_CHARS = 8000
MAX_REVIEW_OUTPUT_TOTAL_CHARS = 24000


def _clip_run_output(text, limit):
    text = text if isinstance(text, str) else str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _awaiting_review(item, run):
    return (
        run is not None
        and run["output_text"]
        and item["status"] == "RUNNING"
        and run["status"] != "running"
    )


def _format_work_item(item, dependencies, run=None, review_output_limit=None):
    dep_ids = [d["depends_on_work_item_id"] for d in dependencies]
    dep_text = f" | depends on: {dep_ids}" if dep_ids else ""
    line = (
        f"- id={item['id']} phase={item['phase_key']} title=\"{item['title']}\" "
        f"status={item['status']} priority={item['priority']} "
        f"completeness={item['completeness_score']} evidence={item['evidence_score']} "
        f"retry={item['retry_count']}/{item['max_retries']} "
        f"human_review_required={bool(item['human_review_required'])}{dep_text}"
    )
    if run is None:
        return line

    line += f" | run_status={run['status']}"
    output = run["output_text"]
    if not output:
        return line
    if review_output_limit is not None:
        # Delimited block: the output is markdown and would otherwise break the
        # one-task-per-line structure of the RESEARCH TASKS section.
        return (
            f"{line}\n<run_output task_id={item['id']}>\n"
            f"{_clip_run_output(output, review_output_limit)}\n</run_output>"
        )
    flattened = " ".join(output.split()).replace('"', "'")
    return f'{line} | run_output="{_clip_run_output(flattened, MAX_RUN_OUTPUT_PREVIEW_CHARS)}"'


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
    item_lines = []
    review_budget = MAX_REVIEW_OUTPUT_TOTAL_CHARS
    for item in items:
        run = research_runs_repo.find_latest_for_work_item(item["id"])
        review_output_limit = None
        if _awaiting_review(item, run) and review_budget >= MAX_RUN_OUTPUT_REVIEW_CHARS:
            review_output_limit = MAX_RUN_OUTPUT_REVIEW_CHARS
            review_budget -= min(len(run["output_text"]), review_output_limit)
        item_lines.append(
            _format_work_item(
                item, research_work_items_repo.list_dependencies(item["id"]), run, review_output_limit
            )
        )
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
        "description": "Create a new task that fills a gap in a task needing follow-up research, and make the original task depend on it. Call this for a task that review_outcome has already marked FOLLOW_UP_REQUIRED (a task still in REVIEWING is marked for you). The original task will become eligible for READY again once the new follow-up task completes or is skipped.",
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

    # review_outcome(FOLLOW_UP_REQUIRED) has normally put the task here already; only a
    # task still in REVIEWING needs the transition (anything else is refused by it).
    if original["status"] != "FOLLOW_UP_REQUIRED":
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


def _selected_project_files(project_name):
    all_files = {
        k: v for k, v in load_project_files(project_name).items()
        if k not in HIDDEN_SOURCE_FILES
    }
    index_entries = reconcile_file_index(project_name)
    selection_state = reconcile_selected_file_ids(project_name, index_entries)
    selected_files = selection_state.get("selected_files", [])
    if selected_files:
        return {k: v for k, v in all_files.items() if k in set(selected_files)}
    return all_files


def _build_file_analysis_prompt(task_row, files):
    objective = task_row["objective"] or task_row["title"]
    entities = json.loads(task_row["entities_json"]) if task_row["entities_json"] else []
    expected_output = task_row["expected_output"] or "a clear, evidence-based answer"
    entities_line = f"\nEntities of interest: {', '.join(entities)}" if entities else ""

    system_prompt = (
        "You are a research analyst for an Australian higher-education competitive "
        "analysis project. Use ONLY the provided source files. If the objective "
        "cannot be answered from them, say MISSING -- do not guess or use general "
        "knowledge.\n\n"
        f"OBJECTIVE: {objective}{entities_line}\n"
        f"EXPECTED OUTPUT: {expected_output}"
    )

    blocks = []
    used = 0
    for filename, content in files.items():
        text = content if isinstance(content, str) else str(content or "")
        if len(text) > MAX_FILE_ANALYSIS_PER_FILE_CHARS:
            head = MAX_FILE_ANALYSIS_PER_FILE_CHARS // 2
            text = text[:head] + "\n\n[...truncated...]\n\n" + text[-(MAX_FILE_ANALYSIS_PER_FILE_CHARS - head):]
        block = f"## {filename}\n\n{text}\n\n---\n\n"
        if used + len(block) > MAX_FILE_ANALYSIS_TOTAL_CHARS:
            break
        blocks.append(block)
        used += len(block)

    user_message = "# SOURCE DATA\n\n" + ("".join(blocks) if blocks else "(no source files available)")
    return system_prompt, user_message


def handle_dispatch_task(project_name, tool_input):
    task_id = tool_input["task_id"]
    method = tool_input["research_method"]
    task_row = research_work_items_repo.get(task_id)
    if task_row is None:
        raise ValueError(f"No such research task: {task_id}")

    research_task_service.transition_task(task_id, "RUNNING")
    prompt_preview = task_row["objective"] or task_row["title"]
    run_id = research_run_service.create_run(project_name, None, None, prompt_preview)
    research_run_service.update_run(project_name, run_id, research_work_item_id=task_id)

    try:
        if method == "FILE_ANALYSIS":
            files = _selected_project_files(project_name)
            system_prompt, user_message = _build_file_analysis_prompt(task_row, files)
            output_text = llm_service.prompt_completion(system_prompt, user_message, max_tokens=3000)
            research_run_service.update_run(
                project_name, run_id, status="completed", output_text=output_text,
                completed_at=datetime.now().isoformat(),
            )
            return {"task_id": task_id, "run_id": run_id, "research_method": method, "status": "completed"}

        # TARGETED_WEB
        response = openai_service.start_deep_research(prompt_preview)
        research_run_service.update_run(project_name, run_id, response_id=response.id)
        return {"task_id": task_id, "run_id": run_id, "research_method": method, "status": "running"}
    except Exception as exc:
        # The task is already RUNNING and the run row exists. Left alone they would be
        # stranded (nothing to poll, review_outcome refuses a "running" run), and a
        # non-ValueError would also bypass the decision log in run_supervisor_cycle.
        research_run_service.fail_run(project_name, run_id, str(exc))
        research_task_service.transition_task(task_id, "FAILED")
        raise ValueError(f"Could not run {method} for task {task_id}: {exc}") from exc


TOOL_HANDLERS["dispatch_task"] = handle_dispatch_task


def handle_review_outcome(project_name, tool_input):
    task_id = tool_input["task_id"]
    outcome = tool_input["outcome"]
    run = research_runs_repo.find_latest_for_work_item(task_id)
    if run is None or run["status"] == "running":
        raise ValueError(f"No finished run to review for task {task_id}")

    research_task_service.set_review_scores(
        task_id,
        completeness_score=tool_input.get("completeness_score"),
        evidence_score=tool_input.get("evidence_score"),
        identified_gaps=tool_input.get("identified_gaps"),
    )

    if outcome == "FAILED":
        # REVIEWING has no FAILED edge in Phase 2's transition table -- must
        # go directly from RUNNING, or this would raise ValueError.
        research_task_service.transition_task(task_id, "FAILED")
    else:
        research_task_service.transition_task(task_id, "REVIEWING")
        research_task_service.transition_task(task_id, outcome)

    return {"task_id": task_id, "outcome": outcome, "run_id": run["id"]}


TOOL_HANDLERS["review_outcome"] = handle_review_outcome


SYNTHESIS_SYSTEM_PROMPT = (
    "You are a senior strategy consultant synthesising a competitive "
    "landscape analysis for Australian higher education. Using ONLY the "
    "phase summaries provided, identify the key strategic options available. "
    "Use the SO WHAT / NOW WHAT framework. Write in Australian English."
)


def handle_trigger_synthesis(project_name, tool_input):
    project_id = projects_repo.get_or_create_id(project_name)
    missing_phases = []
    for phase_key in PHASE_DEFINITIONS:
        if phase_key == "7":
            continue
        items = research_work_items_repo.list_for_phase(project_id, phase_key)
        if not any(item["status"] == "COMPLETE" for item in items):
            missing_phases.append(phase_key)
    if missing_phases:
        raise ValueError(f"Phases missing completed research: {', '.join(missing_phases)}")

    current = insights_service.load_current_insights(project_name)
    phase_summaries = []
    for phase_key in PHASE_DEFINITIONS:
        if phase_key == "7":
            continue
        phase = current["phases"].get(phase_key, {})
        summary = phase.get("summary", "MISSING")
        if summary and summary != "MISSING":
            title = PHASE_DEFINITIONS[phase_key]["title"]
            phase_summaries.append(f"## {title}\n\n{summary}")

    user_message = "\n\n".join(phase_summaries) if phase_summaries else "(no phase summaries available)"
    synthesis_text = llm_service.prompt_completion(SYNTHESIS_SYSTEM_PROMPT, user_message, max_tokens=4000)

    updated_phases = dict(current["phases"])
    phase_7 = dict(updated_phases.get("7", {}))
    phase_7["summary"] = synthesis_text
    phase_7["confidence"] = "medium"
    updated_phases["7"] = phase_7

    insights_service.save_insights(project_name, {
        "generated_at": datetime.now().isoformat(),
        "competitors": current.get("competitors", []),
        "competitor_landscape_markdown": current.get("competitor_landscape_markdown", ""),
        "phases": updated_phases,
    })

    return {"synthesis_generated": True}


TOOL_HANDLERS["trigger_synthesis"] = handle_trigger_synthesis


SUPERVISOR_SYSTEM_PROMPT = (
    "You are the research supervisor for a competitive-intelligence project "
    "in Australian higher education. You coordinate research across 7 fixed "
    "phases by managing a set of research tasks, each with its own status "
    "lifecycle. On every call, you must choose exactly ONE of the provided "
    "tools based on the current project state below. Prefer making forward "
    "progress: propose tasks for phases with no work yet, mark proposed "
    "tasks ready once their dependencies are satisfied, dispatch tasks that "
    "are READY, review tasks whose research has finished, and request human "
    "review or create follow-up tasks when evidence is weak. Only trigger "
    "final synthesis once every phase has real completed research."
)


def run_supervisor_cycle(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    context_text = build_context(project_name)

    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=2000,
        system=SUPERVISOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": context_text}],
        tools=TOOL_SCHEMAS,
        tool_choice={"type": "any"},
    )

    tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use_block is None:
        raise RuntimeError("Supervisor model did not return a tool call.")

    tool_name = tool_use_block.name
    tool_input = tool_use_block.input
    handler = TOOL_HANDLERS[tool_name]

    try:
        result = handler(project_name, tool_input)
        execution_result = {"success": True, "result": result}
    except ValueError as exc:
        execution_result = {"success": False, "error": str(exc)}

    agent_decisions_repo.record(
        project_id,
        decision_type=tool_name,
        detail=json.dumps({"input": tool_input, "execution": execution_result}),
        research_work_item_id=tool_input.get("task_id"),
    )

    return {
        "decision": {"action": tool_name, "input": tool_input},
        "execution": execution_result,
    }
