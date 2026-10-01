import json
from unittest.mock import patch

from services.file_index_service import (
    _dedupe,
    _is_hidden_source_file,
    _is_internal_temp_file,
    _normalize_index_data,
    get_file_id,
    reconcile_file_index,
    remove_file_from_index,
    rename_file_in_index,
    resolve_file_refs_to_names,
)

# --- Unit tests for pure helpers ---

def test_is_internal_temp_file():
    assert _is_internal_temp_file(".write_abc123.tmp") is True
    assert _is_internal_temp_file("report.pdf") is False
    assert _is_internal_temp_file(".write_no_suffix") is False
    assert _is_internal_temp_file(123) is False


def test_is_hidden_source_file():
    assert _is_hidden_source_file("insights.json") is True
    assert _is_hidden_source_file("insights_history.json") is True
    assert _is_hidden_source_file("excluded_competitors.json") is True
    assert _is_hidden_source_file("report.pdf") is False


def test_dedupe_preserves_order():
    assert _dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_dedupe_handles_none():
    assert _dedupe(None) == []


def test_dedupe_skips_non_strings():
    assert _dedupe(["a", 1, "b", None, "a"]) == ["a", "b"]


def test_normalize_index_data_empty():
    assert _normalize_index_data({}) == {}
    assert _normalize_index_data(None) == {}
    assert _normalize_index_data("bad") == {}


def test_normalize_index_data_string_values():
    raw = {"files": {"f_abc": "report.pdf"}}
    result = _normalize_index_data(raw)
    assert result["f_abc"]["filename"] == "report.pdf"
    assert result["f_abc"]["created_at"] is None


def test_normalize_index_data_dict_values():
    raw = {
        "files": {
            "f_abc": {
                "filename": "report.pdf",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-02",
            }
        }
    }
    result = _normalize_index_data(raw)
    assert result["f_abc"]["filename"] == "report.pdf"
    assert result["f_abc"]["created_at"] == "2024-01-01"


def test_normalize_index_data_skips_invalid_entries():
    raw = {
        "files": {
            "f_good": {"filename": "ok.txt"},
            "": {"filename": "empty_id.txt"},
            "f_noname": {"filename": ""},
            "f_badtype": 42,
        }
    }
    result = _normalize_index_data(raw)
    assert "f_good" in result
    assert "" not in result
    assert "f_noname" not in result
    assert "f_badtype" not in result


# --- Integration tests with file system ---

def _setup_project(tmp_path, project_name, files=None, index_data=None):
    """Create a minimal project structure for testing."""
    project_dir = tmp_path / "projects" / project_name
    files_dir = project_dir / "files"
    files_dir.mkdir(parents=True)

    for filename in (files or []):
        (files_dir / filename).write_text("content", encoding="utf-8")

    if index_data is not None:
        (project_dir / "file_index.json").write_text(
            json.dumps(index_data), encoding="utf-8"
        )

    config = {"name": project_name, "selected_files": [], "selected_file_ids": []}
    (project_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    return project_dir


@patch("services.file_index_service.get_project_dir")
def test_reconcile_adds_new_files(mock_dir, tmp_path):
    project_dir = _setup_project(tmp_path, "proj", files=["a.txt", "b.txt"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    filenames = {rec["filename"] for rec in entries.values()}
    assert filenames == {"a.txt", "b.txt"}
    assert all(fid.startswith("f_") for fid in entries)


@patch("services.file_index_service.get_project_dir")
def test_reconcile_removes_deleted_files(mock_dir, tmp_path):
    index_data = {
        "version": 1,
        "files": {"f_old": {"filename": "gone.txt", "created_at": None, "updated_at": None}},
    }
    project_dir = _setup_project(tmp_path, "proj", files=["kept.txt"], index_data=index_data)
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    filenames = {rec["filename"] for rec in entries.values()}
    assert "gone.txt" not in filenames
    assert "kept.txt" in filenames


@patch("services.file_index_service.get_project_dir")
def test_reconcile_excludes_temp_files(mock_dir, tmp_path):
    project_dir = _setup_project(tmp_path, "proj", files=["real.txt"])
    # Manually add a temp file
    (project_dir / "files" / ".write_abc.tmp").write_text("temp", encoding="utf-8")
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    filenames = {rec["filename"] for rec in entries.values()}
    assert "real.txt" in filenames
    assert ".write_abc.tmp" not in filenames


@patch("services.file_index_service.get_project_dir")
def test_get_file_id_returns_correct_id(mock_dir, tmp_path):
    project_dir = _setup_project(tmp_path, "proj", files=["doc.pdf"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "doc.pdf", entries)
    assert file_id is not None
    assert file_id.startswith("f_")
    assert get_file_id("proj", "nonexistent.txt", entries) is None


@patch("services.file_index_service.get_project_dir")
def test_remove_file_from_index(mock_dir, tmp_path):
    project_dir = _setup_project(tmp_path, "proj", files=["a.txt", "b.txt"])
    mock_dir.return_value = project_dir

    reconcile_file_index("proj")
    removed_id = remove_file_from_index("proj", "a.txt")
    assert removed_id is not None

    entries = reconcile_file_index("proj")
    # a.txt still exists on disk, so reconcile will re-add it with a new ID
    filenames = {rec["filename"] for rec in entries.values()}
    assert "a.txt" in filenames


@patch("services.file_index_service.get_project_dir")
def test_rename_file_in_index(mock_dir, tmp_path):
    """rename_file_in_index should be called BEFORE the file is renamed on disk,
    since reconcile_file_index (called internally) would drop the old entry if
    the old file no longer exists."""
    project_dir = _setup_project(tmp_path, "proj", files=["old.txt"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    old_id = get_file_id("proj", "old.txt", entries)

    # Call rename_file_in_index while old.txt still exists on disk
    result = rename_file_in_index("proj", "old.txt", "new.txt")
    assert result is True

    # Now rename the actual file
    (project_dir / "files" / "old.txt").rename(project_dir / "files" / "new.txt")

    entries = reconcile_file_index("proj")
    assert get_file_id("proj", "new.txt", entries) == old_id


@patch("services.file_index_service.get_project_dir")
def test_resolve_file_refs_handles_ids_and_names(mock_dir, tmp_path):
    project_dir = _setup_project(tmp_path, "proj", files=["a.txt", "b.txt"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    a_id = get_file_id("proj", "a.txt", entries)

    # Mix of IDs and bare filenames
    result = resolve_file_refs_to_names("proj", [a_id, "b.txt"], entries)
    assert set(result) == {"a.txt", "b.txt"}


@patch("services.file_index_service.get_project_dir")
def test_resolve_excludes_hidden_files(mock_dir, tmp_path):
    project_dir = _setup_project(tmp_path, "proj", files=["insights.json", "report.txt"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    result = resolve_file_refs_to_names("proj", ["insights.json", "report.txt"], entries)
    assert result == ["report.txt"]
