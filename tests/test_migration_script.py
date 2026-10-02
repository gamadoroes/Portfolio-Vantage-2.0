# tests/test_migration_script.py
import json
import shutil
from pathlib import Path

from db.migrate import migrate_all_projects
from db.repositories import (
    artefacts_repo,
    excluded_competitors_repo,
    projects_repo,
    research_runs_repo,
    research_tasks_repo,
    sources_repo,
)


REAL_SAMPLE_PROJECT = Path(__file__).parent.parent / "projects" / "M Proj Mgmt"


def test_migrates_real_sample_project_artefacts_and_runs(temp_db, tmp_path):
    if not REAL_SAMPLE_PROJECT.exists():
        import pytest
        pytest.skip("Real sample project not present in this environment")

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    shutil.copytree(REAL_SAMPLE_PROJECT, projects_root / "M Proj Mgmt")

    with open(projects_root / "M Proj Mgmt" / "artifacts.json", encoding="utf-8") as f:
        original_artifacts = json.load(f)
    with open(projects_root / "M Proj Mgmt" / "research_runs.json", encoding="utf-8") as f:
        original_runs = json.load(f)

    report = migrate_all_projects(str(projects_root))

    pid = projects_repo.get_id("M Proj Mgmt")
    assert pid is not None

    migrated_artefacts = artefacts_repo.list_for_project(pid)
    assert len(migrated_artefacts) == len(original_artifacts)
    for art_id, original in original_artifacts.items():
        migrated = artefacts_repo.get(art_id)
        assert migrated is not None
        assert migrated["name"] == original["name"]

    migrated_runs = research_runs_repo.list_for_project(pid)
    assert len(migrated_runs) == len(original_runs)
    for run_id, original in original_runs.items():
        migrated = research_runs_repo.get(run_id)
        assert migrated is not None
        assert migrated["status"] == original["status"]
        assert migrated["artefact_id"] == original.get("artifact_id")

    assert report["M Proj Mgmt"]["artefacts"] == len(original_artifacts)
    assert report["M Proj Mgmt"]["research_runs"] == len(original_runs)


def test_migrates_project_with_no_insights_json_cleanly(temp_db, tmp_path):
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Bare Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Bare Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")
    (project_dir / "file_index.json").write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")

    report = migrate_all_projects(str(projects_root))

    pid = projects_repo.get_id("Bare Project")
    assert pid is not None
    assert research_tasks_repo.get_latest_version(pid) is None
    assert report["Bare Project"]["research_tasks"] == 0


def test_migration_is_idempotent(temp_db, tmp_path):
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Bare Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Bare Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text(
        json.dumps({
            "chat_1": {"name": "Chat 1", "created": "2026-01-01T00:00:00",
                        "messages": [{"role": "user", "content": "hello"},
                                     {"role": "assistant", "content": "hi there"}]}
        }),
        encoding="utf-8",
    )
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")
    (project_dir / "file_index.json").write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")

    migrate_all_projects(str(projects_root))
    migrate_all_projects(str(projects_root))  # must not raise, duplicate projects, or duplicate messages

    assert len(projects_repo.list_all()) == 1
    from db.repositories import chat_repo
    assert len(chat_repo.list_messages("chat_1")) == 2  # not 4 -- this is what the re-run bug would produce


def test_migrates_excluded_competitors_and_selected_files(temp_db, tmp_path):
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "P"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "files" / "doc.txt").write_text("content", encoding="utf-8")
    (project_dir / "outputs").mkdir()
    (project_dir / "file_index.json").write_text(
        json.dumps({"version": 1, "files": {"f_abc": {"filename": "doc.txt",
                     "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"}}}),
        encoding="utf-8",
    )
    (project_dir / "config.json").write_text(
        json.dumps({"name": "P", "created": "2026-01-01T00:00:00",
                     "selected_files": ["doc.txt"], "selected_file_ids": ["f_abc"]}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")
    (project_dir / "excluded_competitors.json").write_text(
        json.dumps(["Competitor X"]), encoding="utf-8"
    )

    migrate_all_projects(str(projects_root))

    pid = projects_repo.get_id("P")
    assert set(excluded_competitors_repo.list_for_project(pid)) == {"Competitor X"}
    source = sources_repo.get_by_stable_id(pid, "f_abc")
    assert sources_repo.list_selected_ids(pid) == [source["id"]]
