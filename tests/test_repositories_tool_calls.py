from db.repositories import projects_repo, research_work_items_repo, tool_calls_repo


def test_create_then_finish_records_the_outcome(temp_db):
    pid = projects_repo.get_or_create_id("P")
    card = research_work_items_repo.create(pid, "4", "Card")
    call_id = tool_calls_repo.create(pid, "get_task", "system", '{"task_id": 1}')
    row = tool_calls_repo.get(call_id)
    assert (row["tool"], row["caller"], row["ok"]) == ("get_task", "system", 0)
    assert row["started_at"]
    tool_calls_repo.finish(call_id, True, result_json='{"x": 1}', duration_ms=12, research_work_item_id=card)
    row = tool_calls_repo.get(call_id)
    assert (row["ok"], row["result_json"], row["duration_ms"], row["research_work_item_id"]) == (1, '{"x": 1}', 12, card)


def test_failure_and_parent_link(temp_db):
    pid = projects_repo.get_or_create_id("P")
    parent = tool_calls_repo.create(pid, "evaluate_research_output", "supervisor", "{}")
    child = tool_calls_repo.create(pid, "create_followup_task", "supervisor", "{}", parent_call_id=parent)
    tool_calls_repo.finish(child, False, error_code="conflict", error_message="Wrong state")
    row = tool_calls_repo.get(child)
    assert (row["parent_call_id"], row["ok"], row["error_code"], row["error_message"]) == (parent, 0, "conflict", "Wrong state")


def test_list_for_project_is_newest_first_and_scoped(temp_db):
    pid = projects_repo.get_or_create_id("P")
    other = projects_repo.get_or_create_id("Other")
    first = tool_calls_repo.create(pid, "a", "user", "{}")
    second = tool_calls_repo.create(pid, "b", "user", "{}")
    tool_calls_repo.create(other, "c", "user", "{}")
    assert [r["id"] for r in tool_calls_repo.list_for_project(pid)] == [second, first]
