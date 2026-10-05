# services/supervisor_service.py
import json

import anthropic
from flask import current_app

from db.repositories import agent_decisions_repo, projects_repo, research_runs_repo, research_work_items_repo

from . import research_task_service
from .phases import PHASE_DEFINITIONS
from .project_service import load_project_prompt
from .review_limits import (
    MAX_CONTEXT_CHARS,
    MAX_REVIEW_OUTPUT_TOTAL_CHARS,
    MAX_RUN_OUTPUT_PREVIEW_CHARS,
    MAX_RUN_OUTPUT_REVIEW_CHARS,
)
from .text_utils import as_text_list, clip_text
from .tools import claude_tools, run_tool
from .tools import review as review_tool

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


def _format_work_item(item, dep_ids, run=None, review_output_limit=None):
    dep_text = f" | depends on: {list(dep_ids)}" if dep_ids else ""
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
    result = run_tool("get_project_state", "system", project_name, {})
    if not result.ok:
        raise RuntimeError(result.error["message"])
    return format_briefing(result.data)


def _decision_block(decisions, truncated):
    heading = "# RECENT SUPERVISOR DECISIONS (most recent first" + (", truncated" if truncated else "") + ")"
    lines = [f"- {d['decision_type']}: {d['detail']}" for d in decisions]
    return f"{heading}\n\n" + ("\n".join(lines) if lines else "(no prior decisions)")


def format_briefing(state):
    titles = {p["key"]: p["title"] for p in state["phases"]}
    blocks = [f"# PROJECT OBJECTIVE\n\n{state['objective'] or '(none set)'}"]
    blocks.append(
        "# PHASE DEFINITIONS\n\n"
        + "\n".join(
            f"- {p['key']}: {p['title']} (frameworks: "
            + ", ".join(f"{f['key']} = {f['label']}" for f in p["frameworks"])
            + ")"
            for p in state["phases"]
        )
    )
    phase_lines = [f"- Phase {key} ({titles[key]}): {summary}" for key, summary in state["phase_summaries"].items()]
    blocks.append(
        "# EXISTING PHASE FINDINGS (read-only; you never modify these directly)\n\n"
        + ("\n".join(phase_lines) if phase_lines else "(no phase findings established yet)")
    )
    item_lines = []
    review_budget = MAX_REVIEW_OUTPUT_TOTAL_CHARS
    for card in state["cards"]:
        run = card["latest_run"]
        review_output_limit = None
        if _awaiting_review(card, run) and review_budget >= MAX_RUN_OUTPUT_REVIEW_CHARS:
            review_output_limit = MAX_RUN_OUTPUT_REVIEW_CHARS
            review_budget -= min(len(run["output_text"]), review_output_limit)
        item_lines.append(_format_work_item(card, card["dependency_ids"], run, review_output_limit))
    blocks.append("# RESEARCH TASKS\n\n" + ("\n".join(item_lines) if item_lines else "(no research tasks yet)"))
    blocks.append(_decision_block(state["recent_decisions"], truncated=False))

    text = "\n\n".join(blocks)
    if len(text) > MAX_CONTEXT_CHARS:
        # Drop the oldest decisions first (least useful context), then hard-clip.
        blocks[-1] = _decision_block(state["recent_decisions"][:3], truncated=True)
        text = "\n\n".join(blocks)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n\n[...context truncated for length...]"
    return text


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

REVIEW_SYSTEM_PROMPT = (
    "You are the research supervisor reviewing ONE finished research for an Australian "
    "higher-education competitive-intelligence project. Judge only what you are shown. Score "
    "completeness (did it answer the prompt?) and evidence (are claims sourced?) from 0 to 1, "
    "list concrete gaps, and choose an outcome: COMPLETE if it is good enough, FOLLOW_UP_REQUIRED "
    "if specific gaps need another research (describe that research in followup), NEEDS_HUMAN if "
    "a person must judge it, FAILED if it produced nothing usable. Never claim the report contains "
    "something you were not shown."
)

def _client():
    return anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])


def _tool_use(response, name=None):
    return next(
        (b for b in response.content if b.type == "tool_use" and (name is None or b.name == name)), None
    )


def _review_context(project_name, task):
    card, run = task["card"], task["latest_run"]
    objective = load_project_prompt(project_name, "project_prompt") or "(none set)"
    phase_title = PHASE_DEFINITIONS.get(card["phase_key"], {}).get("title", card["phase_key"])
    prompt = _clip_run_output(run["prompt_text"] or card["prompt_text"] or "", MAX_REVIEW_PROMPT_CHARS)
    excerpt = run["report_excerpt"]
    # review_card has already claimed the card (RUNNING -> REVIEWING); the briefing shows it as it was before.
    card = {**card, "status": "RUNNING"}
    item_line = _format_work_item(
        card, card["dependency_ids"], {"status": run["status"], "output_text": excerpt},
        # The excerpt is already clipped to the review limit; this passes it through unchanged.
        review_output_limit=max(len(excerpt), 1),
    )
    return (
        f"# PROJECT OBJECTIVE\n\n{objective}\n\n"
        f"# RESEARCH BEING REVIEWED\n\nPhase {card['phase_key']}: {phase_title}\n{item_line}\n\n"
        f"# PROMPT THAT WAS SENT\n\n{prompt}"
    )


DRAFTING_MENU = ("create_research_task", "no_action")
REVIEW_MENU = ("evaluate_research_output",)


def draft_researches(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    response = _client().messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=4000,
        system=DRAFTING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_context(project_name)}],
        tools=claude_tools(DRAFTING_MENU),
        tool_choice={"type": "any"},
    )
    block = _tool_use(response)
    if block is None:
        raise RuntimeError("The Supervisor did not return a decision.")
    result = run_tool(block.name, "supervisor", project_name, block.input, menu=DRAFTING_MENU)
    execution = ({"success": True, "result": result.data} if result.ok
                 else {"success": False, "error": result.error["message"]})
    if not result.ok and result.error["code"] == "not_allowed":
        # The model asked for something it may not use. Record that plainly; nothing ran.
        agent_decisions_repo.record(
            project_id, decision_type="off_menu_tool",
            detail=json.dumps({"tool": str(block.name)[:100], "input": block.input, "execution": execution}),
        )
    else:
        agent_decisions_repo.record(
            project_id, decision_type=block.name, detail=json.dumps({"input": block.input, "execution": execution}),
        )
    return {"action": block.name, "input": block.input, "execution": execution}


def _clean_review_input(raw):
    """The model's review, trimmed to the tool's limits so a long answer cannot fail a paid review."""
    cleaned = dict(raw)
    cleaned["identified_gaps"] = [g[:review_tool.GAP_CHARS]
                                  for g in as_text_list(cleaned.get("identified_gaps"))][:review_tool.GAP_ITEMS]
    cleaned["reason"] = str(cleaned.get("reason") or "").strip()[:review_tool.REASON_CHARS]
    followup = cleaned.pop("followup", None)
    title = str(followup.get("title") or "").strip()[:review_tool.FOLLOWUP_TITLE_CHARS] if isinstance(followup, dict) else ""
    if title:  # without a usable title there is no follow-up; the review still goes ahead
        method = followup.get("research_method")
        cleaned["followup"] = {
            "title": title,
            "focus": [f[:review_tool.FOLLOWUP_FOCUS_CHARS]
                      for f in as_text_list(followup.get("focus"))][:review_tool.FOLLOWUP_FOCUS_ITEMS],
            "research_method": method if method in review_tool.FOLLOWUP_METHODS else None,
            "rationale": str(followup.get("rationale") or "").strip()[:review_tool.FOLLOWUP_RATIONALE_CHARS],
        }
    return cleaned


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
        task = run_tool("get_task", "system", project_name, {"task_id": card_id})
        if not task.ok:
            raise RuntimeError(task.error["message"])
        response = _client().messages.create(
            model=current_app.config["ANTHROPIC_MODEL"],
            max_tokens=2000,
            system=REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _review_context(project_name, task.data)}],
            tools=claude_tools(REVIEW_MENU),
            tool_choice={"type": "tool", "name": "evaluate_research_output"},
        )
        block = _tool_use(response, "evaluate_research_output")
        if block is None:
            raise RuntimeError("The Supervisor did not return a review.")
        inputs = _clean_review_input(block.input)
        inputs["task_id"] = card_id  # the system, not the model, decides which card is being reviewed
        result = run_tool("evaluate_research_output", "supervisor", project_name, inputs, menu=REVIEW_MENU)
        if not result.ok:
            raise RuntimeError(result.error["message"])
    except Exception as exc:
        research_task_service.claim_transition(card_id, "REVIEWING", "RUNNING")
        agent_decisions_repo.record(
            project_id, decision_type="review_error", detail=json.dumps({"error": str(exc)}),
            research_work_item_id=card_id,
        )
        return {"reviewed": False, "error": str(exc)}

    agent_decisions_repo.record(
        project_id, decision_type="review_outcome",
        detail=json.dumps({"input": block.input, "execution": {"success": True, "result": result.data}}),
        research_work_item_id=card_id,
    )
    return {"reviewed": True, **result.data}
