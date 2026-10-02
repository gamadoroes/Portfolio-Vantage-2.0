import services.artifact_service as artifact_service
from db.repositories import artefacts_repo, projects_repo, sources_repo


def test_load_artifacts_reconstructs_filename_and_content_from_disk(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pid = projects_repo.get_or_create_id("P")
    files_dir = tmp_path / "projects" / "P" / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "My Artefact.md").write_text("artefact body text", encoding="utf-8")
    sid = sources_repo.upsert(pid, "f_abc", "My Artefact.md")
    artefacts_repo.upsert("art_1", pid, "My Artefact", source_id=sid)

    loaded = artifact_service.load_artifacts("P")
    assert loaded["art_1"]["name"] == "My Artefact"
    assert loaded["art_1"]["filename"] == "My Artefact.md"
    assert loaded["art_1"]["content"] == "artefact body text"
    assert loaded["art_1"]["type"] == "text"


def test_save_artifacts_upserts_each_entry_and_links_source(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pid = projects_repo.get_or_create_id("P")
    sources_repo.upsert(pid, "f_abc", "Doc.md")
    artifact_service.save_artifacts(
        "P", {"art_1": {"id": "art_1", "name": "First", "filename": "Doc.md"}}
    )
    row = artefacts_repo.get("art_1")
    assert row["name"] == "First"
    source = sources_repo.get_by_stable_id(pid, "f_abc")
    assert row["source_id"] == source["id"]


def test_load_artifacts_empty_for_unknown_project(temp_db):
    assert artifact_service.load_artifacts("Nope") == {}


def test_load_artifacts_handles_missing_backing_file_gracefully(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pid = projects_repo.get_or_create_id("P")
    artefacts_repo.upsert("art_1", pid, "Orphaned")  # no source_id — legacy/un-migrated artefact
    loaded = artifact_service.load_artifacts("P")
    assert loaded["art_1"]["filename"] is None
    assert loaded["art_1"]["content"] == ""


def test_save_artifacts_deletes_entries_omitted_from_dict(temp_db, tmp_path, monkeypatch):
    """save_artifacts must fully replace a project's artefact set, matching the old
    write_json(artifacts.json, artifacts) semantics: callers like
    routes/artifacts.py's delete_artifact_route load the dict, `del` an entry, then
    call save_artifacts with the now-smaller dict and expect the removed artefact to
    disappear from storage entirely, not just lose its filename/content."""
    monkeypatch.chdir(tmp_path)
    pid = projects_repo.get_or_create_id("P")
    artifact_service.save_artifacts(
        "P",
        {
            "art_1": {"id": "art_1", "name": "Keep me"},
            "art_2": {"id": "art_2", "name": "Delete me"},
        },
    )
    assert artefacts_repo.get("art_1") is not None
    assert artefacts_repo.get("art_2") is not None

    # Simulate delete_artifact_route: load, remove one key, save back.
    artifacts = artifact_service.load_artifacts("P")
    del artifacts["art_2"]
    artifact_service.save_artifacts("P", artifacts)

    assert artefacts_repo.get("art_1") is not None
    assert artefacts_repo.get("art_2") is None
    remaining_ids = {row["id"] for row in artefacts_repo.list_for_project(pid)}
    assert remaining_ids == {"art_1"}
