from db.repositories import projects_repo, sources_repo


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
