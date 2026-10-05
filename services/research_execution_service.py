# services/research_execution_service.py
"""Starting research runs and collecting their results.

Only the user's Run action reaches start_runs (through board_service). The Supervisor has
no path here. That's how the "nothing runs without your approval" rule is kept.
"""
import re
import threading
from datetime import datetime

from flask import current_app

from db.repositories import projects_repo, research_runs_repo, research_work_items_repo, sources_repo

from . import insights_service, llm_service, openai_service, research_run_service, research_task_service
from .deep_research_output import extract_deep_research_output
from .file_index_service import HIDDEN_SOURCE_FILES, ensure_file_id, reconcile_file_index, reconcile_selected_file_ids
from .file_service import load_project_files, save_project_file
from .phases import PHASE_DEFINITIONS
from .project_service import get_project_file_path

MAX_FILE_ANALYSIS_TOTAL_CHARS = 60000
MAX_FILE_ANALYSIS_PER_FILE_CHARS = 15000
MAX_WEB_SOURCES_LISTED = 20
# Framework prompts ask for long structured reports.
FILE_ANALYSIS_MAX_TOKENS = 8000
SYNTHESIS_MAX_TOKENS = 8000
# A "My files" or options-report run still "running" after this long was cut off (app restart).
ABANDONED_LOCAL_RUN_SECONDS = 1800

RUNNABLE_METHODS = ("TARGETED_WEB", "FILE_ANALYSIS", "SYNTHESIS")
NOT_RUNNABLE_MESSAGE = (
    "This research has no prompt or no method, so it can't run. Move it back to draft and edit it."
)
PHASE7_LOCKED_MESSAGE = "Phase 7 opens when Phases 1 to 6 each have finished research."

FILE_ANALYSIS_SYSTEM_PROMPT = (
    "You are a research analyst for an Australian higher-education competitive analysis "
    "project. Use ONLY the provided source files. If something cannot be answered from them, "
    "say MISSING -- do not guess or use general knowledge."
)

SYNTHESIS_SYSTEM_PROMPT = (
    "You are a senior strategy consultant synthesising a competitive "
    "landscape analysis for Australian higher education. Using ONLY the "
    "phase summaries provided, identify the key strategic options available. "
    "Use the SO WHAT / NOW WHAT framework. Write in Australian English."
)


def _spawn(target, *args):
    """Run target(*args) on a background thread inside an app context. Tests replace this."""
    app = current_app._get_current_object()

    def runner():
        with app.app_context():
            target(*args)

    threading.Thread(target=runner, daemon=True).start()


def start_runs(project_name, card_ids):
    project_id = projects_repo.get_id(project_name)
    result = {"started": [], "not_ready": [], "failed": []}
    for card_id in card_ids:
        card = research_work_items_repo.get(card_id)
        if card is None or project_id is None or card["project_id"] != project_id or card["status"] != "READY":
            result["not_ready"].append(card_id)
            continue
        method = card["research_method"]
        prompt_text = (card["prompt_text"] or "").strip()
        if method not in RUNNABLE_METHODS or not prompt_text:
            result["failed"].append({"id": card_id, "error": NOT_RUNNABLE_MESSAGE})
            continue
        if method == "SYNTHESIS" and not research_task_service.phase7_readiness(project_id)["unlocked"]:
            result["failed"].append({"id": card_id, "error": PHASE7_LOCKED_MESSAGE})
            continue
        if not research_task_service.claim_transition(card_id, "READY", "RUNNING"):
            result["not_ready"].append(card_id)
            continue

        run_id = research_run_service.create_run(project_name, None, None, prompt_text)
        research_run_service.update_run(project_name, run_id, research_work_item_id=card_id, prompt_text=prompt_text)
        try:
            if method == "TARGETED_WEB":
                response = openai_service.start_deep_research(prompt_text)
                research_run_service.update_run(project_name, run_id, response_id=response.id)
            elif method == "FILE_ANALYSIS":
                _spawn(run_file_analysis, project_name, card_id, run_id)
            else:
                _spawn(run_synthesis, project_name, card_id, run_id)
        except Exception as exc:
            research_run_service.fail_run(project_name, run_id, str(exc))
            research_task_service.transition_task(card_id, "FAILED")
            result["failed"].append({"id": card_id, "error": str(exc)})
            continue
        result["started"].append(card_id)
    return result


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


def _build_file_analysis_messages(prompt_text, files):
    system_prompt = f"{FILE_ANALYSIS_SYSTEM_PROMPT}\n\n# RESEARCH INSTRUCTIONS\n\n{prompt_text}"
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


def run_file_analysis(project_name, card_id, run_id):
    """Background worker. On failure the run is failed; the board's refresh then fails the card."""
    try:
        card = research_work_items_repo.get(card_id)
        system_prompt, user_message = _build_file_analysis_messages(
            card["prompt_text"] or card["title"], _selected_project_files(project_name)
        )
        output_text = llm_service.prompt_completion(system_prompt, user_message, max_tokens=FILE_ANALYSIS_MAX_TOKENS)
        research_run_service.update_run(
            project_name, run_id, status="completed", output_text=output_text,
            completed_at=datetime.now().isoformat(),
        )
    except Exception as exc:
        research_run_service.fail_run(project_name, run_id, str(exc))


def run_synthesis(project_name, card_id, run_id):
    """Background worker for the Phase 7 options report. Writes Insights Phase 7 and completes the card."""
    try:
        card = research_work_items_repo.get(card_id)
        current = insights_service.load_current_insights(project_name)
        summaries = []
        for phase_key, definition in PHASE_DEFINITIONS.items():
            if phase_key == "7":
                continue
            summary = current["phases"].get(phase_key, {}).get("summary", "MISSING")
            if summary and summary != "MISSING":
                summaries.append(f"## {definition['title']}\n\n{summary}")
        user_message = (
            f"{card['prompt_text'] or ''}\n\n# PHASE SUMMARIES\n\n"
            + ("\n\n".join(summaries) if summaries else "(no phase summaries available)")
        )
        text = llm_service.prompt_completion(SYNTHESIS_SYSTEM_PROMPT, user_message, max_tokens=SYNTHESIS_MAX_TOKENS)

        # Re-load: the Claude call is slow, and a report link or a phase refresh saved
        # meanwhile must not be reverted. Only Phase 7's summary and confidence change.
        latest = insights_service.load_current_insights(project_name)
        phases = dict(latest["phases"])
        phase_7 = dict(phases.get("7", {}))
        phase_7["summary"] = text
        phase_7["confidence"] = "medium"
        phases["7"] = phase_7
        insights_service.save_insights(project_name, {
            # Kept unchanged: generated_at marks when the Phase 1-6 summaries were last refreshed.
            "generated_at": latest.get("generated_at") or datetime.now().isoformat(),
            "competitors": latest.get("competitors", []),
            "competitor_landscape_markdown": latest.get("competitor_landscape_markdown", ""),
            "phases": phases,
        })
        research_run_service.update_run(
            project_name, run_id, status="completed", output_text=text, completed_at=datetime.now().isoformat(),
        )
        research_task_service.transition_task(card_id, "COMPLETE")
    except Exception as exc:
        research_run_service.fail_run(project_name, run_id, str(exc))


def _needs_web_sync(run):
    # Only web runs have a response_id (file analysis does not). The browser poller can
    # flip a run to "completed" without ever saving its text, so "no text yet" counts
    # as pending too, not just "running".
    return (
        run is not None
        and bool(run["response_id"])
        and not run["output_text"]
        and run["status"] in ("running", "completed")
    )


def _web_research_error(response):
    for attr in ("error", "last_error"):
        err = getattr(response, attr, None)
        if not err:
            continue
        if isinstance(err, dict):
            return err.get("message") or str(err)
        if isinstance(err, str):
            return err
        return getattr(err, "message", None) or str(err)
    return None


def _format_web_research_output(text, citations):
    # Sources go first, because build_context clips from the end and the supervisor needs
    # them to judge how well-evidenced the findings are. Real reports cite one page dozens
    # of times with different "#:~:text=" fragments, so list each page once and cap the
    # list; otherwise it would fill the supervisor's whole view of the report.
    sources = {}
    for c in citations:
        if c["type"] == "url":
            page = c["url"].split("#")[0]
            sources.setdefault(page, f"{c['title']} - {page}")
        else:
            sources.setdefault(c["filename"], c["filename"])
    if not sources:
        return text

    listed = list(sources.values())[:MAX_WEB_SOURCES_LISTED]
    lines = [f"- {source}" for source in listed]
    if len(sources) > len(listed):
        lines.append(f"(+{len(sources) - len(listed)} more)")
    return f"Sources ({len(sources)}):\n" + "\n".join(lines) + "\n\n" + text


def needs_web_sync(run):
    return _needs_web_sync(run)


def sync_web_research_run(project_name, run):
    """Check one web run with OpenAI and store its outcome. Raises if OpenAI can't be reached."""
    response = openai_service.retrieve_deep_research(run["response_id"])
    status = getattr(response, "status", None)
    if status == "completed":
        text, _markdown, citations = extract_deep_research_output(response)
        if text:
            research_run_service.update_run(
                project_name, run["id"], status="completed",
                output_text=_format_web_research_output(text, citations),
                completed_at=datetime.now().isoformat(),
            )
        else:
            research_run_service.fail_run(
                project_name, run["id"], "OpenAI reported the research finished but returned no text"
            )
    elif status in ("failed", "incomplete", "cancelled"):
        research_run_service.fail_run(
            project_name, run["id"], _web_research_error(response) or f"OpenAI reported status: {status}"
        )
    # queued / in_progress: still working; checked again on the next refresh.


def sync_web_research_runs(project_name):
    """Check every unfinished web run of the project (kept for callers that want all at once)."""
    project_id = projects_repo.get_or_create_id(project_name)
    for item in research_work_items_repo.list_for_project(project_id):
        run = research_runs_repo.find_latest_for_work_item(item["id"])
        if not _needs_web_sync(run):
            continue
        try:
            sync_web_research_run(project_name, run)
        except Exception as exc:  # one flaky lookup must not block the whole refresh
            print(f"[board] could not check web research run {run['id']}: {exc}")


METHOD_LABELS = {"TARGETED_WEB": "Web research", "FILE_ANALYSIS": "My files", "SYNTHESIS": "Options report"}
REPORT_TITLE_MAX_CHARS = 80
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_title(title):
    cleaned = _UNSAFE_FILENAME_CHARS.sub(" ", title or "")
    cleaned = " ".join(cleaned.split()).strip(" .")
    cleaned = cleaned[:REPORT_TITLE_MAX_CHARS].rstrip(" .")
    return cleaned or "Untitled"


def report_filename(project_name, card, when):
    base = f"Research P{card['phase_key']} - {_safe_title(card['title'])} ({when:%Y-%m-%d})"
    candidate = f"{base}.md"
    suffix = 2
    while True:
        path = get_project_file_path(project_name, candidate)
        if path is None:
            raise ValueError(f"Could not make a valid file name for {card['title']!r}")
        if not path.exists():
            return candidate
        candidate = f"{base} {suffix}.md"
        suffix += 1


def _report_content(card, run):
    phase = PHASE_DEFINITIONS.get(card["phase_key"], {}).get("title", "")
    return (
        f"# {card['title']}\n\n"
        f"- Phase: {card['phase_key']} - {phase}\n"
        f"- Method: {METHOD_LABELS.get(card['research_method'], card['research_method'] or '')}\n"
        f"- Finished: {(run['completed_at'] or '')[:10]}\n\n"
        f"## Research prompt\n\n{run['prompt_text'] or card['prompt_text'] or ''}\n\n"
        f"## Report\n\n{run['output_text'] or ''}\n"
    )


def save_report(project_name, card_id, run_id):
    """Save a finished run's report as a source file, once. Returns its stable file id."""
    run = research_runs_repo.get(run_id)
    if run["report_stable_file_id"]:
        return run["report_stable_file_id"]
    card = research_work_items_repo.get(card_id)
    filename = report_filename(project_name, card, datetime.now())
    save_project_file(project_name, filename, _report_content(card, run))
    stable_id = ensure_file_id(project_name, filename)
    research_run_service.update_run(project_name, run_id, report_stable_file_id=stable_id)
    return stable_id


def link_report(project_name, card_id, run_id):
    """Link a saved report to its card's phase, once. A later unlink by the user is respected."""
    run = research_runs_repo.get(run_id)
    if not run["report_stable_file_id"] or run["report_linked_at"]:
        return False
    project_id = projects_repo.get_or_create_id(project_name)
    card = research_work_items_repo.get(card_id)
    linked_at = datetime.now().isoformat()
    source = sources_repo.get_by_stable_id(project_id, run["report_stable_file_id"])
    if source is None:  # the user deleted the file before it was linked
        research_run_service.update_run(project_name, run_id, report_linked_at=linked_at)
        return False

    # Check if the file actually exists on disk
    file_path = get_project_file_path(project_name, source["filename"])
    if file_path is None or not file_path.exists():
        research_run_service.update_run(project_name, run_id, report_linked_at=linked_at)
        return False

    current = insights_service.load_current_insights(project_name)
    phases = dict(current["phases"])
    phase = dict(phases[card["phase_key"]])
    ids = list(phase.get("linked_file_ids") or [])
    names = list(phase.get("linked_files") or [])
    added = source["stable_file_id"] not in ids
    if added:
        ids.append(source["stable_file_id"])
        names.append(source["filename"])
        phase["linked_file_ids"] = ids
        phase["linked_files"] = names
        phases[card["phase_key"]] = phase
        insights_service.save_insights(project_name, {
            # Unchanged: generated_at marks when summaries were last generated, not when files were linked.
            "generated_at": current.get("generated_at", ""),
            "competitors": current.get("competitors", []),
            "competitor_landscape_markdown": current.get("competitor_landscape_markdown", ""),
            "phases": phases,
        })
    research_run_service.update_run(project_name, run_id, report_linked_at=linked_at)
    return added
