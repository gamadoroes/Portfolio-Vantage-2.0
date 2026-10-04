# services/supervisor_service.py
from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo

from . import insights_service
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
