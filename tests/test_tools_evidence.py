import pytest

from db.repositories import (
    agent_decisions_repo,
    evidence_repo,
    projects_repo,
    research_runs_repo,
    research_work_items_repo,
)
from services import board_service, research_task_service, tools
from services.tools import evidence


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return projects_repo.get_or_create_id("P")


def _card(pid, phase="4", title="Fees"):
    return research_task_service.create_task(pid, phase, title, research_method="TARGETED_WEB")


def _source(url="https://deakin.edu.au/fees", title="Deakin fees", project="P", **extra):
    return tools.run_tool("save_source", "system", project, dict({"url": url, "title": title}, **extra))


def _fact(card, claim="Deakin charges $3,000 per unit.", project="P", caller="system", **extra):
    return tools.run_tool("save_evidence", caller, project, dict({"task_id": card, "claim": claim}, **extra))


def _status(kind, item_id, action, caller="user", project="P"):
    return tools.run_tool("update_evidence_status", caller, project, {"kind": kind, "id": item_id, "action": action})


# ---- save_source ----

def test_a_page_is_saved_once(pid):
    first = _source(publisher="Deakin University", published_date="2026-02-01")
    again = _source(title="Another title")
    assert first.data["created"] is True
    assert again.data == {"source_id": first.data["source_id"], "created": False}
    row = evidence_repo.get_source(first.data["source_id"])
    assert (row["title"], row["publisher"], row["published_date"]) == ("Deakin fees", "Deakin University", "2026-02-01")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://deakin.edu.au/x", "deakin.edu.au/fees", "https://", ""])
def test_a_page_needs_a_web_address(pid, url):
    assert _source(url=url).error["code"] == "invalid_input"


def test_each_project_has_its_own_pages(pid):
    projects_repo.get_or_create_id("Other")
    assert _source().data["source_id"] != _source(project="Other").data["source_id"]


# ---- save_evidence ----

def test_a_fact_takes_its_phase_from_the_research(pid):
    card = _card(pid, phase="2")
    source_id = _source().data["source_id"]
    result = _fact(card, quote="$3,000 per unit", source_id=source_id, as_of="2026")
    assert result.data["created"] is True and result.data["status"] == "active"
    row = evidence_repo.get_fact(result.data["fact_id"])
    assert (row["phase_key"], row["research_work_item_id"], row["cited_source_id"], row["as_of"]) == (
        "2", card, source_id, "2026")
    assert row["claim_key"] == "deakin charges $3,000 per unit"


def test_the_same_claim_returns_the_fact_already_saved(pid):
    card = _card(pid)
    first = _fact(card).data
    again = _fact(card, claim="  DEAKIN charges   $3,000 per unit!! ").data
    assert again == {"fact_id": first["fact_id"], "created": False, "status": "active"}


def test_claim_key():
    assert evidence.claim_key("  Deakin  Charges $3,000.\n") == "deakin charges $3,000"
    assert evidence.claim_key("...") == ""


def test_a_fact_links_only_to_this_projects_page_and_this_researchs_run(pid):
    card = _card(pid)
    projects_repo.get_or_create_id("Other")
    theirs = _source(project="Other").data["source_id"]
    assert _fact(card, source_id=theirs).error["code"] == "not_found"
    research_runs_repo.create("run_other", pid, None, None, "p")
    research_runs_repo.update("run_other", research_work_item_id=_card(pid, title="Other card"))
    assert _fact(card, run_id="run_other").error["code"] == "not_found"
    research_runs_repo.create("run_mine", pid, None, None, "p")
    research_runs_repo.update("run_mine", research_work_item_id=card)
    assert evidence_repo.get_fact(_fact(card, run_id="run_mine").data["fact_id"])["run_id"] == "run_mine"


@pytest.mark.parametrize("claim", ["", "   ", "x" * 501, "?!"])
def test_a_fact_needs_a_claim_of_a_sensible_length(pid, claim):
    assert _fact(_card(pid), claim=claim).error["code"] == "invalid_input"


def test_a_fact_for_another_projects_research_is_not_found(pid):
    theirs = _card(projects_repo.get_or_create_id("Other"))
    assert _fact(theirs).error["code"] == "not_found"


def test_a_fact_for_research_with_no_phase_is_a_conflict_not_a_crash(pid):
    card = research_work_items_repo.create(pid, None, "No phase")
    assert _fact(card).error["code"] == "conflict"


# ---- create_finding ----

def test_a_conclusion_cites_its_facts(pid):
    card = _card(pid)
    a = _fact(card).data["fact_id"]
    b = _fact(card, claim="Monash runs three intakes").data["fact_id"]
    result = tools.run_tool("create_finding", "system", "P",
                            {"task_id": card, "text": "Fees and intakes differ", "fact_ids": [a, b, a]})
    conclusion_id = result.data["conclusion_id"]
    row = evidence_repo.get_conclusion(conclusion_id)
    assert (row["phase_key"], row["text"], row["status"]) == ("4", "Fees and intakes differ", "active")
    assert evidence_repo.conclusion_fact_links(pid) == {conclusion_id: [(a, "active"), (b, "active")]}


def test_a_conclusion_cannot_cite_a_rejected_or_foreign_fact(pid):
    card = _card(pid)
    fact_id = _fact(card).data["fact_id"]
    _status("fact", fact_id, "reject")
    finding = {"task_id": card, "text": "Pricey", "fact_ids": [fact_id]}
    assert tools.run_tool("create_finding", "system", "P", finding).error["code"] == "conflict"
    other_card = _card(projects_repo.get_or_create_id("Other"))
    theirs = _fact(other_card, project="Other").data["fact_id"]
    assert tools.run_tool("create_finding", "system", "P", dict(finding, fact_ids=[theirs])).error["code"] == "not_found"


def test_a_conclusion_for_research_with_no_phase_is_a_conflict_not_a_crash(pid):
    fact_id = _fact(_card(pid)).data["fact_id"]
    card = research_work_items_repo.create(pid, None, "No phase")
    result = tools.run_tool("create_finding", "system", "P", {"task_id": card, "text": "x", "fact_ids": [fact_id]})
    assert result.error["code"] == "conflict"
    assert evidence_repo.conclusion_fact_links(pid) == {}


@pytest.mark.parametrize("fact_ids", [[], list(range(1, 12))])
def test_a_conclusion_cites_one_to_ten_facts(pid, fact_ids):
    result = tools.run_tool("create_finding", "system", "P", {"task_id": _card(pid), "text": "x", "fact_ids": fact_ids})
    assert result.error["code"] == "invalid_input"


# ---- search_existing_evidence ----

def test_search_finds_active_facts_newest_first(pid):
    card = _card(pid)
    older = _fact(card, claim="Deakin charges $3,000 per unit").data["fact_id"]
    newer = _fact(card, claim="Deakin has three intakes").data["fact_id"]
    gone = _fact(card, claim="Deakin is the cheapest").data["fact_id"]
    _fact(_card(pid, phase="2", title="Students"), claim="Most Deakin students work full time")
    _status("fact", gone, "reject")
    found = tools.run_tool("search_existing_evidence", "user", "P", {"words": "deakin", "phase_key": "4"}).data["facts"]
    assert [f["id"] for f in found] == [newer, older]
    assert found[0]["card_title"] == "Fees" and found[0]["source"] is None


def test_search_has_a_limit_and_lists_the_newest_without_words(pid):
    card = _card(pid)
    for i in range(25):
        _fact(card, claim=f"Fact number {i}")
    found = tools.run_tool("search_existing_evidence", "supervisor", "P", {}).data["facts"]
    assert len(found) == 10 and found[0]["claim"] == "Fact number 24"
    assert len(tools.run_tool("search_existing_evidence", "system", "P", {"limit": 20}).data["facts"]) == 20
    assert tools.run_tool("search_existing_evidence", "user", "P", {"limit": 21}).error["code"] == "invalid_input"


def test_search_shows_the_page_a_fact_cites(pid):
    source_id = _source().data["source_id"]
    _fact(_card(pid), source_id=source_id)
    (found,) = tools.run_tool("search_existing_evidence", "user", "P", {"words": "deakin fees"}).data["facts"]
    assert found["source"] == {"url": "https://deakin.edu.au/fees", "title": "Deakin fees", "publisher": "",
                               "published_date": ""}


# ---- update_evidence_status ----

def test_reject_and_restore_keep_the_row_and_show_in_the_activity_feed(pid):
    card = _card(pid)
    fact_id = _fact(card).data["fact_id"]
    conclusion_id = tools.run_tool("create_finding", "system", "P",
                                   {"task_id": card, "text": "Pricey", "fact_ids": [fact_id]}).data["conclusion_id"]
    assert _status("fact", fact_id, "reject").data == {"id": fact_id, "status": "rejected"}
    assert _status("fact", fact_id, "reject").data["status"] == "rejected"  # again: no change, no new Activity line
    assert _status("conclusion", conclusion_id, "reject").data["status"] == "rejected"
    assert _status("conclusion", conclusion_id, "restore").data["status"] == "active"
    assert evidence_repo.get_fact(fact_id)["status"] == "rejected"
    texts = [a["text"] for a in board_service.get_board_state("P")["activity"]]
    assert texts == ["You restored a conclusion.", "You rejected a conclusion.", "You rejected a fact."]


@pytest.mark.parametrize("caller", ["supervisor", "system"])
def test_only_the_person_rejects_or_restores(pid, caller):
    fact_id = _fact(_card(pid)).data["fact_id"]
    assert _status("fact", fact_id, "reject", caller=caller).error["code"] == "not_allowed"
    assert evidence_repo.get_fact(fact_id)["status"] == "active"


def test_reject_is_scoped_to_the_project(pid):
    other = projects_repo.get_or_create_id("Other")
    theirs = _fact(_card(other), project="Other").data["fact_id"]
    assert _status("fact", theirs, "reject").error["code"] == "not_found"
    assert _status("conclusion", 99999, "reject").error["code"] == "not_found"
    assert agent_decisions_repo.list_for_project(pid) == []


def test_the_person_cannot_save_facts_directly(pid):
    card = _card(pid)
    assert _source().ok and _fact(card, caller="supervisor").ok
    for name, inputs in (("save_source", {"url": "https://a.example", "title": "A"}),
                         ("save_evidence", {"task_id": card, "claim": "A claim"}),
                         ("create_finding", {"task_id": card, "text": "x", "fact_ids": [1]})):
        assert tools.run_tool(name, "user", "P", inputs).error["code"] == "not_allowed"
