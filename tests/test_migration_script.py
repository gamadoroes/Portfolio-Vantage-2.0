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


def test_migrates_research_run_with_dangling_chat_id_without_crashing(temp_db, tmp_path):
    # Reproduces real production data drift: a research run references a
    # chat_id that no longer exists in chat_history.json (the chat session
    # was deleted via the UI at some point, but the old JSON storage never
    # enforced referential integrity between the two files). The new
    # research_runs.chat_session_id FK must not crash the whole migration
    # over this -- it should migrate the run with chat_session_id = None.
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Dangling Chat Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Dangling Chat Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    # chat_history.json has zero sessions -- the referenced chat_id is absent
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text(
        json.dumps({
            "run_1_dangling": {
                "response_id": "resp_123",
                "chat_id": "chat_does_not_exist",
                "prompt_preview": "some prompt",
                "status": "completed",
                "artifact_id": None,
                "error": None,
                "completed_at": "2026-01-01T00:00:00",
            }
        }),
        encoding="utf-8",
    )
    (project_dir / "file_index.json").write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")

    report = migrate_all_projects(str(projects_root))  # must not raise FOREIGN KEY constraint failed

    pid = projects_repo.get_id("Dangling Chat Project")
    assert pid is not None
    migrated_run = research_runs_repo.get("run_1_dangling")
    assert migrated_run is not None
    assert migrated_run["chat_session_id"] is None
    assert migrated_run["response_id"] == "resp_123"
    assert migrated_run["status"] == "completed"
    assert report["Dangling Chat Project"]["research_runs"] == 1


def test_migrates_research_run_with_dangling_artifact_id_without_crashing(temp_db, tmp_path):
    # Same drift pattern as the dangling chat_id case, but for
    # research_runs.artifact_id -> artefacts.id. If artifacts.json doesn't
    # contain the artifact a run claims to have produced (old JSON storage
    # never enforced this either), the FK UPDATE must not crash the whole
    # migration -- the run should migrate with artefact_id = None.
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Dangling Artifact Run Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Dangling Artifact Run Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    # artifacts.json has zero entries -- the referenced artifact_id is absent
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text(
        json.dumps({
            "run_2_dangling": {
                "response_id": "resp_456",
                "chat_id": None,
                "prompt_preview": "another prompt",
                "status": "completed",
                "artifact_id": "art_does_not_exist",
                "error": None,
                "completed_at": "2026-01-01T00:00:00",
            }
        }),
        encoding="utf-8",
    )
    (project_dir / "file_index.json").write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")

    report = migrate_all_projects(str(projects_root))  # must not raise FOREIGN KEY constraint failed

    pid = projects_repo.get_id("Dangling Artifact Run Project")
    assert pid is not None
    migrated_run = research_runs_repo.get("run_2_dangling")
    assert migrated_run is not None
    assert migrated_run["artefact_id"] is None
    assert migrated_run["response_id"] == "resp_456"
    assert migrated_run["status"] == "completed"
    assert report["Dangling Artifact Run Project"]["research_runs"] == 1


def test_migrates_insights_history_linked_file_survives_later_source_deletion(temp_db, tmp_path):
    # Regression test for I1 at the migration-script level: a historical
    # insights_history.json entry linking a file must still show that file
    # after the source is later deleted, same as the live-app regression
    # test in test_insights_service.py.
    from services import insights_service

    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Linked File Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "files" / "report.pdf").write_text("content", encoding="utf-8")
    (project_dir / "outputs").mkdir()
    (project_dir / "file_index.json").write_text(
        json.dumps({"version": 1, "files": {"f_abc": {"filename": "report.pdf",
                     "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"}}}),
        encoding="utf-8",
    )
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Linked File Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")

    phases = {str(i): {"title": f"Phase {i}", "summary": "MISSING", "confidence": "none",
                        "evidence_sources": [], "gaps": [], "suggested_topics": [],
                        "linked_files": [], "linked_file_ids": []} for i in range(1, 8)}
    phases["1"] = {**phases["1"], "summary": "Version 1 summary", "confidence": "medium",
                   "linked_files": ["report.pdf"], "linked_file_ids": ["f_abc"]}
    (project_dir / "insights_history.json").write_text(
        json.dumps([{"version": 1, "data": {
            "generated_at": "2026-01-01T00:00:00", "competitors": [],
            "competitor_landscape_markdown": "", "phases": phases,
        }}]),
        encoding="utf-8",
    )

    migrate_all_projects(str(projects_root))

    pid = projects_repo.get_id("Linked File Project")
    source = sources_repo.get_by_stable_id(pid, "f_abc")
    sources_repo.delete(source["id"])

    history = insights_service.load_insights_history("Linked File Project")
    v1 = next(h for h in history if h["version"] == 1)
    assert v1["data"]["phases"]["1"]["linked_files"] == ["report.pdf"]
    assert v1["data"]["phases"]["1"]["linked_file_ids"] == ["f_abc"]


def test_migrates_insights_history_linked_file_already_deleted_before_migration(temp_db, tmp_path):
    # The file a historical version linked may already be gone by the time the
    # one-shot migration runs (deleted before migration, not after). file_index.json
    # won't have it, so stable_id_to_source_id has no entry -- the migration must
    # still preserve the filename from insights_history.json's own linked_files
    # array rather than silently dropping the link.
    from services import insights_service

    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Already Deleted Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    # file_index.json has NO entry for f_abc -- the file was deleted pre-migration
    (project_dir / "file_index.json").write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Already Deleted Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")

    phases = {str(i): {"title": f"Phase {i}", "summary": "MISSING", "confidence": "none",
                        "evidence_sources": [], "gaps": [], "suggested_topics": [],
                        "linked_files": [], "linked_file_ids": []} for i in range(1, 8)}
    phases["1"] = {**phases["1"], "summary": "Version 1 summary", "confidence": "medium",
                   "linked_files": ["report.pdf"], "linked_file_ids": ["f_abc"]}
    (project_dir / "insights_history.json").write_text(
        json.dumps([{"version": 1, "data": {
            "generated_at": "2026-01-01T00:00:00", "competitors": [],
            "competitor_landscape_markdown": "", "phases": phases,
        }}]),
        encoding="utf-8",
    )

    migrate_all_projects(str(projects_root))

    history = insights_service.load_insights_history("Already Deleted Project")
    v1 = next(h for h in history if h["version"] == 1)
    assert v1["data"]["phases"]["1"]["linked_files"] == ["report.pdf"]
    assert v1["data"]["phases"]["1"]["linked_file_ids"] == ["f_abc"]


def test_migrates_insights_history_linked_file_uses_historical_name_not_renamed_name(temp_db, tmp_path):
    # A file may be renamed between when a historical version was saved and when
    # the one-shot migration runs. insights_history.json's linked_files holds the
    # name as it was AT SAVE TIME -- the migration must use that, not the file's
    # current (renamed) name from file_index.json/sources, or it silently rewrites
    # history.
    from services import insights_service

    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Renamed File Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "files" / "report_renamed.pdf").write_text("content", encoding="utf-8")
    (project_dir / "outputs").mkdir()
    # file_index.json reflects the CURRENT (renamed) filename
    (project_dir / "file_index.json").write_text(
        json.dumps({"version": 1, "files": {"f_abc": {"filename": "report_renamed.pdf",
                     "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-02T00:00:00"}}}),
        encoding="utf-8",
    )
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Renamed File Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text("{}", encoding="utf-8")
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")

    phases = {str(i): {"title": f"Phase {i}", "summary": "MISSING", "confidence": "none",
                        "evidence_sources": [], "gaps": [], "suggested_topics": [],
                        "linked_files": [], "linked_file_ids": []} for i in range(1, 8)}
    # At save time, the file was still named "report.pdf"
    phases["1"] = {**phases["1"], "summary": "Version 1 summary", "confidence": "medium",
                   "linked_files": ["report.pdf"], "linked_file_ids": ["f_abc"]}
    (project_dir / "insights_history.json").write_text(
        json.dumps([{"version": 1, "data": {
            "generated_at": "2026-01-01T00:00:00", "competitors": [],
            "competitor_landscape_markdown": "", "phases": phases,
        }}]),
        encoding="utf-8",
    )

    migrate_all_projects(str(projects_root))

    history = insights_service.load_insights_history("Renamed File Project")
    v1 = next(h for h in history if h["version"] == 1)
    assert v1["data"]["phases"]["1"]["linked_files"] == ["report.pdf"]


def test_migrates_chat_message_with_dangling_artifact_id_without_crashing(temp_db, tmp_path):
    # Same drift pattern again, this time for
    # project_messages.artefact_id -> artefacts.id. A chat message that
    # claims to be linked to an artifact that doesn't exist in
    # artifacts.json must not crash the INSERT -- the message should
    # migrate with artefact_id = None.
    from db.repositories import chat_repo

    projects_root = tmp_path / "projects"
    project_dir = projects_root / "Dangling Artifact Message Project"
    (project_dir / "files").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    (project_dir / "config.json").write_text(
        json.dumps({"name": "Dangling Artifact Message Project", "created": "2026-01-01T00:00:00",
                     "selected_files": [], "selected_file_ids": []}),
        encoding="utf-8",
    )
    (project_dir / "metadata.json").write_text(
        json.dumps({"description": "", "archived": False}), encoding="utf-8"
    )
    # artifacts.json has zero entries -- the referenced artifact_id is absent
    (project_dir / "artifacts.json").write_text("{}", encoding="utf-8")
    (project_dir / "chat_history.json").write_text(
        json.dumps({
            "chat_dangling": {
                "name": "Chat With Dangling Artifact",
                "created": "2026-01-01T00:00:00",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "here's your doc",
                     "artifact_id": "art_does_not_exist"},
                ],
            }
        }),
        encoding="utf-8",
    )
    (project_dir / "research_runs.json").write_text("{}", encoding="utf-8")
    (project_dir / "file_index.json").write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")

    migrate_all_projects(str(projects_root))  # must not raise FOREIGN KEY constraint failed

    pid = projects_repo.get_id("Dangling Artifact Message Project")
    assert pid is not None
    messages = chat_repo.list_messages("chat_dangling")
    assert len(messages) == 2
    assert messages[1]["artefact_id"] is None
    assert messages[1]["content"] == "here's your doc"
