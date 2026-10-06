# services/evidence_service.py
"""Facts and conclusions as the screens and tools show them. All SQL is in db/repositories/evidence_repo.py."""

import re

from db.repositories import evidence_repo, projects_repo

# The facts briefing each phase's Generate gets (spec section 3): about 3,000 tokens at most.
MAX_PHASE_BRIEF_CHARS = 12000
# Kept back for each section's closing line ("… n more … not shown" or "(none)"), so a clipped brief still fits.
_CLOSING_LINE_ROOM = 60
REJECTED_HEADING = "# REJECTED — DO NOT USE"
BRIEF_RULES = (
    "RULES:\n"
    "- Cite every factual claim with the marker of the fact or conclusion it rests on: [F#] for a fact, "
    "[C#] for a conclusion. Several may sit together, e.g. [F#] [F#].\n"
    "- Mark anything taken only from the attached reports, and not from these facts, \"(not fact-checked)\".\n"
    "- Never use a fact listed under REJECTED — DO NOT USE.\n"
    "- Keep every marker exactly as written: square brackets, the letter and the number."
)
# For a phase with no active fact and no active conclusion (only rejected facts, or nothing): there is nothing to cite,
# so asking for a marker on every claim would only make Claude invent ones. Everything comes from the reports.
BRIEF_RULES_NO_FACTS = (
    "RULES:\n"
    "- There are no checked facts for this phase: mark every claim taken from the attached reports "
    "\"(not fact-checked)\".\n"
    "- Never use a fact listed under REJECTED — DO NOT USE.\n"
    "- Keep every marker exactly as written: square brackets, the letter and the number."
)

# [F12] cites fact 12, [C3] conclusion 3 (spec section 2).
# ASCII digits only, at most 9 (not \d, which also matches other scripts' digits and any length of run).
MARKER_RE = re.compile(r"\[([FC])([0-9]{1,9})\]")
SOURCE_CONCLUSION_CHARS = 200
NOT_CITED_NOTE = "Not fact-cited — written from the reports only."
KEEP_MARKERS_INSTRUCTION = (
    "Keep the [F#] and [C#] citation markers from the phase write-ups exactly as written, "
    "next to each point you take from them."
)


def fact_dict(row):
    """One fact from evidence_repo.list_facts / search_facts, as the Insights tab and the search tool show it."""
    source = None
    if row["source_url"]:
        source = {"url": row["source_url"], "title": row["source_title"] or "",
                  "publisher": row["source_publisher"] or "", "published_date": row["source_published_date"] or ""}
    return {
        "id": row["id"], "phase_key": row["phase_key"], "claim": row["claim"], "quote": row["quote"] or "",
        "as_of": row["as_of"] or "", "status": row["status"], "source": source,
        "card_id": row["research_work_item_id"], "card_title": row["card_title"] or "", "created_at": row["created_at"],
    }


def list_evidence(project_name, phase_key=None):
    """Every fact and conclusion of the project (rejected ones included and marked), grouped by phase."""
    project_id = projects_repo.get_id(project_name)
    if project_id is None:
        return {}
    phases = {}

    def slot(key):
        return phases.setdefault(key, {"facts": [], "conclusions": []})

    for row in evidence_repo.list_facts(project_id, phase_key):
        slot(row["phase_key"])["facts"].append(fact_dict(row))
    links = evidence_repo.conclusion_fact_links(project_id)
    for row in evidence_repo.list_conclusions(project_id, phase_key):
        cited = links.get(row["id"], [])
        slot(row["phase_key"])["conclusions"].append({
            "id": row["id"], "phase_key": row["phase_key"], "text": row["text"], "status": row["status"],
            "fact_ids": [fact_id for fact_id, _ in cited],
            "rejected_fact_count": sum(1 for _, status in cited if status == "rejected"),
            "card_id": row["research_work_item_id"], "card_title": row["card_title"] or "",
        })
    return phases


# Characters XML (so Word) cannot hold: control characters, lone surrogates and U+FFFE / U+FFFF. str.split() already
# turns \x0b, \x0c and \x1c-\x1f into spaces, so they are not listed here.
_XML_UNSAFE_RE = re.compile(r"[\x00-\x08\x0e-\x1b\ud800-\udfff\ufffe\uffff]")


def _flat(text):
    """One line: line breaks and runs of spaces become single spaces, so no item can start a heading. Characters
    Word cannot hold are dropped."""
    return " ".join(_XML_UNSAFE_RE.sub("", str(text or "")).split())


def _fit(lines, room, one, many):
    """(shown, printed lines, characters used). Whole lines, in order, while they fit in `room` characters (each
    with its line break). When some are cut, "… n more <many> not shown" closes the list; an empty list prints
    "(none)". Closing lines are paid for from _CLOSING_LINE_ROOM, not from `room`."""
    if not lines:
        return 0, ["(none)"], 0
    shown, used = 0, 0
    for line in lines:
        if used + len(line) + 1 > room:
            break
        shown += 1
        used += len(line) + 1
    printed = lines[:shown]
    cut = len(lines) - shown
    if cut:
        printed.append(f"… {cut} more {one if cut == 1 else many} not shown")
    return shown, printed, used


def _brief_fact_line(row):
    details = [part for part in (_flat(row["source_title"]), _flat(row["source_publisher"])) if part]
    if row["as_of"]:
        details.append(f"as of {_flat(row['as_of'])}")
    line = f"[F{row['id']}] {_flat(row['claim'])}"
    return f"{line} — {', '.join(details)}" if details else line


def _brief_conclusion_line(row, links):
    active = [f"F{fact_id}" for fact_id, status in links if status == "active"]
    line = f"[C{row['id']}] {_flat(row['text'])}"
    return f"{line} (based on {', '.join(active)})" if active else line


def phase_brief(project_id, phase_key):
    """The facts briefing for one phase's Generate (spec section 3): the phase's active facts (newest first), its
    active conclusions, the rejected claims not to use, and the citation rules, within MAX_PHASE_BRIEF_CHARS.
    The rejected list and then the conclusions get the room first; facts fill the rest, newest first.
    rejected_count is every rejected fact of the phase, shown or cut: Generate adds the briefing when it is above
    zero even if no fact is active, so a phase whose facts were all rejected still says what not to use. A phase with
    no active fact and no active conclusion gets BRIEF_RULES_NO_FACTS, which does not ask for markers."""
    facts = evidence_repo.list_facts(project_id, phase_key)
    active = sorted((row for row in facts if row["status"] == "active"), key=lambda row: row["id"], reverse=True)
    rejected = [row for row in facts if row["status"] == "rejected"]
    conclusions = [row for row in evidence_repo.list_conclusions(project_id, phase_key) if row["status"] == "active"]
    links = evidence_repo.conclusion_fact_links(project_id) if conclusions else {}

    rules = BRIEF_RULES if active or conclusions else BRIEF_RULES_NO_FACTS
    facts_heading = f"# CHECKED FACTS FOR PHASE {phase_key} (newest first)"
    fixed = (len(facts_heading) + len("# CONCLUSIONS") + len(REJECTED_HEADING) + len(rules)
             + 3 * len("\n\n") + 3 * _CLOSING_LINE_ROOM)
    room = MAX_PHASE_BRIEF_CHARS - fixed
    _, rejected_lines, used = _fit([f"- {_flat(row['claim'])}" for row in rejected], room,
                                   "rejected fact", "rejected facts")
    room -= used
    conclusion_lines = [_brief_conclusion_line(row, links.get(row["id"], [])) for row in conclusions]
    _, conclusion_lines, used = _fit(conclusion_lines, room, "conclusion", "conclusions")
    room -= used
    shown, fact_lines, _ = _fit([_brief_fact_line(row) for row in active], room, "fact", "facts")
    text = "\n\n".join([
        "\n".join([facts_heading, *fact_lines]),
        "\n".join(["# CONCLUSIONS", *conclusion_lines]),
        "\n".join([REJECTED_HEADING, *rejected_lines]),
        rules,
    ])
    return {"text": text, "fact_count": len(active), "conclusion_count": len(conclusions), "shown_facts": shown,
            "rejected_count": len(rejected)}


def cited_markers(text):
    """The [F#] / [C#] markers in a write-up as (kind, id) pairs, each once, in order of first appearance."""
    seen, found = set(), []
    for kind, digits in MARKER_RE.findall(text if isinstance(text, str) else ""):
        key = (kind, int(digits))
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def citation_index(project_name):
    """Every fact and conclusion of the project by id, whatever its status: what a Sources list looks markers up
    in. An unknown project (or none) has nothing, so every marker in its text reads "(not found)"."""
    project_id = projects_repo.get_id(project_name) if project_name else None
    if project_id is None:
        return {"facts": {}, "conclusions": {}}
    links = evidence_repo.conclusion_fact_links(project_id)
    return {
        "facts": {row["id"]: fact_dict(row) for row in evidence_repo.list_facts(project_id)},
        "conclusions": {
            row["id"]: {"id": row["id"], "text": row["text"], "status": row["status"],
                        "fact_ids": [fact_id for fact_id, _ in links.get(row["id"], [])]}
            for row in evidence_repo.list_conclusions(project_id)
        },
    }


def _clip(text, limit):
    flat = _flat(text)
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def _plain(text):
    return text


# Characters that give a Markdown reader link, image, HTML, emphasis or code syntax. The options report's Sources
# list is shown by that reader, and its text comes from web pages and models, so these are escaped there.
_MARKDOWN_SPECIALS_RE = re.compile(r"([\\\[\]()!<>*_`])")


def _markdown_text(text):
    """Text for a Markdown line: each special character gets a backslash, so a reader shows it as written."""
    return _MARKDOWN_SPECIALS_RE.sub(r"\\\1", text)


_URL_LINK_CHARACTERS = {"[": "%5B", "]": "%5D", "<": "%3C", ">": "%3E"}


def _markdown_url(url):
    """A page address left as plain text. A real address never holds [ ] < >, and without them it cannot open a
    link, an image or an HTML tag, so only those are written in percent form; every other address reads as before."""
    return "".join(_URL_LINK_CHARACTERS.get(char, char) for char in url)


def _fact_source_line(fact_id, fact, esc=_plain, esc_url=_plain):
    if fact is None:
        return f"F{fact_id} — (not found)"
    source = fact["source"] or {}
    title, publisher = esc(_flat(source.get("title"))), esc(_flat(source.get("publisher")))
    card = esc(_flat(fact["card_title"]))
    parts = [f"F{fact_id}", publisher or title or (f"Research report: {card}" if card else "Research report")]
    if publisher and title:
        parts.append(title)
    if fact["as_of"]:
        parts[-1] += f" ({esc(_flat(fact['as_of']))})"
    if source.get("url"):
        parts.append(esc_url(_flat(source["url"])))
    line = " — ".join(parts)
    return f"{line} (since rejected)" if fact["status"] == "rejected" else line


def _conclusion_source_line(conclusion_id, conclusion, esc=_plain):
    if conclusion is None:
        return f"C{conclusion_id} — (not found)"
    line = f"C{conclusion_id} — Conclusion: {esc(_clip(conclusion['text'], SOURCE_CONCLUSION_CHARS))}"
    if conclusion["fact_ids"]:
        line += f" (based on {', '.join(f'F{fact_id}' for fact_id in conclusion['fact_ids'])})"
    return f"{line} (since rejected)" if conclusion["status"] == "rejected" else line


def _source_lines(text, index, escape_text, escape_url):
    index = index or {}
    facts, conclusions = index.get("facts", {}), index.get("conclusions", {})
    return [
        _fact_source_line(item_id, facts.get(item_id), escape_text, escape_url) if kind == "F"
        else _conclusion_source_line(item_id, conclusions.get(item_id), escape_text)
        for kind, item_id in cited_markers(text)
    ]


def sources_lines(text, index):
    """The Sources list for a write-up (spec section 6): one line per distinct cited fact or conclusion, in order of
    first appearance, as plain text (the Word report prints it as it is). `index` is citation_index(project); None
    counts as a project with nothing."""
    return _source_lines(text, index, _plain, _plain)


def sources_markdown(text, index):
    """The same Sources list as a Markdown section to append to a saved report, or "" when nothing is cited. Titles,
    publishers and conclusion text have their Markdown characters escaped, so a hostile one cannot become a link or an
    image in the reader; a page address stays plain text (see _markdown_url)."""
    lines = _source_lines(text, index, _markdown_text, _markdown_url)
    if not lines:
        return ""
    return "\n\n## Sources\n\n" + "\n".join(f"- {line}" for line in lines)
