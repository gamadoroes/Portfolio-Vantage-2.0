from docx import Document

from services import docx_service, evidence_service

FACT_12 = {"id": 12, "status": "active", "as_of": "2026", "card_title": "Fees",
           "source": {"url": "https://deakin.example/fees", "title": "Deakin fees", "publisher": "Deakin University",
                      "published_date": ""}}
FACT_13 = {"id": 13, "status": "rejected", "as_of": "", "card_title": "Fees",
           "source": {"url": "https://old.example", "title": "Old page", "publisher": "", "published_date": ""}}
CITATIONS = {"facts": {12: FACT_12, 13: FACT_13},
             "conclusions": {3: {"id": 3, "text": "Deakin is the priciest", "status": "active", "fact_ids": [12]}}}


def _phase(title, summary):
    return {"title": title, "summary": summary, "confidence": "medium", "evidence_sources": [], "gaps": []}


def _report(phases, citations=CITATIONS):
    buf = docx_service.generate_insights_report({"competitors": [], "phases": phases}, "P", citations=citations)
    return [p.text for p in Document(buf).paragraphs]


def _section(texts, start, end=None):
    first = texts.index(start)
    return texts[first:texts.index(end)] if end else texts[first:]


def test_a_fact_cited_phase_ends_with_its_sources_in_order_of_first_appearance():
    texts = _report({"1": _phase("The Landscape", "Fees rose [F12]. Deakin leads [C3] [F12]. Old view [F13] [F99].")})
    phase = _section(texts, "Phase 1: The Landscape")
    assert any("Fees rose [F12]." in t for t in phase)          # the markers stay in the body text
    start = phase.index("Cited sources")
    assert phase[start + 1:start + 5] == [
        "F12 — Deakin University — Deakin fees (2026) — https://deakin.example/fees",
        "C3 — Conclusion: Deakin is the priciest (based on F12)",
        "F13 — Old page — https://old.example (since rejected)",
        "F99 — (not found)",
    ]
    assert evidence_service.NOT_CITED_NOTE not in phase


def test_a_phase_without_markers_gets_the_note_and_no_sources():
    texts = _report({"1": _phase("The Landscape", "Cited [F12]."),
                     "2": _phase("The Student", "Written from the reports."),
                     "3": _phase("Review of Marketing", "MISSING")})
    two = _section(texts, "Phase 2: The Student", "Phase 3: Review of Marketing")
    assert evidence_service.NOT_CITED_NOTE in two and "Cited sources" not in two
    assert two.index(evidence_service.NOT_CITED_NOTE) < two.index("Written from the reports.")   # above the text
    three = _section(texts, "Phase 3: Review of Marketing", "Data Completeness Overview")
    assert evidence_service.NOT_CITED_NOTE not in three and "Cited sources" not in three


def test_without_a_lookup_every_marker_is_not_found():
    texts = _report({"1": _phase("The Landscape", "Cited [F12].")}, citations=None)
    assert "F12 — (not found)" in texts


def test_a_phase_with_only_spaces_gets_neither_the_note_nor_sources():
    texts = _report({"1": _phase("The Landscape", "Cited [F12]."), "2": _phase("The Student", "   \n  ")})
    two = _section(texts, "Phase 2: The Student", "Data Completeness Overview")
    assert evidence_service.NOT_CITED_NOTE not in two and "Cited sources" not in two
    assert any(t.startswith("No data available for this phase") for t in two)


def test_odd_characters_in_a_fact_do_not_stop_the_report():
    fact = {**FACT_12, "source": {**FACT_12["source"], "title": "a\x00b", "publisher": "c\x1bd\ufffe"}}
    texts = _report({"1": _phase("The Landscape", "Fees rose [F12].")}, citations={"facts": {12: fact}})
    assert "F12 — cd — ab (2026) — https://deakin.example/fees" in texts
