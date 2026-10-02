from db.repositories import projects_repo, research_tasks_repo, sources_repo

HIDDEN_SOURCE_FILES = {"insights.json", "insights_history.json", "excluded_competitors.json"}


def reconcile_project_references(project_name):
    """File-index reconciliation already self-heals selection state on every
    reconcile_file_index() call (Task 13); nothing additional to prune here
    now that references are ID-based. Kept for call-site compatibility.
    """
    if not project_name:
        return {"changed": False, "selected_files": []}
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {"changed": False, "selected_files": []}
    sources_by_id = {s["id"]: s for s in sources_repo.list_for_project(project_id)}
    selected_files = [
        sources_by_id[sid]["filename"]
        for sid in sources_repo.list_selected_ids(project_id)
        if sid in sources_by_id
    ]
    return {"changed": False, "selected_files": selected_files}


def replace_file_references(project_name, old_name, new_name):
    """A no-op: project_selected_sources and finding_evidence both key off the
    stable source_id, not filename, so a rename (Task 13's sources_repo.rename)
    is already reflected everywhere automatically.
    """
    return {"changed": False}


def remove_file_references(project_name, filename, file_id=None):
    if not project_name or not filename:
        return {"changed": False}
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {"changed": False}

    # Prefer the stable file_id when given: sources has no uniqueness
    # constraint on (project_id, filename), only on (project_id,
    # stable_file_id), so two rows can legitimately share a filename (e.g. a
    # race between concurrent reconcile_file_index() upserts). Falling back
    # to a filename-only lookup in that case could unselect/unlink the wrong
    # row. file_id is the precise identifier callers already resolved
    # (see file_index_service.remove_file_from_index), so use it first.
    source = sources_repo.get_by_stable_id(project_id, file_id) if file_id else None
    if source is None:
        source = sources_repo.get_by_filename(project_id, filename)
    if source is None:
        return {"changed": False}

    changed = False
    if source["id"] in sources_repo.list_selected_ids(project_id):
        sources_repo.set_selected(project_id, source["id"], False)
        changed = True

    latest_version = research_tasks_repo.get_latest_version(project_id)
    if latest_version is not None:
        research_tasks_repo.unlink_evidence_by_source_in_version(latest_version["id"], source["id"])
        changed = True

    return {"changed": changed}
