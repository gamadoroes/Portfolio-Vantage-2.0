from db.repositories import projects_repo, research_work_items_repo
from services import insights_service, project_service, supervisor_service


def test_build_context_includes_objective(temp_db):
    projects_repo.get_or_create_id("P")
    project_service.save_project_prompt("P", "project_prompt", "Assess the online MBA market.")
    context = supervisor_service.build_context("P")
    assert "Assess the online MBA market." in context


def test_build_context_includes_phase_definitions(temp_db):
    projects_repo.get_or_create_id("P")
    context = supervisor_service.build_context("P")
    assert "The Landscape" in context  # phase "1"
    assert "Options for OES" in context  # phase "7"


def test_build_context_includes_existing_phase_summary(temp_db):
    projects_repo.get_or_create_id("P")
    insights_service.save_insights("P", {
        "generated_at": "2026-01-01T00:00:00", "competitors": [],
        "competitor_landscape_markdown": "",
        "phases": {str(i): {"title": f"Phase {i}", "summary": "Established fact" if i == 1 else "MISSING",
                             "confidence": "high" if i == 1 else "none", "evidence_sources": [], "gaps": [],
                             "suggested_topics": [], "linked_files": [], "linked_file_ids": []}
                   for i in range(1, 8)},
    })
    context = supervisor_service.build_context("P")
    assert "Established fact" in context


def test_build_context_includes_work_items_and_dependencies(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "Product / La Trobe", priority="high")
    b = research_work_items_repo.create(pid, "4", "Product / Torrens")
    research_work_items_repo.add_dependency(b, a)

    context = supervisor_service.build_context("P")
    assert "Product / La Trobe" in context
    assert "Product / Torrens" in context
    assert str(a) in context  # the dependency should be visible by id


def test_build_context_includes_recent_decisions(temp_db):
    from db.repositories import agent_decisions_repo
    pid = projects_repo.get_or_create_id("P")
    agent_decisions_repo.record(pid, "NO_ACTION", '{"reason": "nothing ready yet"}')

    context = supervisor_service.build_context("P")
    assert "NO_ACTION" in context
    assert "nothing ready yet" in context


def test_build_context_does_not_leak_other_projects(temp_db):
    pid1 = projects_repo.get_or_create_id("P1")
    pid2 = projects_repo.get_or_create_id("P2")
    research_work_items_repo.create(pid1, "4", "P1-only task")
    research_work_items_repo.create(pid2, "4", "P2-only task")
    from db.repositories import agent_decisions_repo
    agent_decisions_repo.record(pid1, "PROPOSE_TASKS", '{"note": "p1 decision"}')
    agent_decisions_repo.record(pid2, "PROPOSE_TASKS", '{"note": "p2 decision"}')

    context1 = supervisor_service.build_context("P1")
    context2 = supervisor_service.build_context("P2")

    assert "P1-only task" in context1
    assert "P2-only task" not in context1
    assert "p1 decision" in context1
    assert "p2 decision" not in context1

    assert "P2-only task" in context2
    assert "P1-only task" not in context2
