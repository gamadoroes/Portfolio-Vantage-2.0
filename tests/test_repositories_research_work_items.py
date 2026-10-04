from db.repositories import projects_repo, research_work_items_repo


def test_create_and_get_round_trip(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(
        pid, "4", "Product / La Trobe",
        objective="Scope La Trobe's product offering",
        priority="high",
        research_method="deep_research",
        entities_json='["La Trobe"]',
        expected_output="A product comparison table",
        source_requirements_json='["public website"]',
        human_review_required=1,
        max_retries=5,
    )
    row = research_work_items_repo.get(task_id)
    assert row["project_id"] == pid
    assert row["phase_key"] == "4"
    assert row["title"] == "Product / La Trobe"
    assert row["objective"] == "Scope La Trobe's product offering"
    assert row["status"] == "PROPOSED"
    assert row["priority"] == "high"
    assert row["entities_json"] == '["La Trobe"]'
    assert row["human_review_required"] == 1
    assert row["max_retries"] == 5
    assert row["retry_count"] == 0


def test_get_missing_returns_none(temp_db):
    assert research_work_items_repo.get(9999) is None


def test_list_for_project_and_phase(temp_db):
    pid = projects_repo.get_or_create_id("P")
    research_work_items_repo.create(pid, "4", "Product / La Trobe")
    research_work_items_repo.create(pid, "4", "Product / Torrens")
    research_work_items_repo.create(pid, "1", "Landscape overview")

    assert len(research_work_items_repo.list_for_project(pid)) == 3
    phase_4_items = research_work_items_repo.list_for_phase(pid, "4")
    assert len(phase_4_items) == 2
    assert {r["title"] for r in phase_4_items} == {"Product / La Trobe", "Product / Torrens"}


def test_multiple_tasks_allowed_under_one_phase(temp_db):
    # The core Phase-2 relaxation: no UNIQUE(project_id, phase_key) here,
    # unlike the legacy research_tasks table.
    pid = projects_repo.get_or_create_id("P")
    id1 = research_work_items_repo.create(pid, "4", "Product / La Trobe")
    id2 = research_work_items_repo.create(pid, "4", "Product / Torrens")
    assert id1 != id2


def test_update_fields(temp_db):
    pid = projects_repo.get_or_create_id("P")
    task_id = research_work_items_repo.create(pid, "4", "Product / La Trobe")
    assert research_work_items_repo.update_fields(task_id, status="READY", completeness_score=0.5) is True
    row = research_work_items_repo.get(task_id)
    assert row["status"] == "READY"
    assert row["completeness_score"] == 0.5


def test_update_fields_unknown_id_returns_false(temp_db):
    assert research_work_items_repo.update_fields(9999, status="READY") is False


def test_dependency_round_trip(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")

    research_work_items_repo.add_dependency(a, b)
    deps = research_work_items_repo.list_dependencies(a)
    assert [r["depends_on_work_item_id"] for r in deps] == [b]

    dependents = research_work_items_repo.list_dependents(b)
    assert [r["work_item_id"] for r in dependents] == [a]

    research_work_items_repo.remove_dependency(a, b)
    assert research_work_items_repo.list_dependencies(a) == []


def test_add_dependency_is_idempotent(temp_db):
    pid = projects_repo.get_or_create_id("P")
    a = research_work_items_repo.create(pid, "4", "A")
    b = research_work_items_repo.create(pid, "4", "B")
    research_work_items_repo.add_dependency(a, b)
    research_work_items_repo.add_dependency(a, b)  # must not raise (duplicate PK)
    assert len(research_work_items_repo.list_dependencies(a)) == 1
