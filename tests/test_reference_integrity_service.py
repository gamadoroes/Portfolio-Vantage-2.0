from db.repositories import projects_repo, sources_repo
from services import insights_service
from services.reference_integrity_service import (
    remove_file_references,
    replace_file_references,
)


def _sample_data_linked_to(stable_file_id):
    data = {"generated_at": "2026-01-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "", "phases": {}}
    for key in [str(i) for i in range(1, 8)]:
        data["phases"][key] = {
            "title": f"Phase {key}", "summary": "A summary" if key == "1" else "MISSING",
            "confidence": "medium" if key == "1" else "none", "evidence_sources": [],
            "gaps": [], "suggested_topics": [],
            "linked_files": [], "linked_file_ids": [stable_file_id] if key == "1" else [],
        }
    return data


def test_replace_file_references_is_a_no_op_now(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sources_repo.upsert(pid, "f_abc", "old.txt")
    result = replace_file_references("P", "old.txt", "new.txt")
    assert result == {"changed": False}


def test_remove_file_references_unselects_source(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sid = sources_repo.upsert(pid, "f_abc", "doc.txt")
    sources_repo.set_selected(pid, sid, True)
    remove_file_references("P", "doc.txt", file_id="f_abc")
    assert sources_repo.list_selected_ids(pid) == []


def test_remove_file_references_unlinks_from_current_version_only(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sources_repo.upsert(pid, "f_abc", "doc.txt")

    # Two versions, both linked to the same source: v1 is "historical" once
    # v2 is saved (becomes current/latest). A single save_insights() call
    # can't distinguish "current" from "historical" since there'd be only
    # one version to look at either way.
    insights_service.save_insights("P", _sample_data_linked_to("f_abc"))
    insights_service.save_insights("P", _sample_data_linked_to("f_abc"))

    before = insights_service.load_current_insights("P")
    assert before["phases"]["1"]["linked_file_ids"] == ["f_abc"]

    remove_file_references("P", "doc.txt", file_id="f_abc")

    after = insights_service.load_current_insights("P")
    assert after["phases"]["1"]["linked_file_ids"] == []

    # Historical (v1) version is untouched; only current (v2) was unlinked.
    history = insights_service.load_insights_history("P")
    assert history[0]["version"] == 1
    assert history[0]["data"]["phases"]["1"]["linked_file_ids"] == ["f_abc"]
    assert history[1]["version"] == 2
    assert history[1]["data"]["phases"]["1"]["linked_file_ids"] == []


def test_remove_file_references_prefers_file_id_over_ambiguous_filename(temp_db):
    """sources has no uniqueness constraint on (project_id, filename), only on
    (project_id, stable_file_id) -- so two rows can share a filename (e.g. a
    race between concurrent reconcile_file_index() upserts). When file_id is
    given it must disambiguate which row gets unselected, not whichever
    filename-matching row the DB happens to return first.
    """
    pid = projects_repo.get_or_create_id("P")
    old_sid = sources_repo.upsert(pid, "f_old", "dup.txt")
    new_sid = sources_repo.upsert(pid, "f_new", "dup.txt")
    sources_repo.set_selected(pid, old_sid, True)
    sources_repo.set_selected(pid, new_sid, True)

    remove_file_references("P", "dup.txt", file_id="f_new")

    selected = sources_repo.list_selected_ids(pid)
    assert selected == [old_sid]
