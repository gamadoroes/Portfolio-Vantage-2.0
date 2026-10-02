import secrets

from db.repositories import projects_repo, sources_repo

from .project_service import get_project_dir

HIDDEN_SOURCE_FILES = {"insights.json", "insights_history.json", "excluded_competitors.json"}


def _is_internal_temp_file(filename):
    return isinstance(filename, str) and filename.startswith(".write_") and filename.endswith(".tmp")


def _is_hidden_source_file(filename):
    return isinstance(filename, str) and filename in HIDDEN_SOURCE_FILES


def _dedupe(items):
    out = []
    seen = set()
    for item in items or []:
        if not isinstance(item, str):
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _normalize_index_data(raw):
    # Retained for any remaining callers that pass raw legacy JSON through;
    # reconcile_file_index itself no longer reads this shape from disk.
    if not isinstance(raw, dict):
        return {}
    files = raw.get("files")
    if not isinstance(files, dict):
        return {}
    entries = {}
    for file_id, rec in files.items():
        if not isinstance(file_id, str) or not file_id:
            continue
        if isinstance(rec, str):
            entries[file_id] = {"filename": rec, "created_at": None, "updated_at": None}
            continue
        if not isinstance(rec, dict):
            continue
        filename = rec.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        entries[file_id] = {
            "filename": filename,
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
        }
    return entries


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
        if p.is_file() and not _is_internal_temp_file(p.name)
    }


def _entries_for_project(project_id):
    return {
        s["stable_file_id"]: {
            "filename": s["filename"], "created_at": s["created_at"], "updated_at": s["updated_at"]
        }
        for s in sources_repo.list_for_project(project_id)
    }


def reconcile_file_index(project_name):
    if not project_name:
        return {}
    project_id = projects_repo.get_or_create_id(project_name)
    existing = _existing_files(project_name)
    entries = _entries_for_project(project_id)

    indexed_names = {rec["filename"] for rec in entries.values()}
    for filename in sorted(existing):
        if filename in indexed_names:
            continue
        stable_file_id = f"f_{secrets.token_hex(8)}"
        sources_repo.upsert(project_id, stable_file_id, filename)

    return _entries_for_project(project_id)


def _name_to_id(entries):
    return {rec["filename"]: file_id for file_id, rec in entries.items()}


def _id_to_name(entries):
    return {file_id: rec["filename"] for file_id, rec in entries.items()}


def get_file_id(project_name, filename, entries=None):
    if not filename:
        return None
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    return _name_to_id(idx).get(filename)


def ensure_file_id(project_name, filename):
    if not filename:
        return None
    entries = reconcile_file_index(project_name)
    return get_file_id(project_name, filename, entries)


def rename_file_in_index(project_name, old_name, new_name):
    if not old_name or not new_name or old_name == new_name:
        return False
    project_id = projects_repo.get_or_create_id(project_name)
    entries = reconcile_file_index(project_name)
    target_id = get_file_id(project_name, old_name, entries)
    if not target_id:
        ensure_file_id(project_name, new_name)
        return False
    source = sources_repo.get_by_stable_id(project_id, target_id)
    sources_repo.rename(source["id"], new_name)
    return True


def remove_file_from_index(project_name, filename):
    if not filename:
        return None
    project_id = projects_repo.get_or_create_id(project_name)
    entries = reconcile_file_index(project_name)
    target_id = get_file_id(project_name, filename, entries)
    if not target_id:
        return None
    source = sources_repo.get_by_stable_id(project_id, target_id)
    sources_repo.delete(source["id"])
    return target_id


def resolve_file_refs_to_names(project_name, refs, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    id_to_name = _id_to_name(idx)
    valid_names = {name for name in id_to_name.values() if not _is_hidden_source_file(name)}
    out, seen = [], set()
    for ref in refs or []:
        if not isinstance(ref, str):
            continue
        filename = id_to_name.get(ref) if ref in id_to_name else (ref if ref in valid_names else None)
        if filename and not _is_hidden_source_file(filename) and filename not in seen:
            seen.add(filename)
            out.append(filename)
    return out


def resolve_ids_to_names(project_name, file_ids, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    id_to_name = _id_to_name(idx)
    out, seen = [], set()
    for file_id in file_ids or []:
        filename = id_to_name.get(file_id) if isinstance(file_id, str) else None
        if filename and not _is_hidden_source_file(filename) and filename not in seen:
            seen.add(filename)
            out.append(filename)
    return out


def resolve_names_to_ids(project_name, filenames, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    name_to_id = _name_to_id(idx)
    out, seen = [], set()
    for filename in filenames or []:
        if not isinstance(filename, str) or _is_hidden_source_file(filename):
            continue
        file_id = name_to_id.get(filename)
        if file_id and file_id not in seen:
            seen.add(file_id)
            out.append(file_id)
    return out


def reconcile_selected_file_ids(project_name, entries=None):
    if not project_name:
        return {"selected_file_ids": [], "selected_files": [], "file_index": {}}
    project_id = projects_repo.get_or_create_id(project_name)
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    id_to_name = _id_to_name(idx)
    sources_by_id = {s["id"]: s for s in sources_repo.list_for_project(project_id)}
    selected_ids = [
        sources_by_id[sid]["stable_file_id"]
        for sid in sources_repo.list_selected_ids(project_id)
        if sid in sources_by_id and not _is_hidden_source_file(sources_by_id[sid]["filename"])
    ]
    selected_files = [id_to_name[fid] for fid in selected_ids if fid in id_to_name]
    return {"selected_file_ids": selected_ids, "selected_files": selected_files, "file_index": id_to_name}


def toggle_selected_file(project_name, filename=None, file_id=None):
    if not project_name:
        return None
    project_id = projects_repo.get_or_create_id(project_name)
    entries = reconcile_file_index(project_name)
    if file_id and file_id in entries:
        target_stable_id = file_id
    elif filename:
        target_stable_id = get_file_id(project_name, filename, entries)
    else:
        target_stable_id = None
    if not target_stable_id:
        return None

    source = sources_repo.get_by_stable_id(project_id, target_stable_id)
    currently_selected = source["id"] in sources_repo.list_selected_ids(project_id)
    sources_repo.set_selected(project_id, source["id"], not currently_selected)
    return not currently_selected
