# services/supervisor_service.py
import json

import anthropic
from flask import current_app

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo

from . import insights_service, research_task_service
from .phases import PHASE_DEFINITIONS
from .project_service import load_project_prompt
from .prompt_drafting_service import create_drafted_card
from .prompt_frameworks import FRAMEWORK_LABELS, frameworks_for_phase, resolve_framework
from .review_limits import (
    MAX_CONTEXT_CHARS,
    MAX_REVIEW_OUTPUT_TOTAL_CHARS,
    MAX_RUN_OUTPUT_PREVIEW_CHARS,
    MAX_RUN_OUTPUT_REVIEW_CHARS,
)
from .text_utils import as_text_list, clip_text

# Each task line also carries what the card is about, kept short.
MAX_FOCUS_CHARS = 150
MAX_RATIONALE_CHARS = 200
MAX_GAPS_CHARS = 300

_clip_run_output = clip_text  # existing call sites keep their name


def _awaiting_review(item, run):
    return (
        run is not None
        and run["output_text"]
        and item["status"] == "RUNNING"
        and run["status"] != "running"
    )


def _one_line(text, limit):
    """Flatten text onto one line (quotes made single) and clip it, for a task line."""
    flattened = " ".join(str(text or "").split()).replace('"', "'")
    return _clip_run_output(flattened, limit)


def _json_list(value):
    try:
        parsed = json.loads(value) if value else []
    except ValueError:
        return []
    return as_text_list(parsed)


def _card_details(item):
    """Method, focus, why and review gaps, so drafting and review see what each card is about."""
    parts = [f"method={item['research_method'] or 'none'}"]
    focus = _json_list(item["entities_json"])
    if focus:
        parts.append(f"focus=\"{_one_line(', '.join(focus), MAX_FOCUS_CHARS)}\"")
    if item["rationale"]:
        parts.append(f"rationale=\"{_one_line(item['rationale'], MAX_RATIONALE_CHARS)}\"")
    gaps = _json_list(item["identified_gaps_json"])
    if gaps:
        parts.append(f"gaps=\"{_one_line('; '.join(gaps), MAX_GAPS_CHARS)}\"")
    return " | " + " ".join(parts)


def _format_work_item(item, dependencies, run=None, review_output_limit=None):
    dep_ids = [d["depends_on_work_item_id"] for d in dependencies]
    dep_text = f" | depends on: {dep_ids}" if dep_ids else ""
    line = (
        f"- id={item['id']} phase={item['phase_key']} title=\"{item['title']}\" "
        f"status={item['status']} priority={item['priority']} "
        f"completeness={item['completeness_score']} evidence={item['evidence_score']} "
        f"retry={item['retry_count']}/{item['max_retries']} "
        f"human_review_required={bool(item['human_review_required'])}{dep_text}"
        f"{_card_details(item)}"
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
    return f'{line} | run_output="{_one_line(output, MAX_RUN_OUTPUT_PREVIEW_CHARS)}"'


def build_context(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    blocks = []

    objective = load_project_prompt(project_name, "project_prompt")
    blocks.append(f"# PROJECT OBJECTIVE\n\n{objective or '(none set)'}")

    blocks.append(
        "# PHASE DEFINITIONS\n\n"
        + "\n".join(
            f"- {key}: {defn['title']} (frameworks: "
            + ", ".join(f"{fk} = {FRAMEWORK_LABELS[fk]}" for fk in frameworks_for_phase(key))
            + ")"
            for key, defn in PHASE_DEFINITIONS.items()
        )
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


DRAFTABLE_PHASES = ("1", "2", "3", "4", "5", "6")
DRAFTABLE_METHODS = ("TARGETED_WEB", "FILE_ANALYSIS")
REVIEW_OUTCOMES = ("COMPLETE", "FOLLOW_UP_REQUIRED", "NEEDS_HUMAN", "FAILED")
MAX_REVIEW_PROMPT_CHARS = 6000

DRAFTING_SYSTEM_PROMPT = (
    "You are the research supervisor for a competitive-intelligence project in Australian "
    "higher education. Research is organised in 7 fixed phases. Your job here is ONLY to draft "
    "researches for the person to review: you cannot approve or start anything, and nothing "
    "runs until they approve it. Read the objective, the existing researches (including skipped "
    "ones, which the person rejected -- do not propose them again) and any review gaps, then "
    "either propose the most useful next researches for Phases 1-6 or, if nothing useful can be "
    "added, call no_action. Prefer phases with no research yet. Choose TARGETED_WEB for public "
    "web information and FILE_ANALYSIS when the answer is likely in the project's uploaded files. "
    "Pick the framework that fits each research from the phase's list. Phase 7 is never drafted here."
)

PROPOSE_TASKS_TOOL = {
    "name": "propose_tasks",
    "description": "Draft one or more researches for the person to review. Each becomes a card that needs their approval before it can run.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "phase_key": {"type": "string", "enum": list(DRAFTABLE_PHASES)},
                        "title": {"type": "string"},
                        "focus": {"type": "array", "items": {"type": "string"}},
                        "research_method": {"type": "string", "enum": list(DRAFTABLE_METHODS)},
                        "framework_key": {"type": "string", "description": "One of the frameworks listed for this phase in PHASE DEFINITIONS."},
                        "rationale": {"type": "string", "description": "One or two plain sentences on why this research is needed now."},
                        "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                        "depends_on_existing_ids": {"type": "array", "items": {"type": "integer"}},
                        "depends_on_batch_indices": {"type": "array", "items": {"type": "integer"}, "description": "Zero-based indices into this same tasks array."},
                    },
                    "required": ["phase_key", "title", "research_method", "rationale"],
                },
            },
            "reason": {"type": "string"},
        },
        "required": ["tasks", "reason"],
    },
}

NO_ACTION_TOOL = {
    "name": "no_action",
    "description": "Nothing useful can be drafted right now.",
    "input_schema": {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
    },
}

DRAFTING_TOOLS = [PROPOSE_TASKS_TOOL, NO_ACTION_TOOL]

REVIEW_SYSTEM_PROMPT = (
    "You are the research supervisor reviewing ONE finished research for an Australian "
    "higher-education competitive-intelligence project. Judge only what you are shown. Score "
    "completeness (did it answer the prompt?) and evidence (are claims sourced?) from 0 to 1, "
    "list concrete gaps, and choose an outcome: COMPLETE if it is good enough, FOLLOW_UP_REQUIRED "
    "if specific gaps need another research (describe that research in followup), NEEDS_HUMAN if "
    "a person must judge it, FAILED if it produced nothing usable. Never claim the report contains "
    "something you were not shown."
)

REVIEW_TOOL = {
    "name": "review_outcome",
    "description": "Record your review of this finished research.",
    "input_schema": {
        "type": "object",
        "properties": {
            "completeness_score": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_score": {"type": "number", "minimum": 0, "maximum": 1},
            "identified_gaps": {"type": "array", "items": {"type": "string"}},
            "outcome": {"type": "string", "enum": list(REVIEW_OUTCOMES)},
            "reason": {"type": "string"},
            "followup": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "focus": {"type": "array", "items": {"type": "string"}},
                    "research_method": {"type": "string", "enum": list(DRAFTABLE_METHODS)},
                    "rationale": {"type": "string"},
                },
                "required": ["title"],
            },
        },
        "required": ["completeness_score", "evidence_score", "outcome", "reason"],
    },
}


def _client():
    return anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])


def _tool_use(response, name=None):
    return next(
        (b for b in response.content if b.type == "tool_use" and (name is None or b.name == name)), None
    )


def _batch_has_cycle(tasks_input):
    edges = {i: list(t.get("depends_on_batch_indices") or []) for i, t in enumerate(tasks_input)}
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


def _validate_proposals(project_id, tasks_input):
    if not tasks_input:
        raise ValueError("No researches were proposed")
    for t in tasks_input:
        if t.get("phase_key") == "7":
            raise ValueError("Phase 7 (the options report) is drafted from its own button, not proposed")
        if t.get("phase_key") not in DRAFTABLE_PHASES:
            raise ValueError(f"Unknown phase: {t.get('phase_key')!r}")
        if not (t.get("title") or "").strip():
            raise ValueError("Every research needs a title")
        if t.get("research_method") not in DRAFTABLE_METHODS:
            raise ValueError(f"Unknown research method: {t.get('research_method')!r}")
        for dep_id in t.get("depends_on_existing_ids") or []:
            existing = research_work_items_repo.get(dep_id) if isinstance(dep_id, int) else None
            if existing is None or existing["project_id"] != project_id:
                raise ValueError(f"depends_on_existing_ids value {dep_id!r} is not a research in this project")
        for dep_index in t.get("depends_on_batch_indices") or []:
            if not isinstance(dep_index, int) or dep_index < 0 or dep_index >= len(tasks_input):
                raise ValueError(
                    f"depends_on_batch_indices value {dep_index!r} is out of range for a batch of {len(tasks_input)}"
                )
    if _batch_has_cycle(tasks_input):
        raise ValueError("These researches depend on each other in a loop")


def handle_propose_tasks(project_name, tool_input):
    tasks_input = tool_input.get("tasks") or []
    _validate_proposals(projects_repo.get_or_create_id(project_name), tasks_input)  # everything is checked before anything is created
    new_ids = []
    drafted = 0
    for t in tasks_input:
        created = create_drafted_card(
            project_name, t["phase_key"], t["title"].strip(), research_method=t["research_method"],
            focus=as_text_list(t.get("focus")) or None, rationale=t.get("rationale"),
            framework_key=resolve_framework(t["phase_key"], t.get("framework_key")),
            priority=t.get("priority") if t.get("priority") in ("low", "medium", "high") else None,
        )
        new_ids.append(created["card_id"])
        drafted += 1 if created["drafted"] else 0
    for i, t in enumerate(tasks_input):
        for dep_id in t.get("depends_on_existing_ids") or []:
            research_task_service.add_dependency(new_ids[i], dep_id)
        for dep_index in t.get("depends_on_batch_indices") or []:
            research_task_service.add_dependency(new_ids[i], new_ids[dep_index])
    return {"created_task_ids": new_ids, "drafted_from_framework": drafted}


def handle_no_action(project_name, tool_input):
    return {"message": "No action taken."}


DRAFTING_HANDLERS = {"propose_tasks": handle_propose_tasks, "no_action": handle_no_action}


def draft_researches(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    response = _client().messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=4000,
        system=DRAFTING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_context(project_name)}],
        tools=DRAFTING_TOOLS,
        tool_choice={"type": "any"},
    )
    block = _tool_use(response)
    if block is None:
        raise RuntimeError("The Supervisor did not return a decision.")

    handler = DRAFTING_HANDLERS.get(block.name)
    try:
        if handler is None:
            raise ValueError(f"The Supervisor chose a tool it is not allowed to use: {block.name}")
        execution = {"success": True, "result": handler(project_name, block.input)}
    except ValueError as exc:
        execution = {"success": False, "error": str(exc)}

    agent_decisions_repo.record(
        project_id, decision_type=block.name, detail=json.dumps({"input": block.input, "execution": execution}),
    )
    return {"action": block.name, "input": block.input, "execution": execution}


def create_followup_card(project_name, original, followup):
    method = followup.get("research_method")
    if method not in DRAFTABLE_METHODS:
        method = original["research_method"] if original["research_method"] in DRAFTABLE_METHODS else "TARGETED_WEB"
    created = create_drafted_card(
        project_name, original["phase_key"], (followup.get("title") or f"Follow-up: {original['title']}").strip(),
        research_method=method, focus=as_text_list(followup.get("focus")) or None,
        rationale=followup.get("rationale") or f"Fills gaps found in \"{original['title']}\".",
        framework_key=original["framework_key"], suggested_from_work_item_id=original["id"],
    )
    return created["card_id"]


def _review_context(project_name, card, run):
    objective = load_project_prompt(project_name, "project_prompt") or "(none set)"
    phase_title = PHASE_DEFINITIONS.get(card["phase_key"], {}).get("title", card["phase_key"])
    prompt = _clip_run_output(run["prompt_text"] or card["prompt_text"] or "", MAX_REVIEW_PROMPT_CHARS)
    item_line = _format_work_item(
        card, research_work_items_repo.list_dependencies(card["id"]), run, MAX_RUN_OUTPUT_REVIEW_CHARS
    )
    return (
        f"# PROJECT OBJECTIVE\n\n{objective}\n\n"
        f"# RESEARCH BEING REVIEWED\n\nPhase {card['phase_key']}: {phase_title}\n{item_line}\n\n"
        f"# PROMPT THAT WAS SENT\n\n{prompt}"
    )


def _apply_review(project_name, card, tool_input):
    outcome = tool_input.get("outcome")
    if outcome not in REVIEW_OUTCOMES:
        raise ValueError(f"Unknown review outcome: {outcome!r}")
    research_task_service.set_review_scores(
        card["id"],
        completeness_score=tool_input.get("completeness_score"),
        evidence_score=tool_input.get("evidence_score"),
        identified_gaps=(
            as_text_list(tool_input["identified_gaps"]) if tool_input.get("identified_gaps") is not None else None
        ),
    )
    followup_id = None
    if outcome == "FOLLOW_UP_REQUIRED" and tool_input.get("followup"):
        followup_id = create_followup_card(project_name, card, tool_input["followup"])
    if outcome == "NEEDS_HUMAN" or (outcome == "COMPLETE" and card["human_review_required"]):
        research_task_service.flag_for_human_review(card["id"])
        new_status = "WAITING_FOR_HUMAN"
    else:
        new_status = outcome
    research_task_service.transition_task(card["id"], new_status)
    return {"outcome": outcome, "new_status": new_status, "followup_card_id": followup_id}


def review_card(project_name, card_id):
    project_id = projects_repo.get_or_create_id(project_name)
    card = research_work_items_repo.get(card_id)
    if card is None or card["project_id"] != project_id:
        raise ValueError(f"No such research: {card_id}")
    run = research_runs_repo.find_latest_for_work_item(card_id)
    if card["status"] != "RUNNING" or run is None or run["status"] != "completed" or not run["output_text"]:
        return {"reviewed": False, "reason": "This research has no finished report to review."}
    if not research_task_service.claim_transition(card_id, "RUNNING", "REVIEWING"):
        return {"reviewed": False, "reason": "This research is already being reviewed."}

    try:
        response = _client().messages.create(
            model=current_app.config["ANTHROPIC_MODEL"],
            max_tokens=2000,
            system=REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _review_context(project_name, card, run)}],
            tools=[REVIEW_TOOL],
            tool_choice={"type": "tool", "name": "review_outcome"},
        )
        block = _tool_use(response, "review_outcome")
        if block is None:
            raise RuntimeError("The Supervisor did not return a review.")
        result = _apply_review(project_name, research_work_items_repo.get(card_id), block.input)
    except Exception as exc:
        research_task_service.claim_transition(card_id, "REVIEWING", "RUNNING")
        agent_decisions_repo.record(
            project_id, decision_type="review_error", detail=json.dumps({"error": str(exc)}),
            research_work_item_id=card_id,
        )
        return {"reviewed": False, "error": str(exc)}

    agent_decisions_repo.record(
        project_id, decision_type="review_outcome",
        detail=json.dumps({"input": block.input, "execution": {"success": True, "result": result}}),
        research_work_item_id=card_id,
    )
    return {"reviewed": True, **result}
