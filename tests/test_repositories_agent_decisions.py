from db.repositories import agent_decisions_repo, projects_repo, research_work_items_repo


def test_record_without_work_item_id(temp_db):
    pid = projects_repo.get_or_create_id("P")
    decision_id = agent_decisions_repo.record(pid, "NO_ACTION", '{"reason": "nothing to do"}')
    assert isinstance(decision_id, int)


def test_record_with_work_item_id(temp_db):
    pid = projects_repo.get_or_create_id("P")
    work_item_id = research_work_items_repo.create(pid, "4", "Task")
    decision_id = agent_decisions_repo.record(
        pid, "SKIP_TASK", '{"task_id": 7}', research_work_item_id=work_item_id
    )
    assert isinstance(decision_id, int)


def test_list_for_project_ordering_and_limit(temp_db):
    pid = projects_repo.get_or_create_id("P")
    for i in range(5):
        agent_decisions_repo.record(pid, f"ACTION_{i}", "{}")

    all_decisions = agent_decisions_repo.list_for_project(pid)
    assert len(all_decisions) == 5
    assert all_decisions[0]["decision_type"] == "ACTION_4"  # most recent first

    limited = agent_decisions_repo.list_for_project(pid, limit=2)
    assert len(limited) == 2
    assert limited[0]["decision_type"] == "ACTION_4"


def test_list_for_project_scoped_to_project(temp_db):
    pid1 = projects_repo.get_or_create_id("P1")
    pid2 = projects_repo.get_or_create_id("P2")
    agent_decisions_repo.record(pid1, "ACTION_P1", "{}")
    agent_decisions_repo.record(pid2, "ACTION_P2", "{}")

    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid1)] == ["ACTION_P1"]
    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid2)] == ["ACTION_P2"]
