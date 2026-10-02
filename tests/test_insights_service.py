# tests/test_insights_service.py
from db.repositories import projects_repo, sources_repo
from services import insights_service


def _sample_data(summary="A landscape summary", evidence=None, linked_file_id=None):
    data = {
        "generated_at": "2026-01-01T00:00:00",
        "competitors": [{"name": "Comp A"}],
        "competitor_landscape_markdown": "# Landscape",
        "phases": {},
    }
    for key in [str(i) for i in range(1, 8)]:
        data["phases"][key] = {
            "title": f"Phase {key}",
            "summary": summary if key == "1" else "MISSING",
            "confidence": "medium" if key == "1" else "none",
            "evidence_sources": evidence or [] if key == "1" else [],
            "gaps": ["Some gap"] if key == "1" else [],
            "suggested_topics": ["Look into X"] if key == "1" else [],
            "linked_files": [],
            "linked_file_ids": [linked_file_id] if (key == "1" and linked_file_id) else [],
        }
    return data


def test_save_then_load_current_round_trips_phase_1(temp_db):
    insights_service.save_insights("P", _sample_data())
    loaded = insights_service.load_current_insights("P")
    assert loaded["phases"]["1"]["summary"] == "A landscape summary"
    assert loaded["phases"]["1"]["gaps"] == ["Some gap"]
    # Suggested topics are promoted into standalone ad-hoc research_tasks on save
    # (research_tasks_repo.create_ad_hoc_task) rather than preserved on the phase
    # itself -- there is no (task, version)-scoped storage for them in the schema,
    # by design (the same one-way pattern is used by the legacy-data migration
    # script for this exact field). So they don't round-trip back onto the phase.
    assert loaded["phases"]["1"]["suggested_topics"] == []
    assert loaded["competitors"] == [{"name": "Comp A"}]
    assert loaded["competitor_landscape_markdown"] == "# Landscape"
    # Untouched phases still round-trip to their empty defaults
    assert loaded["phases"]["2"]["summary"] == "MISSING"


def test_save_twice_creates_two_versions_and_history_preserves_first(temp_db):
    insights_service.save_insights("P", _sample_data(summary="Version 1 summary"))
    insights_service.save_insights("P", _sample_data(summary="Version 2 summary"))

    current = insights_service.load_current_insights("P")
    assert current["phases"]["1"]["summary"] == "Version 2 summary"

    history = insights_service.load_insights_history("P")
    assert [h["version"] for h in history] == [1, 2]
    assert history[0]["data"]["phases"]["1"]["summary"] == "Version 1 summary"
    assert history[1]["data"]["phases"]["1"]["summary"] == "Version 2 summary"


def test_evidence_sources_with_linked_file_round_trips(temp_db):
    pid = projects_repo.get_or_create_id("P")
    sources_repo.upsert(pid, "f_abc123", "report.pdf")
    insights_service.save_insights(
        "P", _sample_data(evidence=["freeform citation"], linked_file_id="f_abc123")
    )
    current = insights_service.load_current_insights("P")
    assert current["phases"]["1"]["linked_file_ids"] == ["f_abc123"]
    assert current["phases"]["1"]["linked_files"] == ["report.pdf"]


def test_load_current_insights_for_project_with_no_saves_yet_returns_empty_shape(temp_db):
    loaded = insights_service.load_current_insights("Untouched Project")
    assert loaded["phases"]["1"]["summary"] == "MISSING"
    assert loaded["competitors"] == []


def test_load_insights_history_for_project_with_no_saves_is_empty_list(temp_db):
    assert insights_service.load_insights_history("Untouched Project") == []


def test_excluded_competitors_round_trip(temp_db):
    insights_service.save_excluded_competitors("P", ["Comp A", "Comp B"])
    assert set(insights_service.load_excluded_competitors("P")) == {"Comp A", "Comp B"}
