# services/prompt_drafting_service.py
"""Turns a research idea (phase, title, focus, why) into a full research prompt using that
phase's framework. This is the server-side replacement for the Prompt Developer's
"Generate Master Prompt"."""
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from flask import current_app, has_app_context

from db.repositories import projects_repo, research_work_items_repo

from . import llm_service, research_task_service
from .project_service import load_project_prompt
from .prompt_frameworks import get_framework, resolve_framework

# Starts every fallback prompt; Approve refuses a prompt that still contains it.
FALLBACK_MARKER = "[Couldn't draft this from the framework. Edit the prompt before approving.]"
# How many prompts are drafted (one slow Claude call each) at the same time when a batch of cards is created.
MAX_DRAFT_WORKERS = 4


def strip_prompt_budget_sections(prompt_text):
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        return prompt_text

    lines = prompt_text.splitlines()
    cleaned = []
    skipping_budget_section = False

    for line in lines:
        stripped = line.strip()
        heading_match = re.match(r"^#{1,6}\s*(.+?)\s*$", stripped)

        if heading_match:
            heading = heading_match.group(1).lower()
            if re.search(r"\b(token budget(?: allocation)?|budget(?:\s*&\s*timeline)?)\b", heading):
                skipping_budget_section = True
                continue
            if skipping_budget_section:
                skipping_budget_section = False

        if skipping_budget_section:
            continue

        if re.search(r"\btoken budget\b", stripped, re.IGNORECASE):
            continue
        if re.search(r"\b\d{1,3}(?:,\d{3})\s*tokens?\b", stripped, re.IGNORECASE):
            continue

        cleaned.append(line)

    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _drafting_message(framework, objective, title, focus, rationale):
    focus_text = ", ".join(focus) if focus else "(none given)"
    return (
        "Create a deep research prompt for the research below.\n\n"
        f"PROJECT OBJECTIVE: {objective or '(none set)'}\n"
        f"RESEARCH TITLE: {title}\n"
        f"FOCUS: {focus_text}\n"
        f"WHY THIS RESEARCH: {rationale or '(not given)'}\n\n"
        "Infer these framework inputs from the objective and the research above, and leave out "
        "any you cannot infer: " + "; ".join(framework["fields"]) + "\n\n"
        "OUTPUT RULES:\n"
        "- Do not include token budgets, budget allocation, token counts, runtime limits, or timeframe sections.\n"
        "- Do not include headings like 'Token Budget Allocation' or 'Budget & Timeline'.\n"
        "- Return only the final prompt text."
    )


def fallback_prompt(title, focus, objective):
    lines = [FALLBACK_MARKER, "", f"Research question: {title}"]
    if focus:
        lines.append(f"Focus: {', '.join(focus)}")
    if objective:
        lines.append(f"Project objective: {objective}")
    lines.append("Cite every claim and prefer official university and regulator sources.")
    return "\n".join(lines)


def draft_prompt(project_name, phase_key, framework_key, title, focus=None, rationale=None):
    resolved = resolve_framework(phase_key, framework_key)
    framework = get_framework(resolved)
    objective = load_project_prompt(project_name, "project_prompt") or ""
    try:
        text = llm_service.prompt_completion(
            framework["system_prompt"], _drafting_message(framework, objective, title, focus, rationale)
        )
        text = strip_prompt_budget_sections(text)
    except Exception as exc:  # drafting must never block creating the card
        print(f"[prompt-drafting] could not draft '{title}': {exc}")
        text = None
    if not text or not text.strip():
        return {"prompt_text": fallback_prompt(title, focus, objective), "framework_key": resolved, "drafted": False}
    return {"prompt_text": text.strip(), "framework_key": resolved, "drafted": True}


def draft_prompts(project_name, jobs):
    """Draft several prompts at the same time and return draft_prompt's result for each, in the order of `jobs`.

    Each job is a dict of draft_prompt's arguments after the project name (phase_key, framework_key, title and,
    optionally, focus and rationale). Only the slow Claude calls run in other threads; each runs inside the
    caller's Flask app context (llm_service needs it), and writing the results to the database is left to the
    caller. Never raises: a job that fails gets the fallback prompt and the other jobs carry on."""
    jobs = list(jobs)
    if not jobs:
        return []
    # Worker threads start with no app context of their own. Without one here (some tests) there is nothing to pass on.
    app = current_app._get_current_object() if has_app_context() else None

    def draft_one(job):
        try:
            with app.app_context() if app else nullcontext():
                return draft_prompt(project_name, **job)
        except Exception as exc:  # draft_prompt does not raise; this is for what nobody planned for
            print(f"[prompt-drafting] could not draft {ascii(job.get('title'))}: {ascii(str(exc))}")
            return {"prompt_text": fallback_prompt(job.get("title"), job.get("focus"), ""),
                    "framework_key": job.get("framework_key"), "drafted": False}

    with ThreadPoolExecutor(max_workers=min(MAX_DRAFT_WORKERS, len(jobs))) as pool:
        return list(pool.map(draft_one, jobs))  # map keeps the input order, whichever draft finishes first


def create_drafted_card(
    project_name, phase_key, title, research_method=None, focus=None, rationale=None,
    framework_key=None, priority=None, suggested_from_work_item_id=None,
):
    project_id = projects_repo.get_or_create_id(project_name)
    resolved = resolve_framework(phase_key, framework_key)
    card_id = research_task_service.create_task(
        project_id, str(phase_key), title, priority=priority, research_method=research_method,
        entities=focus or None, framework_key=resolved, rationale=rationale,
        suggested_from_work_item_id=suggested_from_work_item_id,
    )
    draft = draft_prompt(project_name, phase_key, resolved, title, focus=focus, rationale=rationale)
    # The card already exists during the slow draft; a prompt the user wrote meanwhile wins.
    research_work_items_repo.fill_empty_draft_prompt(card_id, draft["prompt_text"])
    return {"card_id": card_id, "drafted": draft["drafted"]}
