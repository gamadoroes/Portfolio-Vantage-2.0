import json
from unittest.mock import patch

from services.reference_integrity_service import (
    _dedupe_preserve,
    reconcile_project_references,
    remove_file_references,
    replace_file_references,
)


def _setup_project(tmp_path, project_name, files=None, config=None, insights=None):
    """Create a minimal project structure for testing."""
    project_dir = tmp_path / "projects" / project_name
    files_dir = project_dir / "files"
    files_dir.mkdir(parents=True)

    for filename in (files or []):
        (files_dir / filename).write_text("content", encoding="utf-8")

    cfg = config or {"name": project_name, "selected_files": [], "selected_file_ids": []}
    (project_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    if insights is not None:
        (files_dir / "insights.json").write_text(json.dumps(insights), encoding="utf-8")

    return project_dir


# --- Pure helper tests ---

def test_dedupe_preserve_order():
    assert _dedupe_preserve(["c", "a", "c", "b", "a"]) == ["c", "a", "b"]


def test_dedupe_preserve_handles_none():
    assert _dedupe_preserve(None) == []


def test_dedupe_preserve_skips_non_strings():
    assert _dedupe_preserve(["x", 1, None, "y"]) == ["x", "y"]


# --- Integration tests ---

def _mock_both(mock_get_dir, mock_reconcile, project_dir, files):
    mock_get_dir.return_value = project_dir
    # Return a simple index: each file gets a predictable ID
    entries = {}
    for i, f in enumerate(files):
        fid = f"f_{i:016x}"
        entries[fid] = {"filename": f, "created_at": None, "updated_at": None}
    mock_reconcile.return_value = entries
    return entries


@patch("services.reference_integrity_service.save_project_config")
@patch("services.reference_integrity_service.load_project_config")
@patch("services.reference_integrity_service.reconcile_file_index")
@patch("services.reference_integrity_service.get_project_dir")
def test_reconcile_removes_stale_selected_files(mock_dir, mock_reconcile, mock_load, mock_save, tmp_path):
    config = {
        "name": "proj",
        "selected_files": ["exists.txt", "deleted.txt"],
        "selected_file_ids": [],
    }
    project_dir = _setup_project(tmp_path, "proj", files=["exists.txt"], config=config)
    _mock_both(mock_dir, mock_reconcile, project_dir, ["exists.txt"])
    mock_load.return_value = config

    result = reconcile_project_references("proj")
    assert result["changed"] is True
    assert result["selected_files"] == ["exists.txt"]


@patch("services.reference_integrity_service.reconcile_file_index")
@patch("services.reference_integrity_service.get_project_dir")
def test_reconcile_cleans_phase_links(mock_dir, mock_reconcile, temp_db, tmp_path):
    insights = {
        "phases": {
            "Landscape": {
                "linked_files": ["exists.txt", "deleted.txt"],
                "linked_file_ids": [],
            }
        }
    }
    project_dir = _setup_project(
        tmp_path, "proj", files=["exists.txt"], insights=insights
    )
    _mock_both(mock_dir, mock_reconcile, project_dir, ["exists.txt"])

    result = reconcile_project_references("proj")
    assert result["changed"] is True

    # Verify insights.json was updated
    updated = json.loads(
        (project_dir / "files" / "insights.json").read_text(encoding="utf-8")
    )
    assert updated["phases"]["Landscape"]["linked_files"] == ["exists.txt"]


@patch("services.reference_integrity_service.reconcile_file_index")
@patch("services.reference_integrity_service.get_project_dir")
def test_reconcile_no_changes_when_clean(mock_dir, mock_reconcile, temp_db, tmp_path):
    config = {
        "name": "proj",
        "selected_files": ["a.txt"],
        "selected_file_ids": [],
    }
    project_dir = _setup_project(tmp_path, "proj", files=["a.txt"], config=config)
    _mock_both(mock_dir, mock_reconcile, project_dir, ["a.txt"])

    result = reconcile_project_references("proj")
    assert result["changed"] is False


@patch("services.reference_integrity_service.load_project_config")
@patch("services.reference_integrity_service.save_project_config")
@patch("services.reference_integrity_service.get_project_dir")
def test_replace_file_references_in_selected(mock_dir, mock_save, mock_load, tmp_path):
    project_dir = _setup_project(tmp_path, "proj", files=["new.txt"])
    mock_dir.return_value = project_dir
    mock_load.return_value = {
        "selected_files": ["old.txt", "other.txt"],
    }

    result = replace_file_references("proj", "old.txt", "new.txt")
    assert result["changed"] is True
    # Verify save was called with updated list
    saved_config = mock_save.call_args[0][1]
    assert saved_config["selected_files"] == ["new.txt", "other.txt"]


@patch("services.reference_integrity_service.load_project_config")
@patch("services.reference_integrity_service.save_project_config")
@patch("services.reference_integrity_service.get_project_dir")
def test_remove_file_references_from_selected(mock_dir, mock_save, mock_load, tmp_path):
    project_dir = _setup_project(tmp_path, "proj", files=["keep.txt"])
    mock_dir.return_value = project_dir
    mock_load.return_value = {
        "selected_files": ["remove_me.txt", "keep.txt"],
    }

    result = remove_file_references("proj", "remove_me.txt")
    assert result["changed"] is True
    saved_config = mock_save.call_args[0][1]
    assert saved_config["selected_files"] == ["keep.txt"]


def test_reconcile_returns_empty_for_no_project():
    result = reconcile_project_references("")
    assert result == {"changed": False, "selected_files": []}
    result = reconcile_project_references(None)
    assert result == {"changed": False, "selected_files": []}


def test_replace_noop_for_same_name():
    result = replace_file_references("proj", "same.txt", "same.txt")
    assert result == {"changed": False}


def test_remove_noop_for_no_filename():
    result = remove_file_references("proj", "")
    assert result == {"changed": False}
    result = remove_file_references("proj", None)
    assert result == {"changed": False}
