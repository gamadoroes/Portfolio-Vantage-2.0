from db.repositories import artefacts_repo, projects_repo, research_tasks_repo, sources_repo


def test_upsert_creates_then_reuses(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid1 = sources_repo.upsert(pid, "f_abc", "doc.txt")
    sid2 = sources_repo.upsert(pid, "f_abc", "doc.txt")
    assert sid1 == sid2


def test_get_by_stable_id_and_filename(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "doc.txt")
    assert sources_repo.get_by_stable_id(pid, "f_abc")["id"] == sid
    assert sources_repo.get_by_filename(pid, "doc.txt")["id"] == sid
    assert sources_repo.get_by_filename(pid, "nope.txt") is None


def test_rename_preserves_id(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "old.txt")
    sources_repo.rename(sid, "new.txt")
    assert sources_repo.get_by_stable_id(pid, "f_abc")["filename"] == "new.txt"


def test_delete_removes_source(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "doc.txt")
    sources_repo.delete(sid)
    assert sources_repo.get_by_stable_id(pid, "f_abc") is None


def test_selected_sources_round_trip(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid1 = sources_repo.upsert(pid, "f_1", "a.txt")
    sid2 = sources_repo.upsert(pid, "f_2", "b.txt")
    sources_repo.set_selected(pid, sid1, True)
    assert sources_repo.list_selected_ids(pid) == [sid1]
    sources_repo.set_selected(pid, sid2, True)
    assert set(sources_repo.list_selected_ids(pid)) == {sid1, sid2}
    sources_repo.set_selected(pid, sid1, False)
    assert sources_repo.list_selected_ids(pid) == [sid2]


def test_delete_source_also_unselects_it(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_1", "a.txt")
    sources_repo.set_selected(pid, sid, True)
    sources_repo.delete(sid)
    assert sources_repo.list_selected_ids(pid) == []


def test_delete_source_linked_as_evidence_does_not_raise(temp_db):
    """Regression test: evidence.source_id has no ON DELETE clause and
    foreign_keys=ON, so deleting a source still referenced by an evidence row
    used to raise sqlite3.IntegrityError. delete() must detach (not cascade-
    delete) the evidence instead, so the finding/evidence/historical-version
    record survives with source_id now NULL.
    """
    pid = projects_repo.get_or_create_id("P")
    tid = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    vid = research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    fid = research_tasks_repo.create_finding(tid, vid, "Summary", None, "medium")
    sid = sources_repo.upsert(pid, "f_abc", "doc.txt")
    eid = research_tasks_repo.create_evidence(pid, None, source_id=sid)
    research_tasks_repo.link_finding_evidence(fid, eid)

    sources_repo.delete(sid)  # must not raise

    evidence_rows = research_tasks_repo.list_evidence_for_finding(fid)
    assert len(evidence_rows) == 1
    assert evidence_rows[0]["id"] == eid
    assert evidence_rows[0]["source_id"] is None


def test_delete_source_referenced_by_artefact_does_not_raise(temp_db):
    """Regression test: artefacts.source_id has no ON DELETE clause either
    (same as evidence.source_id), and foreign_keys=ON, so deleting a source
    still referenced by an artefact row also used to raise
    sqlite3.IntegrityError. delete() must detach the artefact's source_id
    instead of cascading, so the artefact row survives with source_id NULL.
    """
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "doc.txt")
    artefacts_repo.upsert("art_1", pid, "My Artefact", source_id=sid)

    sources_repo.delete(sid)  # must not raise

    artefact = artefacts_repo.get("art_1")
    assert artefact is not None
    assert artefact["source_id"] is None
