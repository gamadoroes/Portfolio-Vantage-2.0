from datetime import datetime, timedelta
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from db.repositories import (
    agent_decisions_repo,
    evidence_repo,
    projects_repo,
    research_runs_repo,
    research_work_items_repo,
    tool_calls_repo,
)
from services import research_task_service, supervisor_service, tools
from services.tools import research_facts

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


def test_a_rejected_claim_with_a_bad_address_is_skipped_once_and_not_called_saved(pid):
    card = _card(pid)
    (fact_id,) = _record(card, [FACT]).data["fact_ids"]
    tools.run_tool("update_evidence_status", "user", "P", {"kind": "fact", "id": fact_id, "action": "reject"})
    result = _record(card, [dict(FACT, source_url="javascript:alert(1)")]).data
    assert result["skipped"] == [{"item": "fact 1", "reason": "It matches a fact you rejected.", "saved": False}]


def _break(monkeypatch, name):
    def boom(ctx, inputs):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(tools.get_tool(name), "handler", boom)


def test_a_failing_save_gives_a_plain_reason_and_the_batch_carries_on(pid, monkeypatch):
    _break(monkeypatch, "save_evidence")
    result = _record(_card(pid), [FACT, FACT2]).data
    assert result["fact_ids"] == []
    assert result["skipped"] == [{"item": f"fact {n}", "reason": "It could not be saved.", "saved": False}
                                 for n in (1, 2)]


def test_a_failing_conclusion_is_skipped_plainly_and_its_facts_stay_saved(pid, monkeypatch):
    _break(monkeypatch, "create_finding")
    result = _record(_card(pid), [FACT], [{"text": "Pricey", "fact_numbers": [1]}]).data
    assert len(result["fact_ids"]) == 1 and result["conclusion_ids"] == []
    assert result["skipped"] == [{"item": "conclusion 1", "reason": "It could not be saved.", "saved": False}]


def test_a_non_object_conclusion_is_skipped_and_the_others_are_saved(pid):
    result = _record(_card(pid), [FACT], ["x", {"text": "Pricey", "fact_numbers": [1]}]).data
    assert len(result["conclusion_ids"]) == 1
    assert result["skipped"] == [{"item": "conclusion 1", "reason": "It has no text.", "saved": False}]


def test_zero_and_negative_fact_numbers_are_ignored(pid):
    result = _record(_card(pid), [FACT, FACT2], [{"text": "Second only", "fact_numbers": "-1, 0, fact 2"}]).data
    (conclusion_id,) = result["conclusion_ids"]
    links = evidence_repo.conclusion_fact_links(pid)[conclusion_id]
    assert [fact_id for fact_id, _ in links] == [result["fact_ids"][1]]


# ---- extract_research_facts: one paid call that only extracts facts ----

REPORT = "Sources (1):\n- Deakin fees - https://deakin.edu.au/fees\n\nDeakin charges $3,000 per unit in 2026."


class _Block:
    def __init__(self, name, input):
        self.type, self.name, self.input = "tool_use", name, input


class _FakeClient:
    def __init__(self, blocks=(), error=None):
        self.calls, self._blocks, self._error = [], list(blocks), error
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(content=self._blocks)


@pytest.fixture
def app_context(pid):
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        yield flask_app


def _install(monkeypatch, client):
    client.built_with = []
    monkeypatch.setattr(supervisor_service.anthropic, "Anthropic",
                        lambda api_key, **options: client.built_with.append(options) or client)
    return client


def _finished(pid, status="COMPLETE", method="TARGETED_WEB", phase="4", output=REPORT, title="Fees"):
    card = _card(pid, phase=phase, method=method, title=title)
    research_work_items_repo.update_fields(card, status=status, completeness_score=0.8, evidence_score=0.7)
    research_runs_repo.create(f"run_{card}", pid, "resp", None, "p")
    research_runs_repo.update(f"run_{card}", research_work_item_id=card, status="completed", output_text=output)
    return card


def _answer(facts=(FACT,), conclusions=()):
    return _Block("record_research_facts", {"facts": list(facts), "conclusions": list(conclusions)})


def _extract(card, caller="user"):
    return tools.run_tool("extract_research_facts", caller, "P", {"task_id": card})


def test_extract_saves_facts_without_touching_the_review(app_context, pid, monkeypatch):
    card = _finished(pid)
    client = _install(monkeypatch, _FakeClient([_answer(conclusions=[{"text": "Deakin is pricey", "fact_numbers": [1]}])]))
    result = _extract(card)
    assert result.ok and len(result.data["fact_ids"]) == 1 and len(result.data["conclusion_ids"]) == 1
    row = research_work_items_repo.get(card)
    assert (row["status"], row["completeness_score"], row["evidence_score"]) == ("COMPLETE", 0.8, 0.7)
    assert research_runs_repo.get(f"run_{card}")["facts_extracted_at"]
    call = client.calls[0]
    assert [t["name"] for t in call["tools"]] == ["record_research_facts"]
    assert call["tool_choice"] == {"type": "tool", "name": "record_research_facts"}
    assert call["max_tokens"] == 6000
    assert "Deakin charges $3,000 per unit in 2026." in call["messages"][0]["content"]
    assert client.built_with == [{"timeout": 240, "max_retries": 1}]  # the button's call has the same time limit
    child = next(r for r in tool_calls_repo.list_for_project(pid) if r["tool"] == "record_research_facts")
    assert (child["caller"], child["parent_call_id"]) == ("supervisor", result.call_id)
    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid)[:2]] == [
        "user_extract_facts", "facts_recorded"]


def test_the_system_extracts_after_a_review_without_a_you_line(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(card, caller="system").ok
    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid)] == ["facts_recorded"]


def test_a_second_click_does_not_call_claude_again(app_context, pid, monkeypatch):
    card = _finished(pid)
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(card).ok
    second = _extract(card)
    assert second.error["code"] == "conflict" and len(client.calls) == 1


def test_a_click_while_extracting_is_refused_without_a_call(app_context, pid, monkeypatch):
    card = _finished(pid)
    research_runs_repo.claim_facts_extraction(f"run_{card}")  # the automatic step or another tab is extracting now
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(card).error["code"] == "conflict" and client.calls == []


def test_a_finished_extraction_is_marked_done(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(card).ok
    assert research_runs_repo.get(f"run_{card}")["facts_extracted_at"].startswith("done:")


def test_an_extraction_whose_facts_were_all_known_before_is_still_done(app_context, pid, monkeypatch):
    first, second = _finished(pid), _finished(pid, title="Fees again")
    client = _install(monkeypatch, _FakeClient([_answer(), _answer()]))
    assert _extract(first).ok
    again = _extract(second)  # the same claim: its fact carries the first report's run id
    assert again.ok and len(again.data["fact_ids"]) == 1
    assert evidence_repo.list_facts(pid)[0]["run_id"] == f"run_{first}"
    row = research_runs_repo.get(f"run_{second}")
    assert row["facts_extracted_at"].startswith("done:")
    later = datetime.now() + timedelta(hours=1)
    assert research_runs_repo.extraction_available(row, now=later) is False
    assert research_runs_repo.claim_facts_extraction(f"run_{second}") is False
    assert len(client.calls) == 2


def test_a_stale_claim_from_a_dead_call_offers_the_button_and_can_be_retaken(app_context, pid, monkeypatch):
    card = _finished(pid)
    old = (datetime.now() - timedelta(minutes=16)).isoformat()
    research_runs_repo.update(f"run_{card}", facts_extracted_at=old)
    run = research_runs_repo.find_latest_for_work_item(card)
    assert research_facts.can_extract_facts(research_work_items_repo.get(card), run) is True
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(card).ok and len(client.calls) == 1
    run = research_runs_repo.find_latest_for_work_item(card)
    assert research_facts.can_extract_facts(research_work_items_repo.get(card), run) is False


def test_a_fresh_claim_does_not_offer_the_button(app_context, pid):
    card = _finished(pid)
    research_runs_repo.claim_facts_extraction(f"run_{card}")
    run = research_runs_repo.find_latest_for_work_item(card)
    assert research_facts.can_extract_facts(research_work_items_repo.get(card), run) is False


def test_an_error_after_facts_were_saved_does_not_free_the_report(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_answer()]))

    def save_then_fail(project_name, task_id, parent_call_id=None):
        evidence_repo.get_or_create_fact(pid, "4", card, "Saved", "saved", run_id=f"run_{card}")
        raise RuntimeError("late failure")

    monkeypatch.setattr(supervisor_service, "extract_facts", save_then_fail)
    assert _extract(card).ok is False
    assert research_runs_repo.get(f"run_{card}")["facts_extracted_at"]  # not released: the facts are in
    assert research_runs_repo.claim_facts_extraction(f"run_{card}") is False


def test_a_failed_claude_call_clears_the_claim(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient(error=anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://example.invalid"))))
    assert _extract(card).error["code"] == "unavailable"
    assert research_runs_repo.get(f"run_{card}")["facts_extracted_at"] is None
    assert evidence_repo.list_facts(pid) == []


def test_an_answer_with_no_usable_facts_leaves_the_button(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_answer(facts=[{"claim": ""}])]))
    result = _extract(card)
    assert result.ok and result.data["fact_ids"] == []
    assert research_runs_repo.get(f"run_{card}")["facts_extracted_at"] is None


def test_a_garbled_answer_is_cleaned_before_saving(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_Block("record_research_facts", {
        "facts": '<parameter name="facts">[{"claim": "Deakin charges $3,000 per unit"}, {"claim": "cut',
        "task_id": 99999})]))
    result = _extract(card)
    assert result.ok and [f["claim"] for f in evidence_repo.list_facts(pid)] == ["Deakin charges $3,000 per unit"]
    assert evidence_repo.list_facts(pid)[0]["research_work_item_id"] == card  # the model cannot pick the research


def test_a_click_that_saves_nothing_leaves_no_you_line(app_context, pid, monkeypatch):
    card = _finished(pid)
    _install(monkeypatch, _FakeClient([_answer(facts=[{"claim": ""}])]))
    assert _extract(card).ok
    assert [d["decision_type"] for d in agent_decisions_repo.list_for_project(pid)] == ["facts_recorded"]  # no "You" line


@pytest.mark.parametrize("make_card", [
    lambda pid: _finished(pid, status="RUNNING"),
    lambda pid: _finished(pid, method="SYNTHESIS", phase="7"),
    lambda pid: _finished(pid, output=""),
    lambda pid: _card(pid),
])
def test_extract_needs_a_finished_research_with_a_report(app_context, pid, monkeypatch, make_card):
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(make_card(pid)).error["code"] == "conflict" and client.calls == []


def test_the_supervisor_cannot_start_an_extraction(app_context, pid, monkeypatch):
    client = _install(monkeypatch, _FakeClient([_answer()]))
    assert _extract(_finished(pid), caller="supervisor").error["code"] == "not_allowed" and client.calls == []
