import re

import pytest

from db.repositories import evidence_repo, projects_repo, research_work_items_repo
from services import evidence_service


@pytest.fixture
def pid(temp_db):
    return projects_repo.get_or_create_id("P")


def _card(pid, phase="1", title="Landscape"):
    return research_work_items_repo.create(pid, phase, title)


def _fact(pid, card, claim, phase="1", source=None, **kw):
    """source is (url, title, publisher)."""
    if source:
        kw["cited_source_id"] = evidence_repo.get_or_create_source(pid, *source)[0]
    return evidence_repo.get_or_create_fact(pid, phase, card, claim, claim.lower(), **kw)[0]


# ---- the facts briefing ----

def test_the_brief_lists_facts_newest_first_then_conclusions_then_rejected_then_rules(pid):
    card = _card(pid)
    old = _fact(pid, card, "Old claim", source=("https://a.example/p", "Page A", "Uni A"), as_of="2025")
    new = _fact(pid, card, "New claim")
    gone = _fact(pid, card, "Wrong claim")
    evidence_repo.set_fact_status(gone, "rejected")
    conclusion = evidence_repo.create_conclusion(pid, "1", card, "A conclusion", [old, new, gone])
    brief = evidence_service.phase_brief(pid, "1")
    assert (brief["fact_count"], brief["conclusion_count"], brief["shown_facts"]) == (2, 1, 2)
    assert brief["rejected_count"] == 1
    assert brief["text"].split("\n") == [
        "# CHECKED FACTS FOR PHASE 1 (newest first)",
        f"[F{new}] New claim",
        f"[F{old}] Old claim — Page A, Uni A, as of 2025",
        "",
        "# CONCLUSIONS",
        f"[C{conclusion}] A conclusion (based on F{old}, F{new})",
        "",
        "# REJECTED — DO NOT USE",
        "- Wrong claim",
        "",
        *evidence_service.BRIEF_RULES.split("\n"),
    ]


def test_the_rules_say_how_to_cite(pid):
    rules = evidence_service.BRIEF_RULES
    assert rules.startswith("RULES:")
    for words in ("[F#]", "[C#]", "(not fact-checked)", "Never use a fact listed under REJECTED", "exactly as written"):
        assert words in rules
    # No real-looking ids: a model that echoes the example must not cite facts that do not exist.
    assert not re.search(r"\[[FC]\d", rules)


def test_a_phase_with_nothing_says_none_in_every_section(pid):
    brief = evidence_service.phase_brief(pid, "3")
    assert (brief["fact_count"], brief["conclusion_count"], brief["shown_facts"]) == (0, 0, 0)
    assert brief["rejected_count"] == 0
    sections = brief["text"].split("\n\n")
    assert sections[:3] == ["# CHECKED FACTS FOR PHASE 3 (newest first)\n(none)", "# CONCLUSIONS\n(none)",
                            "# REJECTED — DO NOT USE\n(none)"]
    assert evidence_service.phase_brief(None, "3")["fact_count"] == 0     # a project with no database row
    assert evidence_service.phase_brief(None, "3")["rejected_count"] == 0


def test_rejected_facts_and_conclusions_never_appear_as_citable(pid):
    card = _card(pid)
    kept = _fact(pid, card, "Kept claim")
    gone = _fact(pid, card, "Gone claim")
    evidence_repo.set_fact_status(gone, "rejected")
    dropped = evidence_repo.create_conclusion(pid, "1", card, "Dropped conclusion", [kept])
    evidence_repo.set_conclusion_status(dropped, "rejected")
    brief = evidence_service.phase_brief(pid, "1")
    facts_section, conclusions_section, rejected_section = brief["text"].split("\n\n")[:3]
    assert f"[F{gone}]" not in brief["text"] and "Gone claim" not in facts_section
    assert "- Gone claim" in rejected_section
    assert "Dropped conclusion" not in brief["text"] and conclusions_section == "# CONCLUSIONS\n(none)"
    assert brief["conclusion_count"] == 0


def test_only_this_phase_and_this_project(pid):
    _fact(pid, _card(pid), "Phase one claim")
    _fact(pid, _card(pid, phase="2"), "Phase two claim", phase="2")
    other = projects_repo.get_or_create_id("Other")
    _fact(other, _card(other), "Other project claim")
    text = evidence_service.phase_brief(pid, "1")["text"]
    assert "Phase one claim" in text
    assert "Phase two claim" not in text and "Other project claim" not in text


def test_every_item_is_one_line_so_none_can_start_a_heading(pid):
    card = _card(pid)
    fact = _fact(pid, card, "Line one\n# Not a heading\n\nline   three",
                 source=("https://a.example", "Title\n# with break", None))
    gone = _fact(pid, card, "Rejected\n# heading")
    evidence_repo.set_fact_status(gone, "rejected")
    evidence_repo.create_conclusion(pid, "1", card, "Conclusion\n## heading", [fact])
    text = evidence_service.phase_brief(pid, "1")["text"]
    assert [line for line in text.split("\n") if line.startswith("#")] == [
        "# CHECKED FACTS FOR PHASE 1 (newest first)", "# CONCLUSIONS", "# REJECTED — DO NOT USE"]
    assert f"[F{fact}] Line one # Not a heading line three — Title # with break" in text


def test_a_long_fact_list_is_cut_newest_first_within_the_budget(pid):
    card = _card(pid)
    ids = [_fact(pid, card, f"Claim {n:03d} " + "x" * 150) for n in range(200)]
    gone = _fact(pid, card, "Rejected claim")
    evidence_repo.set_fact_status(gone, "rejected")
    evidence_repo.create_conclusion(pid, "1", card, "The conclusion", ids[:2])
    brief = evidence_service.phase_brief(pid, "1")
    text = brief["text"]
    assert len(text) <= evidence_service.MAX_PHASE_BRIEF_CHARS
    assert brief["fact_count"] == 200 and 0 < brief["shown_facts"] < 200
    facts_section = text.split("\n\n")[0].split("\n")
    shown = facts_section[1:-1]
    assert [line.split("]")[0] for line in shown] == [f"[F{i}" for i in reversed(ids)][:brief["shown_facts"]]
    assert facts_section[-1] == f"… {200 - brief['shown_facts']} more facts not shown"
    assert "The conclusion" in text and "- Rejected claim" in text and evidence_service.BRIEF_RULES in text


def test_conclusions_and_the_rejected_list_come_first_and_are_clipped_only_if_they_alone_are_too_long(pid):
    card = _card(pid)
    fact = _fact(pid, card, "A long fact " + "w" * 640)
    for n in range(40):
        evidence_repo.create_conclusion(pid, "1", card, f"Conclusion {n} " + "y" * 590, [fact])
    gone = _fact(pid, card, "Rejected claim")
    evidence_repo.set_fact_status(gone, "rejected")
    brief = evidence_service.phase_brief(pid, "1")
    text = brief["text"]
    assert len(text) <= evidence_service.MAX_PHASE_BRIEF_CHARS
    assert "- Rejected claim" in text                       # the rejected list is never squeezed out by conclusions
    assert "more conclusions not shown" in text
    assert text.split("\n\n")[0].split("\n")[1:] == ["… 1 more fact not shown"]
    assert brief["shown_facts"] == 0 and brief["conclusion_count"] == 40
    assert text.endswith(evidence_service.BRIEF_RULES)


def test_a_huge_rejected_list_is_clipped_and_the_brief_still_fits(pid):
    card = _card(pid)
    for n in range(100):
        gone = _fact(pid, card, f"Rejected {n:03d} " + "r" * 200)
        evidence_repo.set_fact_status(gone, "rejected")
    brief = evidence_service.phase_brief(pid, "1")
    assert len(brief["text"]) <= evidence_service.MAX_PHASE_BRIEF_CHARS
    rejected = brief["text"].split("\n\n")[2].split("\n")
    assert rejected[0] == "# REJECTED — DO NOT USE" and rejected[-1].endswith("more rejected facts not shown")
    assert brief["text"].endswith(evidence_service.BRIEF_RULES)
    assert brief["rejected_count"] == 100                    # the count is of every rejected fact, shown or cut


# ---- the rejected count (tells Generate that a phase with no usable facts still has claims not to use) ----

def test_rejected_count_counts_only_this_phases_rejected_facts_of_this_project(pid):
    card = _card(pid)
    other_phase_card = _card(pid, phase="2")
    for claim in ("Gone one", "Gone two"):
        evidence_repo.set_fact_status(_fact(pid, card, claim), "rejected")
    _fact(pid, card, "Still good")                                        # active: not counted
    evidence_repo.set_fact_status(_fact(pid, other_phase_card, "Phase two gone", phase="2"), "rejected")
    other = projects_repo.get_or_create_id("Other")
    evidence_repo.set_fact_status(_fact(other, _card(other), "Other project gone"), "rejected")
    brief = evidence_service.phase_brief(pid, "1")
    assert (brief["fact_count"], brief["rejected_count"]) == (1, 2)
    assert evidence_service.phase_brief(pid, "2")["rejected_count"] == 1
    assert evidence_service.phase_brief(pid, "3")["rejected_count"] == 0
    assert evidence_service.phase_brief(other, "1")["rejected_count"] == 1


def test_a_restored_fact_is_no_longer_counted_as_rejected(pid):
    card = _card(pid)
    fact = _fact(pid, card, "Changed my mind")
    evidence_repo.set_fact_status(fact, "rejected")
    assert evidence_service.phase_brief(pid, "1")["rejected_count"] == 1
    evidence_repo.set_fact_status(fact, "active")
    brief = evidence_service.phase_brief(pid, "1")
    assert (brief["fact_count"], brief["rejected_count"]) == (1, 0)


def test_a_phase_with_only_rejected_facts_still_lists_them_as_not_to_use(pid):
    card = _card(pid)
    for claim in ("First wrong claim", "Second wrong claim"):
        evidence_repo.set_fact_status(_fact(pid, card, claim), "rejected")
    brief = evidence_service.phase_brief(pid, "1")
    assert (brief["fact_count"], brief["conclusion_count"], brief["shown_facts"]) == (0, 0, 0)
    assert brief["rejected_count"] == 2
    sections = brief["text"].split("\n\n")
    assert sections[0] == "# CHECKED FACTS FOR PHASE 1 (newest first)\n(none)"
    assert sections[2].split("\n") == ["# REJECTED — DO NOT USE", "- First wrong claim", "- Second wrong claim"]
    assert brief["text"].endswith(evidence_service.BRIEF_RULES)


# ---- the Sources list ----

def test_markers_are_read_once_each_in_order_of_first_appearance():
    text = "A [F26] [F28] [F4]. B [C3] and [F26] again. [f5], [F], [X1] and F7 are not markers. [F007] is fact 7."
    assert evidence_service.cited_markers(text) == [("F", 26), ("F", 28), ("F", 4), ("C", 3), ("F", 7)]
    assert evidence_service.cited_markers("No markers here.") == []
    assert evidence_service.cited_markers(None) == []


def test_the_index_holds_every_fact_and_conclusion_of_the_project_whatever_its_status(pid):
    card = _card(pid)
    kept, gone = _fact(pid, card, "Kept"), _fact(pid, card, "Gone")
    evidence_repo.set_fact_status(gone, "rejected")
    conclusion = evidence_repo.create_conclusion(pid, "1", card, "Both", [kept, gone])
    other = projects_repo.get_or_create_id("Other")
    theirs = _fact(other, _card(other), "Theirs")
    index = evidence_service.citation_index("P")
    assert set(index["facts"]) == {kept, gone} and theirs not in index["facts"]
    assert index["facts"][gone]["status"] == "rejected"
    assert index["conclusions"] == {conclusion: {"id": conclusion, "text": "Both", "status": "active",
                                                 "fact_ids": [kept, gone]}}
    assert evidence_service.citation_index("Nope") == {"facts": {}, "conclusions": {}}
    assert evidence_service.citation_index(None) == {"facts": {}, "conclusions": {}}


def test_each_cited_item_gets_one_sources_line_in_order_of_first_appearance(pid):
    card = _card(pid)
    full = _fact(pid, card, "Fees", source=("https://deakin.example/fees", "Deakin fees", "Deakin University"),
                 as_of="2026")
    bare = _fact(pid, card, "No page", as_of="2025")
    titled = _fact(pid, card, "Titled", source=("https://b.example", "Page B", None))
    gone = _fact(pid, card, "Gone", source=("https://c.example", "Page C", "Uni C"))
    evidence_repo.set_fact_status(gone, "rejected")
    conclusion = evidence_repo.create_conclusion(pid, "1", card, "x" * 250, [full, bare])
    other = projects_repo.get_or_create_id("Other")
    theirs = _fact(other, _card(other), "Theirs")
    text = (f"[F{full}] a [C{conclusion}] b [F{bare}] [F{full}] [F{titled}] [F{gone}] [F99999] [F{theirs}] "
            "[C88888]")
    assert evidence_service.sources_lines(text, evidence_service.citation_index("P")) == [
        f"F{full} — Deakin University — Deakin fees (2026) — https://deakin.example/fees",
        f"C{conclusion} — Conclusion: {'x' * 199}… (based on F{full}, F{bare})",
        f"F{bare} — Research report: Landscape (2025)",
        f"F{titled} — Page B — https://b.example",
        f"F{gone} — Uni C — Page C — https://c.example (since rejected)",
        "F99999 — (not found)",
        f"F{theirs} — (not found)",
        "C88888 — (not found)",
    ]


def test_a_rejected_conclusion_says_so(pid):
    card = _card(pid)
    fact = _fact(pid, card, "A fact")
    conclusion = evidence_repo.create_conclusion(pid, "1", card, "Short\nconclusion", [fact])
    evidence_repo.set_conclusion_status(conclusion, "rejected")
    assert evidence_service.sources_lines(f"[C{conclusion}]", evidence_service.citation_index("P")) == [
        f"C{conclusion} — Conclusion: Short conclusion (based on F{fact}) (since rejected)"]


def test_no_markers_means_no_sources(pid):
    index = evidence_service.citation_index("P")
    assert evidence_service.sources_lines("Plain text.", index) == []
    assert evidence_service.sources_markdown("Plain text.", index) == ""
    assert evidence_service.sources_lines("[F1]", None) == ["F1 — (not found)"]


def test_the_sources_list_as_markdown(pid):
    fact = _fact(pid, _card(pid), "Fees", source=("https://a.example", "Page A", None))
    assert evidence_service.sources_markdown(f"Dear [F{fact}].", evidence_service.citation_index("P")) == (
        f"\n\n## Sources\n\n- F{fact} — Page A — https://a.example")
