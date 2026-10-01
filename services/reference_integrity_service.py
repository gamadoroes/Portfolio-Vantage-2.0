from .project_service import get_project_dir, load_project_config, save_project_config
from .storage import read_json, write_json
from .file_index_service import reconcile_file_index

HIDDEN_SOURCE_FILES = {"insights.json", "insights_history.json", "excluded_competitors.json"}


def _dedupe_preserve(items):
    seen = set()
    out = []
    for item in items or []:
        if not isinstance(item, str):
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _existing_files(project_name):
    project_dir = get_project_dir(project_name)
    if project_dir is None:
        return set()
    files_dir = project_dir / "files"
    if not files_dir.exists():
        return set()
    return {
        p.name
        for p in files_dir.iterdir()
        if p.is_file()
        and p.name not in HIDDEN_SOURCE_FILES
        and not (p.name.startswith(".write_") and p.name.endswith(".tmp"))
    }


def _insights_path(project_name):
    project_dir = get_project_dir(project_name)
    if project_dir is None:
        return None
    return project_dir / "files" / "insights.json"


def _update_selected_files(project_name, updater):
    config = load_project_config(project_name) or {}
    selected = _dedupe_preserve(config.get("selected_files", []))
    updated = updater(selected)
    if updated != config.get("selected_files", []):
        config["selected_files"] = updated
        try:
            save_project_config(project_name, config)
            return True, updated
        except Exception:
            # Non-fatal: integrity pass should never block normal app flow.
            return False, selected
    return False, selected


def _update_phase_links(project_name, updater, id_updater=None, name_to_id=None):
    path = _insights_path(project_name)
    if path is None:
        return False
    data = read_json(path, None)
    if not isinstance(data, dict):
        return False

    phases = data.get("phases")
    if not isinstance(phases, dict):
        return False

    changed = False
    for phase in phases.values():
        if not isinstance(phase, dict):
            continue
        linked = _dedupe_preserve(phase.get("linked_files", []))
        updated = updater(linked)
        if updated != linked:
            phase["linked_files"] = updated
            changed = True
        elif phase.get("linked_files") != linked:
            phase["linked_files"] = linked
            changed = True

        linked_ids = _dedupe_preserve(phase.get("linked_file_ids", []))
        if not linked_ids and linked and isinstance(name_to_id, dict):
            linked_ids = [name_to_id[f] for f in linked if f in name_to_id]
        updated_ids = id_updater(linked_ids) if id_updater else linked_ids
        if updated_ids != linked_ids:
            phase["linked_file_ids"] = updated_ids
            changed = True
        elif phase.get("linked_file_ids") != linked_ids:
            phase["linked_file_ids"] = linked_ids
            changed = True

    if changed:
        try:
            write_json(path, data)
            return True
        except Exception:
            # Non-fatal: integrity pass should never block normal app flow.
            return False
    return False


def reconcile_project_references(project_name):
    if not project_name:
        return {"changed": False, "selected_files": []}

    existing = _existing_files(project_name)
    indexed = reconcile_file_index(project_name)
    valid_ids = {
        file_id for file_id, rec in indexed.items() if rec.get("filename") in existing
    }
    name_to_id = {
        rec["filename"]: file_id
        for file_id, rec in indexed.items()
        if rec.get("filename") in existing
    }

    changed_sel, selected = _update_selected_files(
        project_name, lambda lst: [f for f in lst if f in existing]
    )
    changed_links = _update_phase_links(
        project_name,
        lambda lst: [f for f in lst if f in existing],
        lambda lst: [file_id for file_id in lst if file_id in valid_ids],
        name_to_id,
    )

    return {"changed": changed_sel or changed_links, "selected_files": selected}


def replace_file_references(project_name, old_name, new_name):
    if not project_name or not old_name or not new_name or old_name == new_name:
        return {"changed": False}

    changed_sel, _ = _update_selected_files(
        project_name,
        lambda lst: _dedupe_preserve([new_name if f == old_name else f for f in lst]),
    )
    changed_links = _update_phase_links(
        project_name,
        lambda lst: _dedupe_preserve([new_name if f == old_name else f for f in lst]),
    )
    return {"changed": changed_sel or changed_links}


def remove_file_references(project_name, filename, file_id=None):
    if not project_name or not filename:
        return {"changed": False}

    changed_sel, _ = _update_selected_files(
        project_name, lambda lst: [f for f in lst if f != filename]
    )
    changed_links = _update_phase_links(
        project_name,
        lambda lst: [f for f in lst if f != filename],
        (
            (lambda lst: [fid for fid in lst if fid != file_id])
            if file_id
            else None
        ),
    )
    return {"changed": changed_sel or changed_links}
