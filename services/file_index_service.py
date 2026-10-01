import secrets
from datetime import datetime

from .project_service import get_project_dir, load_project_config, save_project_config
from .storage import read_json, write_json

HIDDEN_SOURCE_FILES = {"insights.json", "insights_history.json", "excluded_competitors.json"}


def _is_internal_temp_file(filename):
    return isinstance(filename, str) and filename.startswith(".write_") and filename.endswith(".tmp")


def _is_hidden_source_file(filename):
    return isinstance(filename, str) and filename in HIDDEN_SOURCE_FILES


def _index_path(project_name):
    project_dir = get_project_dir(project_name)
    if project_dir is None:
        return None
    return project_dir / "file_index.json"


def _new_file_id():
    return f"f_{secrets.token_hex(8)}"


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
            entries[file_id] = {
                "filename": rec,
                "created_at": None,
                "updated_at": None,
            }
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


def _save_index_entries(project_name, entries):
    path = _index_path(project_name)
    if path is None:
        return
    write_json(
        path,
        {
            "version": 1,
            "files": entries,
        },
    )


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


def reconcile_file_index(project_name):
    if not project_name:
        return {}

    path = _index_path(project_name)
    if path is None:
        return {}
    entries = _normalize_index_data(read_json(path, {}))
    existing = _existing_files(project_name)

    changed = False

    # Drop index rows for deleted files.
    to_delete = [file_id for file_id, rec in entries.items() if rec["filename"] not in existing]
    for file_id in to_delete:
        del entries[file_id]
        changed = True

    # Add index rows for new files.
    indexed_names = {rec["filename"] for rec in entries.values()}
    now = datetime.now().isoformat()
    for filename in sorted(existing):
        if filename in indexed_names:
            continue
        file_id = _new_file_id()
        while file_id in entries:
            file_id = _new_file_id()
        entries[file_id] = {
            "filename": filename,
            "created_at": now,
            "updated_at": now,
        }
        changed = True

    if changed:
        _save_index_entries(project_name, entries)

    return entries


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
    file_id = get_file_id(project_name, filename, entries)
    if file_id:
        return file_id

    now = datetime.now().isoformat()
    file_id = _new_file_id()
    while file_id in entries:
        file_id = _new_file_id()
    entries[file_id] = {
        "filename": filename,
        "created_at": now,
        "updated_at": now,
    }
    _save_index_entries(project_name, entries)
    return file_id


def rename_file_in_index(project_name, old_name, new_name):
    if not old_name or not new_name or old_name == new_name:
        return False

    entries = reconcile_file_index(project_name)
    target_id = get_file_id(project_name, old_name, entries)
    if not target_id:
        # If old name wasn't indexed, make sure new name gets an ID.
        ensure_file_id(project_name, new_name)
        return False

    entries[target_id]["filename"] = new_name
    entries[target_id]["updated_at"] = datetime.now().isoformat()
    _save_index_entries(project_name, entries)
    return True


def remove_file_from_index(project_name, filename):
    if not filename:
        return None
    entries = reconcile_file_index(project_name)
    target_id = get_file_id(project_name, filename, entries)
    if not target_id:
        return None
    del entries[target_id]
    _save_index_entries(project_name, entries)
    return target_id


def resolve_file_refs_to_names(project_name, refs, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    id_to_name = _id_to_name(idx)
    valid_names = {name for name in id_to_name.values() if not _is_hidden_source_file(name)}

    out = []
    seen = set()
    for ref in refs or []:
        if not isinstance(ref, str):
            continue
        filename = None
        if ref in id_to_name:
            filename = id_to_name[ref]
        elif ref in valid_names:
            filename = ref
        if filename and not _is_hidden_source_file(filename) and filename not in seen:
            seen.add(filename)
            out.append(filename)
    return out


def resolve_ids_to_names(project_name, file_ids, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    id_to_name = _id_to_name(idx)
    out = []
    seen = set()
    for file_id in file_ids or []:
        if not isinstance(file_id, str):
            continue
        filename = id_to_name.get(file_id)
        if filename and not _is_hidden_source_file(filename) and filename not in seen:
            seen.add(filename)
            out.append(filename)
    return out


def resolve_names_to_ids(project_name, filenames, entries=None):
    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    name_to_id = _name_to_id(idx)
    out = []
    seen = set()
    for filename in filenames or []:
        if not isinstance(filename, str):
            continue
        if _is_hidden_source_file(filename):
            continue
        file_id = name_to_id.get(filename)
        if file_id and file_id not in seen:
            seen.add(file_id)
            out.append(file_id)
    return out


def _build_selected_state(project_name, entries, config):
    valid_ids = set(entries.keys())
    id_to_name = _id_to_name(entries)
    selected_ids = _dedupe(config.get("selected_file_ids", []))
    if not selected_ids:
        selected_ids = resolve_names_to_ids(project_name, config.get("selected_files", []), entries)
    selected_ids = [file_id for file_id in selected_ids if file_id in valid_ids]
    selected_ids = [file_id for file_id in selected_ids if not _is_hidden_source_file(id_to_name.get(file_id))]
    selected_files = [id_to_name[file_id] for file_id in selected_ids if file_id in id_to_name]
    return selected_ids, selected_files


def reconcile_selected_file_ids(project_name, entries=None):
    if not project_name:
        return {
            "selected_file_ids": [],
            "selected_files": [],
            "file_index": {},
        }

    idx = entries if isinstance(entries, dict) else reconcile_file_index(project_name)
    config = load_project_config(project_name) or {}
    selected_ids, selected_files = _build_selected_state(project_name, idx, config)

    changed = (
        config.get("selected_file_ids", []) != selected_ids
        or config.get("selected_files", []) != selected_files
    )
    if changed:
        config["selected_file_ids"] = selected_ids
        config["selected_files"] = selected_files
        save_project_config(project_name, config)

    return {
        "selected_file_ids": selected_ids,
        "selected_files": selected_files,
        "file_index": _id_to_name(idx),
    }


def toggle_selected_file(project_name, filename=None, file_id=None):
    if not project_name:
        return None

    entries = reconcile_file_index(project_name)
    if file_id and isinstance(file_id, str):
        target_id = file_id if file_id in entries else None
    elif filename and isinstance(filename, str):
        target_id = get_file_id(project_name, filename, entries)
    else:
        target_id = None

    if not target_id:
        return None

    config = load_project_config(project_name) or {}
    selected_ids, _ = _build_selected_state(project_name, entries, config)

    if target_id in selected_ids:
        selected_ids = [f for f in selected_ids if f != target_id]
    else:
        selected_ids.append(target_id)

    selected_files = resolve_ids_to_names(project_name, selected_ids, entries)
    config["selected_file_ids"] = selected_ids
    config["selected_files"] = selected_files
    save_project_config(project_name, config)

    return target_id in selected_ids
