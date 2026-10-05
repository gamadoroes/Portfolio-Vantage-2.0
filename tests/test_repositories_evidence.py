from datetime import datetime, timedelta

import pytest

from db.repositories import evidence_repo, projects_repo, research_runs_repo, research_work_items_repo


@pytest.fixture
def pid(temp_db):
    return projects_repo.get_or_create_id("P")


def _card(pid, phase="4", title="Fees"):
    return research_work_items_repo.create(pid, phase, title)


def _fact(pid, card, claim, phase="4", **kw):
    return evidence_repo.get_or_create_fact(pid, phase, card, claim, claim.lower(), **kw)[0]


def test_a_page_is_stored_once_per_project(pid):
    first = evidence_repo.get_or_create_source(pid, "https://a.example/fees", "Fees", publisher="A Uni")
    again = evidence_repo.get_or_create_source(pid, "https://a.example/fees", "Other title")
    other = evidence_repo.get_or_create_source(projects_repo.get_or_create_id("Other"), "https://a.example/fees", "Fees")
    assert first[1] is True and again == (first[0], False) and other[0] != first[0]
    row = evidence_repo.get_source(first[0])
    assert (row["title"], row["publisher"], row["project_id"]) == ("Fees", "A Uni", pid)


def test_a_claim_is_stored_once_per_project(pid):
    card = _card(pid)
    first = evidence_repo.get_or_create_fact(pid, "4", card, "Deakin charges $3,000.", "deakin charges $3,000")
    again = evidence_repo.get_or_create_fact(pid, "4", card, "DEAKIN charges $3,000", "deakin charges $3,000")
    assert first[1] is True and again == (first[0], False)
    row = evidence_repo.get_fact(first[0])
    assert (row["status"], row["phase_key"], row["research_work_item_id"], row["claim"]) == (
        "active", "4", card, "Deakin charges $3,000.")


def test_conclusions_link_their_facts(pid):
    card = _card(pid)
    a, b = _fact(pid, card, "A"), _fact(pid, card, "B")
    conclusion_id = evidence_repo.create_conclusion(pid, "4", card, "Both matter", [a, b])
    assert evidence_repo.get_conclusion(conclusion_id)["status"] == "active"
    evidence_repo.set_fact_status(b, "rejected")
    assert evidence_repo.conclusion_fact_links(pid) == {conclusion_id: [(a, "active"), (b, "rejected")]}


def test_status_changes_keep_the_row(pid):
    card = _card(pid)
    fact = _fact(pid, card, "A")
    conclusion_id = evidence_repo.create_conclusion(pid, "4", card, "C", [fact])
    evidence_repo.set_fact_status(fact, "rejected")
    evidence_repo.set_conclusion_status(conclusion_id, "rejected")
    assert evidence_repo.get_fact(fact)["status"] == "rejected"
    assert evidence_repo.get_conclusion(conclusion_id)["status"] == "rejected"


def test_list_facts_joins_page_and_research_oldest_first(pid):
    card = _card(pid, title="Fees")
    source, _ = evidence_repo.get_or_create_source(pid, "https://a.example", "A page")
    first = _fact(pid, card, "First", cited_source_id=source, as_of="2026")
    second = _fact(pid, _card(pid, phase="2", title="Students"), "Second", phase="2")
    rows = evidence_repo.list_facts(pid)
    assert [r["id"] for r in rows] == [first, second]
    assert (rows[0]["source_url"], rows[0]["source_title"], rows[0]["card_title"], rows[0]["as_of"]) == (
        "https://a.example", "A page", "Fees", "2026")
    assert rows[1]["source_url"] is None
    assert [r["id"] for r in evidence_repo.list_facts(pid, "2")] == [second]


def test_list_conclusions_by_phase(pid):
    card = _card(pid)
    fact = _fact(pid, card, "A")
    four = evidence_repo.create_conclusion(pid, "4", card, "Four", [fact])
    two = evidence_repo.create_conclusion(pid, "2", _card(pid, phase="2"), "Two", [fact])
    assert [r["id"] for r in evidence_repo.list_conclusions(pid)] == [four, two]
    assert [r["text"] for r in evidence_repo.list_conclusions(pid, "4")] == ["Four"]
    assert evidence_repo.list_conclusions(pid, "4")[0]["card_title"] == "Fees"


def test_search_matches_every_word_in_claim_quote_or_page_title(pid):
    card = _card(pid)
    source, _ = evidence_repo.get_or_create_source(pid, "https://deakin.example", "Deakin fees 2026")
    by_title = _fact(pid, card, "Units cost $3,000", cited_source_id=source)
    by_quote = _fact(pid, card, "Census dates vary", quote="Deakin census in March")
    rejected = _fact(pid, card, "Deakin is cheap")
    evidence_repo.set_fact_status(rejected, "rejected")
    assert [r["id"] for r in evidence_repo.search_facts(pid, ["deakin"])] == [by_quote, by_title]
    assert [r["id"] for r in evidence_repo.search_facts(pid, ["DEAKIN", "census"])] == [by_quote]
    assert evidence_repo.search_facts(pid, ["deakin"], phase_key="2") == []
    assert len(evidence_repo.search_facts(pid, [], limit=1)) == 1


def test_search_treats_percent_and_underscore_as_plain_characters(pid):
    card = _card(pid)
    _fact(pid, card, "Fees rose 5% in 2026")
    _fact(pid, card, "Fees rose 5 points")
    assert [r["claim"] for r in evidence_repo.search_facts(pid, ["5%"])] == ["Fees rose 5% in 2026"]
    assert evidence_repo.search_facts(pid, ["_"]) == []


def test_known_facts_counts_active_facts_and_lists_the_newest(pid):
    card = _card(pid)
    for i in range(12):
        _fact(pid, card, f"Fact {i}")
    gone = _fact(pid, card, "Rejected one")
    evidence_repo.set_fact_status(gone, "rejected")
    known = evidence_repo.known_facts(pid, per_phase=10)
    assert set(known) == {"4"}
    assert known["4"]["count"] == 12
    assert known["4"]["recent"][:2] == ["Fact 11", "Fact 10"] and len(known["4"]["recent"]) == 10
    assert "Rejected one" not in known["4"]["recent"]


def test_a_run_can_be_claimed_for_fact_extraction_once(pid):
    research_runs_repo.create("run_1", pid, None, None, "p")
    assert research_runs_repo.claim_facts_extraction("run_1") is True
    assert research_runs_repo.claim_facts_extraction("run_1") is False
    assert research_runs_repo.get("run_1")["facts_extracted_at"]
    research_runs_repo.release_facts_extraction("run_1")
    assert research_runs_repo.get("run_1")["facts_extracted_at"] is None
    assert research_runs_repo.claim_facts_extraction("run_1") is True


def _claimed_minutes_ago(minutes, run_id="run_1", pid=None):
    research_runs_repo.create(run_id, pid, None, None, "p")
    when = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    research_runs_repo.update(run_id, facts_extracted_at=when)


def test_a_stale_claim_with_no_facts_can_be_claimed_again(pid):
    assert research_runs_repo.STALE_EXTRACTION_MINUTES == 15
    _claimed_minutes_ago(16, pid=pid)
    assert research_runs_repo.claim_facts_extraction("run_1") is True
    assert research_runs_repo.claim_facts_extraction("run_1") is False


def test_a_stale_claim_on_a_run_that_has_a_fact_stays_claimed(pid):
    card = _card(pid)
    _claimed_minutes_ago(16, pid=pid)
    _fact(pid, card, "From that report", run_id="run_1")
    assert research_runs_repo.claim_facts_extraction("run_1") is False


def test_a_recent_claim_cannot_be_claimed_again(pid):
    _claimed_minutes_ago(1, pid=pid)
    assert research_runs_repo.claim_facts_extraction("run_1") is False
