# tests/test_repositories_excluded_competitors.py
from db.repositories import excluded_competitors_repo, projects_repo


def test_add_and_list(temp_db):
    pid = projects_repo.get_or_create_id("P")
    excluded_competitors_repo.add(pid, "Competitor A")
    excluded_competitors_repo.add(pid, "Competitor B")
    assert set(excluded_competitors_repo.list_for_project(pid)) == {"Competitor A", "Competitor B"}


def test_add_is_idempotent(temp_db):
    pid = projects_repo.get_or_create_id("P")
    excluded_competitors_repo.add(pid, "Competitor A")
    excluded_competitors_repo.add(pid, "Competitor A")
    assert excluded_competitors_repo.list_for_project(pid) == ["Competitor A"]


def test_remove(temp_db):
    pid = projects_repo.get_or_create_id("P")
    excluded_competitors_repo.add(pid, "Competitor A")
    excluded_competitors_repo.remove(pid, "Competitor A")
    assert excluded_competitors_repo.list_for_project(pid) == []
