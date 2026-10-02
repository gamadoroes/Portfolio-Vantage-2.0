from unittest.mock import patch

from db.repositories import projects_repo, sources_repo
from services.file_index_service import (
    _dedupe,
    _is_hidden_source_file,
    _is_internal_temp_file,
    _normalize_index_data,
    ensure_file_id,
    get_file_id,
    reconcile_file_index,
    remove_file_from_index,
    rename_file_in_index,
    resolve_file_refs_to_names,
    toggle_selected_file,
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

def _setup_project_dir(tmp_path, project_name, files=None):
    project_dir = tmp_path / "projects" / project_name
    files_dir = project_dir / "files"
    files_dir.mkdir(parents=True)
    for filename in (files or []):
        (files_dir / filename).write_text("content", encoding="utf-8")
    return project_dir


@patch("services.file_index_service.get_project_dir")
def test_reconcile_registers_new_files_as_sources(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["a.txt", "b.txt"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    filenames = {rec["filename"] for rec in entries.values()}
    assert filenames == {"a.txt", "b.txt"}
    assert all(fid.startswith("f_") for fid in entries)


@patch("services.file_index_service.get_project_dir")
def test_reconcile_is_stable_across_calls(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["a.txt"])
    mock_dir.return_value = project_dir

    first = reconcile_file_index("proj")
    second = reconcile_file_index("proj")
    assert first == second


@patch("services.file_index_service.get_project_dir")
def test_reconcile_excludes_temp_files(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["real.txt"])
    (project_dir / "files" / ".write_abc.tmp").write_text("temp", encoding="utf-8")
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    filenames = {rec["filename"] for rec in entries.values()}
    assert ".write_abc.tmp" not in filenames


@patch("services.file_index_service.get_project_dir")
def test_get_file_id_returns_correct_id(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["doc.pdf"])
    mock_dir.return_value = project_dir

    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "doc.pdf", entries)
    assert file_id in entries


@patch("services.file_index_service.get_project_dir")
def test_rename_preserves_id(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["old.txt"])
    mock_dir.return_value = project_dir
    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "old.txt", entries)

    (project_dir / "files" / "old.txt").rename(project_dir / "files" / "new.txt")
    rename_file_in_index("proj", "old.txt", "new.txt")

    entries_after = reconcile_file_index("proj")
    assert entries_after[file_id]["filename"] == "new.txt"


@patch("services.file_index_service.get_project_dir")
def test_toggle_selected_file(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["a.txt"])
    mock_dir.return_value = project_dir
    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "a.txt", entries)

    assert toggle_selected_file("proj", filename="a.txt") is True
    state = reconcile_selected_file_ids_for_test("proj")
    assert file_id in state
    assert toggle_selected_file("proj", filename="a.txt") is False


def reconcile_selected_file_ids_for_test(project_name):
    from services.file_index_service import reconcile_selected_file_ids
    return reconcile_selected_file_ids(project_name)["selected_file_ids"]


@patch("services.file_index_service.get_project_dir")
def test_resolve_file_refs_to_names_excludes_hidden(mock_dir, temp_db, tmp_path):
    project_dir = _setup_project_dir(tmp_path, "proj", files=["a.txt"])
    mock_dir.return_value = project_dir
    entries = reconcile_file_index("proj")
    file_id = get_file_id("proj", "a.txt", entries)

    names = resolve_file_refs_to_names("proj", [file_id, "insights.json"])
    assert names == ["a.txt"]
