# db/migrate.py
import json
import sys
from pathlib import Path

from db.repositories import (
    artefacts_repo,
    chat_repo,
    excluded_competitors_repo,
    projects_repo,
    research_runs_repo,
    research_tasks_repo,
    sources_repo,
)
from services.phases import PHASE_DEFINITIONS


def _read_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _migrate_project_core(project_dir, project_name):
    project_id = projects_repo.get_or_create_id(project_name)

    metadata = _read_json(project_dir / "metadata.json", {"description": "", "archived": False})
    projects_repo.save_metadata(
        project_name, metadata.get("description", ""), bool(metadata.get("archived", False))
    )

    file_index = _read_json(project_dir / "file_index.json", {"files": {}})
    stable_id_to_source_id = {}
    for stable_file_id, rec in (file_index.get("files") or {}).items():
        filename = rec.get("filename") if isinstance(rec, dict) else rec
        if not filename:
            continue
        source_id = sources_repo.upsert(project_id, stable_file_id, filename)
        stable_id_to_source_id[stable_file_id] = source_id

    config = _read_json(project_dir / "config.json", {})
    for stable_file_id in config.get("selected_file_ids", []):
        source_id = stable_id_to_source_id.get(stable_file_id)
        if source_id is not None:
            sources_repo.set_selected(project_id, source_id, True)

    artifacts = _read_json(project_dir / "artifacts.json", {})
    for art_id, art in artifacts.items():
        source_id = None
        backing_filename = art.get("filename")
        if backing_filename:
            source_row = sources_repo.get_by_filename(project_id, backing_filename)
            if source_row is not None:
                source_id = source_row["id"]
        artefacts_repo.upsert(art_id, project_id, art.get("name", art_id), source_id=source_id)

    chat_history = _read_json(project_dir / "chat_history.json", {})
    for chat_id, session in chat_history.items():
        if chat_repo.get_session(chat_id) is not None:
            continue  # already migrated on a prior run -- skip, don't re-append messages
        chat_repo.create_session(chat_id, project_id, session.get("name", chat_id))
        for message in session.get("messages", []):
            chat_repo.append_message(
                chat_id,
                message.get("role", "user"),
                message.get("content", ""),
                artefact_id=message.get("artifact_id"),
            )

    research_runs = _read_json(project_dir / "research_runs.json", {})
    for run_id, run in research_runs.items():
        if research_runs_repo.get(run_id) is not None:
            continue
        chat_id = run.get("chat_id")
        if chat_id is not None and chat_repo.get_session(chat_id) is None:
            # Pre-existing data drift: the old JSON storage never enforced
            # referential integrity between research_runs.json and
            # chat_history.json, so this backlink can point at a chat
            # session that no longer exists (e.g. deleted via the UI).
            # Preserve the run but drop the dangling backlink rather than
            # letting the chat_session_id FK crash the whole migration.
            chat_id = None
        research_runs_repo.create(
            run_id,
            project_id,
            run.get("response_id"),
            chat_id,
            run.get("prompt_preview", ""),
            status=run.get("status", "completed"),
        )
        research_runs_repo.update(
            run_id,
            artefact_id=run.get("artifact_id"),
            error=run.get("error"),
            completed_at=run.get("completed_at"),
        )

    excluded = _read_json(project_dir / "excluded_competitors.json", [])
    for name in excluded:
        excluded_competitors_repo.add(project_id, name)

    return project_id, stable_id_to_source_id


def _migrate_insights_version(project_id, stable_id_to_source_id, version_number, version_data):
    generated_at = version_data.get("generated_at", "")
    competitors_json = json.dumps(version_data.get("competitors", []))
    competitor_md = version_data.get("competitor_landscape_markdown", "")
    version_id = research_tasks_repo.create_version(
        project_id, version_number, generated_at, competitors_json, competitor_md
    )

    for phase_key, phase in (version_data.get("phases") or {}).items():
        if phase_key not in PHASE_DEFINITIONS:
            continue
        summary = phase.get("summary", "MISSING")
        if summary == "MISSING":
            continue
        task_id = research_tasks_repo.get_or_create_task(
            project_id, phase_key, phase.get("title") or PHASE_DEFINITIONS[phase_key]["title"]
        )
        gaps_notes = "\n".join(phase.get("gaps") or []) or None
        finding_id = research_tasks_repo.create_finding(
            task_id, version_id, summary, gaps_notes, phase.get("confidence")
        )
        for raw_text in phase.get("evidence_sources") or []:
            evidence_id = research_tasks_repo.create_evidence(project_id, raw_text)
            research_tasks_repo.link_finding_evidence(finding_id, evidence_id)
        for stable_file_id in phase.get("linked_file_ids") or []:
            source_id = stable_id_to_source_id.get(stable_file_id)
            if source_id is None:
                continue
            evidence_id = research_tasks_repo.create_evidence(project_id, None, source_id=source_id)
            research_tasks_repo.link_finding_evidence(finding_id, evidence_id)

    for phase_key, phase in (version_data.get("phases") or {}).items():
        for topic in phase.get("suggested_topics") or []:
            research_tasks_repo.create_ad_hoc_task(project_id, topic)

    return 1 if any(
        phase.get("summary", "MISSING") != "MISSING"
        for phase in (version_data.get("phases") or {}).values()
    ) else 0


def _migrate_insights(project_dir, project_id, stable_id_to_source_id):
    if research_tasks_repo.get_latest_version(project_id) is not None:
        return 0  # already migrated on a prior run

    history = _read_json(project_dir / "insights_history.json", [])
    task_count = 0
    seen_versions = set()
    for entry in history:
        version_number = entry.get("version")
        if version_number in seen_versions:
            continue
        seen_versions.add(version_number)
        task_count += _migrate_insights_version(
            project_id, stable_id_to_source_id, version_number, entry.get("data", {})
        )

    current = _read_json(project_dir / "insights.json", None)
    if current is not None:
        last_entry_data = history[-1]["data"] if history else None
        if current != last_entry_data:
            next_version = research_tasks_repo.next_version_number(project_id)
            task_count += _migrate_insights_version(
                project_id, stable_id_to_source_id, next_version, current
            )

    return task_count


def migrate_all_projects(projects_root="projects"):
    root = Path(projects_root)
    report = {}
    if not root.exists():
        return report

    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        project_name = project_dir.name
        project_id, stable_id_to_source_id = _migrate_project_core(project_dir, project_name)
        research_tasks_migrated = _migrate_insights(project_dir, project_id, stable_id_to_source_id)

        report[project_name] = {
            "artefacts": len(artefacts_repo.list_for_project(project_id)),
            "research_runs": len(research_runs_repo.list_for_project(project_id)),
            "research_tasks": research_tasks_migrated,
        }

    return report


if __name__ == "__main__":
    from db.migrate_runner import apply_migrations

    apply_migrations()
    result = migrate_all_projects()
    for project_name, counts in result.items():
        print(f"{project_name}: {counts}")
    print(f"Migrated {len(result)} project(s).")
    sys.exit(0)
