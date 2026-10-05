import pytest

from db.repositories import evidence_repo, projects_repo, research_runs_repo, tool_calls_repo
from services import research_task_service, tools

FACT = {"claim": "Deakin charges $3,000 per unit", "quote": "$3,000 per unit", "source_url": "https://deakin.edu.au/fees",
        "source_title": "Deakin fees", "publisher": "Deakin University", "published_date": "2026-02-01", "as_of": "2026"}
FACT2 = {"claim": "Monash runs three intakes", "source_url": "https://monash.edu/intakes", "source_title": "Monash intakes"}


@pytest.fixture
def pid(temp_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return projects_repo.get_or_create_id("P")


def _card(pid, phase="4", method="TARGETED_WEB", title="Fees"):
    return research_task_service.create_task(pid, phase, title, research_method=method)


def _record(card, facts=(), conclusions=(), caller="supervisor"):
    inputs = {"task_id": card, "facts": facts if isinstance(facts, dict) else list(facts),
              "conclusions": list(conclusions)}
    return tools.run_tool("record_research_facts", caller, "P", inputs)


def test_a_batch_saves_facts_pages_and_conclusions_as_logged_child_calls(pid):
    card = _card(pid)
    research_runs_repo.create("run_1", pid, None, None, "p")
    research_runs_repo.update("run_1", research_work_item_id=card, status="completed", output_text="Report")
    result = _record(card, [FACT, FACT2], [{"text": "Fees and intakes differ", "fact_numbers": [1, 2]}])
    assert result.ok and result.data["skipped"] == []
    assert len(result.data["fact_ids"]) == 2 and len(result.data["conclusion_ids"]) == 1
    first = evidence_repo.get_fact(result.data["fact_ids"][0])
    assert (first["run_id"], first["quote"], first["as_of"]) == ("run_1", "$3,000 per unit", "2026")
    source = evidence_repo.get_source(first["cited_source_id"])
    assert (source["url"], source["publisher"], source["published_date"]) == (
        "https://deakin.edu.au/fees", "Deakin University", "2026-02-01")
    children = [r["tool"] for r in reversed(tool_calls_repo.list_for_project(pid)) if r["parent_call_id"] == result.call_id]
    assert children == ["save_source", "save_evidence", "save_source", "save_evidence", "create_finding"]


def test_one_bad_fact_is_skipped_with_a_reason_and_the_rest_are_saved(pid):
    result = _record(_card(pid), [FACT, {"claim": ""}, "just a sentence", 42, {"quote": "no claim"}])
    assert len(result.data["fact_ids"]) == 1
    assert result.data["skipped"] == [{"item": f"fact {n}", "reason": "It has no claim.", "saved": False}
                                      for n in (2, 3, 4, 5)]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "www.deakin.edu.au", "ftp://deakin.edu.au/fees"])
def test_a_bad_page_address_saves_the_fact_without_its_page(pid, url):
    result = _record(_card(pid), [dict(FACT, source_url=url)])
    (fact_id,) = result.data["fact_ids"]
    assert evidence_repo.get_fact(fact_id)["cited_source_id"] is None
    (skip,) = result.data["skipped"]
    assert skip["item"] == "fact 1" and skip["saved"] is True
    assert skip["reason"].startswith("Saved without its source")


def test_long_text_is_clipped_not_refused(pid):
    result = _record(_card(pid), [dict(FACT, claim="c" * 900, quote="q" * 900, source_title="t" * 400, as_of=2026)])
    row = evidence_repo.get_fact(result.data["fact_ids"][0])
    assert (len(row["claim"]), len(row["quote"]), row["as_of"]) == (500, 500, "2026")
    assert len(evidence_repo.get_source(row["cited_source_id"])["title"]) == 300


def test_a_page_with_no_title_is_named_by_its_address(pid):
    result = _record(_card(pid), [dict(FACT, source_title="")])
    row = evidence_repo.get_fact(result.data["fact_ids"][0])
    assert evidence_repo.get_source(row["cited_source_id"])["title"] == "https://deakin.edu.au/fees"


def test_a_conclusion_keeps_only_the_facts_that_were_saved(pid):
    result = _record(_card(pid), [FACT, {"claim": ""}, FACT2],
                     [{"text": "Fees and intakes differ", "fact_numbers": [1, 2, 3]}])
    (conclusion_id,) = result.data["conclusion_ids"]
    assert [fact_id for fact_id, _ in evidence_repo.conclusion_fact_links(pid)[conclusion_id]] == result.data["fact_ids"]


@pytest.mark.parametrize("conclusion,reason", [
    ({"text": "Nothing behind it", "fact_numbers": [2]}, "None of the facts it cites were saved."),
    ({"text": "Out of range", "fact_numbers": [9]}, "None of the facts it cites were saved."),
    ({"text": "No numbers"}, "None of the facts it cites were saved."),
    ({"fact_numbers": [1]}, "It has no text."),
])
def test_a_conclusion_with_no_saved_facts_is_skipped(pid, conclusion, reason):
    result = _record(_card(pid), [FACT, {"claim": ""}], [conclusion])
    assert result.data["conclusion_ids"] == []
    assert {"item": "conclusion 1", "reason": reason, "saved": False} in result.data["skipped"]


@pytest.mark.parametrize("numbers", ["1, 2", ["1", 2.0], [1, 2, 2]])
def test_fact_numbers_sent_loosely_are_understood(pid, numbers):
    result = _record(_card(pid), [FACT, FACT2], [{"text": "Both", "fact_numbers": numbers}])
    (conclusion_id,) = result.data["conclusion_ids"]
    assert len(evidence_repo.conclusion_fact_links(pid)[conclusion_id]) == 2


def test_a_duplicate_claim_counts_as_saved(pid):
    card = _card(pid)
    first = _record(card, [FACT]).data
    again = _record(card, [dict(FACT, claim="deakin charges $3,000 per unit.")]).data
    assert again["fact_ids"] == first["fact_ids"] and again["skipped"] == []


def test_a_claim_the_person_rejected_is_not_brought_back(pid):
    card = _card(pid)
    (fact_id,) = _record(card, [FACT]).data["fact_ids"]
    tools.run_tool("update_evidence_status", "user", "P", {"kind": "fact", "id": fact_id, "action": "reject"})
    result = _record(card, [FACT], [{"text": "Pricey", "fact_numbers": [1]}]).data
    assert result["fact_ids"] == [] and result["conclusion_ids"] == []
    assert {"item": "fact 1", "reason": "It matches a fact you rejected.", "saved": False} in result["skipped"]
    assert evidence_repo.get_fact(fact_id)["status"] == "rejected"


def test_one_object_instead_of_a_list_is_accepted(pid):
    assert len(_record(_card(pid), FACT).data["fact_ids"]) == 1


def test_a_batch_holds_at_most_25_facts_and_3_conclusions(pid):
    card = _card(pid)
    assert _record(card, [FACT] * 26).error["code"] == "invalid_input"
    assert _record(card, [FACT], [{"text": "c", "fact_numbers": [1]}] * 4).error["code"] == "invalid_input"


def test_the_options_report_is_not_mined_for_facts(pid):
    result = _record(_card(pid, phase="7", method="SYNTHESIS", title="Options"), [FACT])
    assert result.error["code"] == "conflict" and evidence_repo.list_facts(pid) == []


@pytest.mark.parametrize("caller", ["user", "system"])
def test_only_the_supervisor_records_a_batch(pid, caller):
    assert _record(_card(pid), [FACT], caller=caller).error["code"] == "not_allowed"
