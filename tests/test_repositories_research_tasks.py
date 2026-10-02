# tests/test_repositories_research_tasks.py
from db.repositories import projects_repo, research_tasks_repo, sources_repo


def test_get_or_create_task_is_idempotent_per_phase(temp_db):
    pid = projects_repo.get_or_create_id("P")
    tid1 = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    tid2 = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    assert tid1 == tid2


def test_create_ad_hoc_task_allows_multiple_per_project(temp_db):
    pid = projects_repo.get_or_create_id("P")
    tid1 = research_tasks_repo.create_ad_hoc_task(pid, "Look into X")
    tid2 = research_tasks_repo.create_ad_hoc_task(pid, "Look into Y")
    assert tid1 != tid2


def test_version_numbering_increments(temp_db):
    pid = projects_repo.get_or_create_id("P")
    assert research_tasks_repo.next_version_number(pid) == 1
    research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    assert research_tasks_repo.next_version_number(pid) == 2


def test_get_latest_version(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    research_tasks_repo.create_version(pid, 2, "2026-01-02T00:00:00", "[]", "")
    assert research_tasks_repo.get_latest_version(pid)["version"] == 2


def test_get_latest_version_none_when_no_versions(temp_db):
    pid = projects_repo.get_or_create_id("P")
    assert research_tasks_repo.get_latest_version(pid) is None


def test_finding_and_evidence_round_trip(temp_db):
    pid = projects_repo.get_or_create_id("P")
    tid = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    vid = research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    fid = research_tasks_repo.create_finding(tid, vid, "Summary text", "Some gap", "medium")
    sid = sources_repo.upsert(pid, "f_1", "report.pdf")
    eid1 = research_tasks_repo.create_evidence(pid, None, source_id=sid)
    eid2 = research_tasks_repo.create_evidence(pid, "freeform citation text")
    research_tasks_repo.link_finding_evidence(fid, eid1)
    research_tasks_repo.link_finding_evidence(fid, eid2)

    findings = research_tasks_repo.list_findings_for_version(vid)
    assert len(findings) == 1
    assert findings[0]["content"] == "Summary text"

    evidence = research_tasks_repo.list_evidence_for_finding(fid)
    assert len(evidence) == 2


def test_get_finding_for_task_in_version_is_scoped_correctly(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task1 = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    task2 = research_tasks_repo.get_or_create_task(pid, "2", "The Student")
    vid = research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    research_tasks_repo.create_finding(task1, vid, "Phase 1 summary", None, "medium")
    research_tasks_repo.create_finding(task2, vid, "Phase 2 summary", None, "medium")

    # The bug this guards against: list_findings_for_version returns BOTH rows,
    # so callers must look up by (task, version), not just take the first result.
    all_in_version = research_tasks_repo.list_findings_for_version(vid)
    assert len(all_in_version) == 2

    phase1_finding = research_tasks_repo.get_finding_for_task_in_version(task1, vid)
    phase2_finding = research_tasks_repo.get_finding_for_task_in_version(task2, vid)
    assert phase1_finding["content"] == "Phase 1 summary"
    assert phase2_finding["content"] == "Phase 2 summary"


def test_unlink_evidence_by_source_in_version(temp_db):
    pid = projects_repo.get_or_create_id("P")
    tid = research_tasks_repo.get_or_create_task(pid, "1", "The Landscape")
    sid = sources_repo.upsert(pid, "f_1", "report.pdf")

    # Version 1: finding + evidence linked to the source we're about to unlink.
    vid1 = research_tasks_repo.create_version(pid, 1, "2026-01-01T00:00:00", "[]", "")
    fid1 = research_tasks_repo.create_finding(tid, vid1, "Summary", None, "medium")
    eid1 = research_tasks_repo.create_evidence(pid, None, source_id=sid)
    research_tasks_repo.link_finding_evidence(fid1, eid1)

    # Version 2: a separate finding for the same task, with its own evidence
    # row pointing at the SAME source, linked in this other version.
    vid2 = research_tasks_repo.create_version(pid, 2, "2026-01-02T00:00:00", "[]", "")
    fid2 = research_tasks_repo.create_finding(tid, vid2, "Summary v2", None, "medium")
    eid2 = research_tasks_repo.create_evidence(pid, None, source_id=sid)
    research_tasks_repo.link_finding_evidence(fid2, eid2)

    research_tasks_repo.unlink_evidence_by_source_in_version(vid1, sid)

    # Version 1's link to the source is gone...
    assert research_tasks_repo.list_evidence_for_finding(fid1) == []
    # ...but version 2's link to the same source is untouched.
    assert len(research_tasks_repo.list_evidence_for_finding(fid2)) == 1
