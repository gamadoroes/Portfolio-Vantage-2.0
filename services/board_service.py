# services/board_service.py
"""The Research Board: the user's card actions, the board's refresh cycle, and the board state.

Only these user actions approve (-> READY) or run (-> RUNNING via start_runs) research.
"""
import json
import threading
from datetime import datetime, timezone

from db.repositories import (
    agent_decisions_repo,
    projects_repo,
    research_runs_repo,
    research_work_items_repo,
    sources_repo,
)

from . import (
    insights_service,
    research_execution_service,
    research_run_service,
    research_task_service,
    supervisor_service,
)
from .phases import PHASE_DEFINITIONS
from .project_service import load_project_prompt, save_project_prompt
from .prompt_drafting_service import FALLBACK_MARKER, create_drafted_card, draft_prompt
from .prompt_frameworks import FRAMEWORK_LABELS, frameworks_for_phase
from .text_utils import as_text_list
from .tools import run_tool

STALE_REVIEW_CLAIM_SECONDS = 600
ACTIVITY_LIMIT = 30
USER_PHASES = ("1", "2", "3", "4", "5", "6")
USER_METHODS = ("TARGETED_WEB", "FILE_ANALYSIS")
EDITABLE_STATUSES = ("PROPOSED", "READY")
OPEN_STATUSES = ("PROPOSED", "READY", "RUNNING", "REVIEWING")
OPTIONS_REPORT_TITLE = "Options for OES report"


class CardNotFound(LookupError):
    """No such card in this project (HTTP 404)."""


class BoardStateError(ValueError):
    """The card is not in a state that allows this action (HTTP 409)."""


class DraftingUnavailable(RuntimeError):
    """Claude could not draft a prompt just now (HTTP 503)."""


# ---- helpers ----

def _project_id(project_name):
    return projects_repo.get_or_create_id(project_name)


def _card(project_name, card_id):
    card = research_work_items_repo.get(card_id)
    if card is None or card["project_id"] != projects_repo.get_id(project_name):
        raise CardNotFound(f"No such research: {card_id}")
    return card


_CHANGED_MESSAGE = "This research has changed since the board was loaded. The board has been refreshed."


def _require_status(card, allowed):
    if card["status"] not in allowed:
        raise BoardStateError(_CHANGED_MESSAGE)


def _record(project_name, decision_type, card=None, **detail):
    if card is not None:
        detail.setdefault("title", card["title"])
    agent_decisions_repo.record(
        _project_id(project_name), decision_type, json.dumps(detail),
        research_work_item_id=card["id"] if card is not None else None,
    )


def _clean_focus(value):
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _json_text_list(raw):
    """A stored JSON list field as a list of strings, also accepting rows that hold a plain string."""
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return as_text_list(value)


def _focus(card):
    return _json_text_list(card["entities_json"])


# ---- card actions ----

def _user_tool(project_name, name, inputs):
    """Run a tool as the user and turn a failure back into the board's existing exceptions."""
    result = run_tool(name, "user", project_name, inputs)
    if result.ok:
        return result.data
    code, message = result.error["code"], result.error["message"]
    if code == "not_found":
        raise CardNotFound(message)
    if code == "conflict":
        raise BoardStateError(message)
    if code == "unavailable":
        raise DraftingUnavailable(message)
    if code == "invalid_input":
        raise ValueError(message)
    raise RuntimeError(message)


def create_card(project_name, data):
    phase_key = str(data.get("phase_key") or "")
    title = (data.get("title") or "").strip()
    method = data.get("research_method")
    if phase_key not in USER_PHASES:
        raise ValueError("Choose a phase from 1 to 6. The options report has its own button in Phase 7.")
    if not title:
        raise ValueError("Add a title.")
    if method not in USER_METHODS:
        raise ValueError("Choose how it runs: web research or your files.")
    draft = bool(data.get("draft_prompt"))
    task = {
        "phase_key": phase_key, "title": title, "research_method": method,
        "focus": _clean_focus(data.get("focus")), "rationale": (data.get("rationale") or "").strip(),
        "framework_key": data.get("framework_key") or None,
        # A typed prompt is ignored when the user asked for one to be drafted, as before.
        "prompt_text": None if draft else (data.get("prompt_text") or "").strip() or None,
        "draft_prompt": draft,
    }
    return _user_tool(project_name, "create_research_task", {"tasks": [task]})["created_task_ids"][0]


def edit_card(project_name, card_id, data):
    card = _card(project_name, card_id)
    _require_status(card, EDITABLE_STATUSES)
    fields = {}
    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title:
            raise ValueError("Add a title.")
        fields["title"] = title
    if "prompt_text" in data:
        fields["prompt_text"] = data.get("prompt_text") or ""
    if "rationale" in data:
        fields["rationale"] = (data.get("rationale") or "").strip() or None
    if "focus" in data:
        focus = _clean_focus(data.get("focus"))
        fields["entities_json"] = json.dumps(focus) if focus else None
    if "research_method" in data:
        method = data.get("research_method")
        if card["research_method"] == "SYNTHESIS":
            if method != "SYNTHESIS":
                raise ValueError("The options report always runs as the options report.")
        elif method not in USER_METHODS:
            raise ValueError("Choose how it runs: web research or your files.")
        fields["research_method"] = method

    changed = {k: v for k, v in fields.items() if card[k] != v}
    if not changed:
        return card_id
    # One statement: a Run or approval racing this edit cannot see the new text on an approved card.
    if not research_work_items_repo.update_editable(card_id, **changed):
        raise BoardStateError(_CHANGED_MESSAGE)
    _record(project_name, "user_edit_unapproved" if card["status"] == "READY" else "user_edit", card)
    return card_id


def _status(project_name, card_id, action):
    _user_tool(project_name, "update_task_status", {"task_id": card_id, "action": action})


def approve(project_name, card_id):
    _status(project_name, card_id, "approve")


def unapprove(project_name, card_id):
    _status(project_name, card_id, "back_to_draft")


def skip(project_name, card_id):
    _status(project_name, card_id, "skip")


def restore(project_name, card_id):
    _status(project_name, card_id, "restore")


def retry(project_name, card_id):
    _status(project_name, card_id, "retry")


def back_to_draft(project_name, card_id):
    _status(project_name, card_id, "back_to_draft")


def redraft(project_name, card_id):
    card = _card(project_name, card_id)
    _require_status(card, EDITABLE_STATUSES)
    draft = draft_prompt(
        project_name, card["phase_key"], card["framework_key"], card["title"],
        focus=_focus(card) or None, rationale=card["rationale"],
    )
    if not draft["drafted"]:
        raise DraftingUnavailable("Couldn't draft from the framework just now. Your prompt is unchanged.")
    # The Claude call above is slow; if the card was approved, edited or started meanwhile, drop the draft.
    if not research_work_items_repo.update_editable(
        card_id, expected_updated_at=card["updated_at"],
        prompt_text=draft["prompt_text"], framework_key=draft["framework_key"],
    ):
        raise BoardStateError(_CHANGED_MESSAGE)
    _record(project_name, "user_redraft", card)


def accept(project_name, card_id):
    _status(project_name, card_id, "accept")


def needs_followup(project_name, card_id):
    _status(project_name, card_id, "needs_followup")


def mark_failed(project_name, card_id):
    _status(project_name, card_id, "mark_failed")


ACTIONS = {
    "approve": approve, "unapprove": unapprove, "skip": skip, "restore": restore, "retry": retry,
    "back-to-draft": back_to_draft, "redraft": redraft, "accept": accept,
    "needs-followup": needs_followup, "mark-failed": mark_failed,
}


def draft_options_report(project_name):
    project_id = _project_id(project_name)
    if not research_task_service.phase7_readiness(project_id)["unlocked"]:
        raise BoardStateError(research_execution_service.PHASE7_LOCKED_MESSAGE)
    if any(c["status"] in OPEN_STATUSES for c in research_work_items_repo.list_for_phase(project_id, "7")):
        raise BoardStateError("An options report is already in your plan.")
    card_id = create_drafted_card(
        project_name, "7", OPTIONS_REPORT_TITLE, research_method="SYNTHESIS",
        rationale="Phases 1 to 6 each have finished research.", framework_key="oes-options-whitespace",
    )["card_id"]
    _record(project_name, "user_draft_options", research_work_items_repo.get(card_id))
    return card_id


def run(project_name, card_ids):
    try:
        ids = [int(i) for i in card_ids]
    except (TypeError, ValueError) as exc:
        raise ValueError("card_ids must be a list of research ids") from exc
    result = research_execution_service.start_runs(project_name, ids)
    if result["started"]:
        _record(project_name, "user_run", count=len(result["started"]))
    return result


def save_objective(project_name, objective):
    save_project_prompt(project_name, "project_prompt", objective or "")
    _record(project_name, "user_objective")


def get_report(project_name, card_id):
    card = _card(project_name, card_id)
    run_row = research_runs_repo.find_latest_for_work_item(card_id)
    if run_row is None or not run_row["output_text"]:
        raise CardNotFound("This research has no report yet.")
    filename = None
    if run_row["report_stable_file_id"]:
        source = sources_repo.get_by_stable_id(card["project_id"], run_row["report_stable_file_id"])
        filename = source["filename"] if source else None
    return {"title": card["title"], "text": run_row["output_text"], "filename": filename}


# ---- refresh ----

_refresh_locks = {}
_refresh_locks_guard = threading.Lock()


def _lock_for(project_name):
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(project_name, threading.Lock())


def _age_seconds(iso_value):
    try:
        return (datetime.now() - datetime.fromisoformat(iso_value)).total_seconds()
    except (TypeError, ValueError):
        return 0


def _release_stale_review_claims(project_id):
    for card in research_work_items_repo.list_for_project(project_id):
        if card["status"] == "REVIEWING" and _age_seconds(card["updated_at"]) > STALE_REVIEW_CLAIM_SECONDS:
            research_task_service.claim_transition(card["id"], "REVIEWING", "RUNNING")


def _fail_abandoned_local_runs(project_name, project_id):
    for card in research_work_items_repo.list_for_project(project_id):
        if card["status"] != "RUNNING" or card["research_method"] not in ("FILE_ANALYSIS", "SYNTHESIS"):
            continue
        run_row = research_runs_repo.find_latest_for_work_item(card["id"])
        if (run_row and run_row["status"] == "running"
                and _age_seconds(run_row["created_at"]) > research_execution_service.ABANDONED_LOCAL_RUN_SECONDS):
            research_run_service.fail_run(
                project_name, run_row["id"], "Stopped before finishing. The app may have restarted while it was running."
            )


def _settle_card(project_name, card, defer_linking):
    run_row = research_runs_repo.find_latest_for_work_item(card["id"])
    if run_row is None:
        return 0
    if card["status"] == "RUNNING" and run_row["status"] in ("failed", "cancelled"):
        research_task_service.transition_task(card["id"], "FAILED")
        return 0
    # The options report writes Insights itself and is never reviewed.
    if card["research_method"] == "SYNTHESIS" or run_row["status"] != "completed" or not run_row["output_text"]:
        return 0
    linked = 0
    if run_row["prompt_text"]:  # only runs the board started produce report files
        try:
            research_execution_service.save_report(project_name, card["id"], run_row["id"])
            if not defer_linking and research_execution_service.link_report(project_name, card["id"], run_row["id"]):
                linked = 1
        except Exception as exc:  # the report is retried on the next refresh; the review need not wait
            print(f"[board] could not save or link the report for research {card['id']}: {exc}")
    if card["status"] == "RUNNING":
        supervisor_service.review_card(project_name, card["id"])
    return linked


def refresh(project_name, defer_linking=False):
    project_id = _project_id(project_name)
    linked = 0
    lock = _lock_for(project_name)
    if lock.acquire(blocking=False):  # another tab's refresh is already doing this work
        try:
            research_execution_service.sync_web_research_runs(project_name)
            _release_stale_review_claims(project_id)
            _fail_abandoned_local_runs(project_name, project_id)
            for card in research_work_items_repo.list_for_project(project_id):
                try:
                    linked += _settle_card(project_name, card, defer_linking)
                except Exception as exc:  # one bad card must not stop the others
                    print(f"[board] could not settle research {card['id']}: {exc}")
        finally:
            lock.release()
    state = get_board_state(project_name)
    state["linked_reports"] = linked
    return state


# ---- state ----

def _to_utc(value):
    """Browser timestamps are UTC ('...Z'); server timestamps are naive local time."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _run_state(project_id, run_row):
    if run_row is None:
        return None
    try:
        start = datetime.fromisoformat(run_row["created_at"])
        end = datetime.fromisoformat(run_row["completed_at"]) if run_row["completed_at"] else datetime.now()
        elapsed = max(0, int((end - start).total_seconds()))
    except (TypeError, ValueError):
        elapsed = None
    filename = None
    if run_row["report_stable_file_id"]:
        source = sources_repo.get_by_stable_id(project_id, run_row["report_stable_file_id"])
        filename = source["filename"] if source else None
    return {
        "id": run_row["id"], "status": run_row["status"], "error": run_row["error"],
        "elapsed_seconds": elapsed, "completed_at": run_row["completed_at"],
        "has_report": bool(run_row["output_text"]), "report_filename": filename,
    }


def _brief(card):
    return {"id": card["id"], "title": card["title"], "status": card["status"]}


def _card_state(project_id, card, cards, by_id, run_row, last_review_event):
    prompt = card["prompt_text"] or ""
    review_error = None
    if card["status"] == "RUNNING" and last_review_event and last_review_event["decision_type"] == "review_error":
        review_error = json.loads(last_review_event["detail"] or "{}").get("error") or "Review failed"
    suggested = by_id.get(card["suggested_from_work_item_id"])
    deps = [by_id.get(r["depends_on_work_item_id"]) for r in research_work_items_repo.list_dependencies(card["id"])]
    return {
        "id": card["id"], "phase_key": card["phase_key"], "title": card["title"], "status": card["status"],
        "research_method": card["research_method"],
        "method_label": research_execution_service.METHOD_LABELS.get(card["research_method"]),
        "framework_key": card["framework_key"], "framework_label": FRAMEWORK_LABELS.get(card["framework_key"]),
        "prompt_text": card["prompt_text"],
        "needs_prompt_edit": not prompt.strip() or FALLBACK_MARKER in prompt,
        "focus": _focus(card), "rationale": card["rationale"], "priority": card["priority"],
        "suggested_from": {"id": suggested["id"], "title": suggested["title"]} if suggested else None,
        "followups": [_brief(c) for c in cards if c["suggested_from_work_item_id"] == card["id"]],
        "depends_on": [_brief(d) for d in deps if d is not None],
        "completeness_score": card["completeness_score"], "evidence_score": card["evidence_score"],
        "gaps": _json_text_list(card["identified_gaps_json"]),
        "human_review_required": bool(card["human_review_required"]),
        "retry_count": card["retry_count"], "max_retries": card["max_retries"],
        "run": _run_state(project_id, run_row), "review_error": review_error,
        "created_at": card["created_at"], "updated_at": card["updated_at"],
    }


def _phase7_state(project_name, project_id, run_rows):
    readiness = research_task_service.phase7_readiness(project_id)
    generated_at = insights_service.load_current_insights(project_name).get("generated_at") or ""
    report_times = [
        (r["completed_at"], _to_utc(r["completed_at"])) for r in run_rows
        if r is not None and r["report_stable_file_id"] and r["completed_at"]
    ]
    report_times = [(raw, parsed) for raw, parsed in report_times if parsed is not None]
    newest = max(report_times, key=lambda pair: pair[1]) if report_times else None
    generated = _to_utc(generated_at) if generated_at else None
    stale = newest is not None and (generated is None or generated < newest[1])
    return {
        **readiness, "summaries_generated_at": generated_at,
        "newest_report_at": newest[0] if newest else None, "summaries_stale": stale,
    }


_USER_TEXT = {
    "user_add": 'You added "{title}".',
    "user_edit": 'You edited "{title}".',
    "user_edit_unapproved": 'You edited "{title}". It needs your approval again.',
    "user_approve": 'You approved "{title}".',
    "user_unapprove": 'You moved "{title}" back to draft.',
    "user_back_to_draft": 'You moved "{title}" back to draft.',
    "user_skip": 'You skipped "{title}".',
    "user_restore": 'You restored "{title}" to the plan.',
    "user_retry": 'You approved "{title}" to run again.',
    "user_redraft": 'You re-drafted the prompt for "{title}".',
    "user_accept": 'You accepted "{title}" as finished.',
    "user_needs_followup": 'You asked for a follow-up to "{title}".',
    "user_mark_failed": 'You marked "{title}" as failed.',
    "user_draft_options": "You drafted the options report.",
    "user_objective": "You updated the objective.",
}

_OUTCOME_TEXT = {
    "COMPLETE": "finished",
    "FOLLOW_UP_REQUIRED": "finished, with a follow-up suggested for your approval",
    "NEEDS_HUMAN": "it needs your review",
    "FAILED": "it did not produce a usable result",
}


def _activity_item(decision, by_id):
    try:
        detail = json.loads(decision["detail"] or "{}")
    except ValueError:
        detail = {}
    card = by_id.get(decision["research_work_item_id"])
    title = card["title"] if card else detail.get("title", "a research")
    dtype = decision["decision_type"]
    actor = "supervisor"
    if dtype == "user_run":
        actor, count = "you", detail.get("count", 0)
        text = f"You started {count} {'research' if count == 1 else 'researches'}."
    elif dtype in _USER_TEXT:
        actor, text = "you", _USER_TEXT[dtype].format(title=title)
    elif dtype in ("propose_tasks", "create_research_task"):
        execution = detail.get("execution", {})
        if execution.get("success"):
            n = len(execution.get("result", {}).get("created_task_ids", []))
            text = f"Supervisor drafted {n} new {'research' if n == 1 else 'researches'}. Nothing runs until you approve."
        else:
            text = f"Supervisor's draft was rejected: {execution.get('error', 'unknown error')}"
    elif dtype == "no_action":
        reason = detail.get("input", {}).get("reason")
        text = f"Supervisor had nothing new to propose: {reason}" if reason else "Supervisor had nothing new to propose."
    elif dtype == "review_outcome":
        outcome = detail.get("input", {}).get("outcome")
        text = f'Supervisor reviewed "{title}": {_OUTCOME_TEXT.get(outcome, outcome)}.'
    elif dtype == "review_error":
        text = f'The review of "{title}" didn\'t complete. It will be retried.'
    else:
        text = f"Supervisor: {dtype.replace('_', ' ')}"
    return {"at": decision["created_at"], "actor": actor, "text": text}


def get_board_state(project_name):
    project_id = _project_id(project_name)
    cards = research_work_items_repo.list_for_project(project_id)
    by_id = {c["id"]: c for c in cards}
    runs = {c["id"]: research_runs_repo.find_latest_for_work_item(c["id"]) for c in cards}
    decisions = agent_decisions_repo.list_for_project(project_id, limit=200)
    last_review_event = {}
    for d in decisions:  # newest first
        wid = d["research_work_item_id"]
        if wid is not None and wid not in last_review_event and d["decision_type"] in ("review_outcome", "review_error"):
            last_review_event[wid] = d
    return {
        "project": project_name,
        "objective": load_project_prompt(project_name, "project_prompt") or "",
        "phases": [
            {"key": key, "title": definition["title"],
             "frameworks": [{"key": fk, "label": FRAMEWORK_LABELS[fk]} for fk in frameworks_for_phase(key)]}
            for key, definition in PHASE_DEFINITIONS.items()
        ],
        "cards": [_card_state(project_id, c, cards, by_id, runs[c["id"]], last_review_event.get(c["id"])) for c in cards],
        "phase7": _phase7_state(project_name, project_id, runs.values()),
        "activity": [_activity_item(d, by_id) for d in decisions[:ACTIVITY_LIMIT]],
    }
