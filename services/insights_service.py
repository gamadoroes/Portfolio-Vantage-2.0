# services/insights_service.py
import json

from db.repositories import excluded_competitors_repo, projects_repo, research_tasks_repo, sources_repo

from .phases import PHASE_DEFINITIONS


def _empty_phase_payload(phase_key):
    return {
        "title": PHASE_DEFINITIONS[phase_key]["title"],
        "summary": "MISSING",
        "confidence": "none",
        "evidence_sources": [],
        "gaps": [],
        "suggested_topics": [],
        "linked_files": [],
        "linked_file_ids": [],
    }


def save_insights(project_name, data):
    project_id = projects_repo.get_or_create_id(project_name)
    version_number = research_tasks_repo.next_version_number(project_id)
    version_id = research_tasks_repo.create_version(
        project_id,
        version_number,
        data.get("generated_at", ""),
        json.dumps(data.get("competitors", [])),
        data.get("competitor_landscape_markdown", ""),
    )

    phases = data.get("phases", {})
    for phase_key in PHASE_DEFINITIONS:
        phase = phases.get(phase_key, _empty_phase_payload(phase_key))
        task_id = research_tasks_repo.get_or_create_task(
            project_id, phase_key, phase.get("title") or PHASE_DEFINITIONS[phase_key]["title"]
        )
        gaps_notes = "\n".join(phase.get("gaps") or []) or None
        finding_id = research_tasks_repo.create_finding(
            task_id, version_id, phase.get("summary", "MISSING"), gaps_notes, phase.get("confidence")
        )

        linked_ids = set(phase.get("linked_file_ids") or [])
        for raw_text in phase.get("evidence_sources") or []:
            evidence_id = research_tasks_repo.create_evidence(project_id, raw_text)
            research_tasks_repo.link_finding_evidence(finding_id, evidence_id)
        for stable_file_id in linked_ids:
            source_row = sources_repo.get_by_stable_id(project_id, stable_file_id)
            if source_row is None:
                continue
            evidence_id = research_tasks_repo.create_evidence(project_id, None, source_id=source_row["id"])
            research_tasks_repo.link_finding_evidence(finding_id, evidence_id)

        for topic in phase.get("suggested_topics") or []:
            research_tasks_repo.create_ad_hoc_task(project_id, topic)


def _build_phase_data(version_id, phase_key, task_id, project_id):
    # Scoped to (task_id, version_id) — NOT list_findings_for_version() + [0], which
    # would return an arbitrary one of all 7 phases' findings for this version.
    finding = research_tasks_repo.get_finding_for_task_in_version(task_id, version_id)
    phase = _empty_phase_payload(phase_key)
    if finding is None:
        return phase

    phase["summary"] = finding["content"]
    phase["confidence"] = finding["confidence"] or "none"
    phase["gaps"] = finding["gaps_notes"].split("\n") if finding["gaps_notes"] else []

    evidence_rows = research_tasks_repo.list_evidence_for_finding(finding["id"])
    evidence_sources = []
    linked_files = []
    linked_file_ids = []
    for row in evidence_rows:
        if row["raw_text"]:
            evidence_sources.append(row["raw_text"])
        if row["source_id"] is not None:
            sources = sources_repo.list_for_project(project_id)
            match = next((s for s in sources if s["id"] == row["source_id"]), None)
            if match is not None:
                linked_files.append(match["filename"])
                linked_file_ids.append(match["stable_file_id"])
    phase["evidence_sources"] = evidence_sources
    phase["linked_files"] = linked_files
    phase["linked_file_ids"] = linked_file_ids
    return phase


def _build_insights_payload(project_id, version_row):
    if version_row is None:
        return {
            "generated_at": "",
            "competitors": [],
            "competitor_landscape_markdown": "",
            "phases": {key: _empty_phase_payload(key) for key in PHASE_DEFINITIONS},
        }

    tasks_by_phase = {
        t["phase_key"]: t["id"]
        for t in research_tasks_repo.list_tasks_for_project(project_id)
        if t["phase_key"] is not None
    }
    phases = {}
    for phase_key in PHASE_DEFINITIONS:
        task_id = tasks_by_phase.get(phase_key)
        if task_id is None:
            phases[phase_key] = _empty_phase_payload(phase_key)
        else:
            phases[phase_key] = _build_phase_data(version_row["id"], phase_key, task_id, project_id)

    return {
        "generated_at": version_row["generated_at"],
        "competitors": json.loads(version_row["competitors_json"] or "[]"),
        "competitor_landscape_markdown": version_row["competitor_landscape_markdown"] or "",
        "phases": phases,
    }


def load_current_insights(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    latest = research_tasks_repo.get_latest_version(project_id)
    return _build_insights_payload(project_id, latest)


def load_insights_history(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    versions = research_tasks_repo.list_versions_for_project(project_id)
    return [
        {
            "version": v["version"],
            "generated_at": v["generated_at"],
            "data": _build_insights_payload(project_id, v),
        }
        for v in versions
    ]


def save_excluded_competitors(project_name, names):
    project_id = projects_repo.get_or_create_id(project_name)
    existing = set(excluded_competitors_repo.list_for_project(project_id))
    target = set(names or [])
    for name in target - existing:
        excluded_competitors_repo.add(project_id, name)
    for name in existing - target:
        excluded_competitors_repo.remove(project_id, name)


def load_excluded_competitors(project_name):
    project_id = projects_repo.get_or_create_id(project_name)
    return excluded_competitors_repo.list_for_project(project_id)
