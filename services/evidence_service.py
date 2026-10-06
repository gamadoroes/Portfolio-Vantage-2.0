# services/evidence_service.py
"""Facts and conclusions as the screens and tools show them. All SQL is in db/repositories/evidence_repo.py."""

from db.repositories import evidence_repo, projects_repo

# The facts briefing each phase's Generate gets (spec section 3): about 3,000 tokens at most.
MAX_PHASE_BRIEF_CHARS = 12000
# Kept back for each section's closing line ("… n more … not shown" or "(none)"), so a clipped brief still fits.
_CLOSING_LINE_ROOM = 60
REJECTED_HEADING = "# REJECTED — DO NOT USE"
BRIEF_RULES = (
    "RULES:\n"
    "- Cite every factual claim with the marker of the fact or conclusion it rests on: [F#] for a fact, "
    "[C#] for a conclusion. Several may sit together, e.g. [F26] [F28].\n"
    "- Mark anything taken only from the attached reports, and not from these facts, \"(not fact-checked)\".\n"
    "- Never use a fact listed under REJECTED — DO NOT USE.\n"
    "- Keep every marker exactly as written: square brackets, the letter and the number."
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


def _flat(text):
    """One line: line breaks and runs of spaces become single spaces, so no item can start a heading."""
    return " ".join(str(text or "").split())


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
    zero even if no fact is active, so a phase whose facts were all rejected still says what not to use."""
    facts = evidence_repo.list_facts(project_id, phase_key)
    active = sorted((row for row in facts if row["status"] == "active"), key=lambda row: row["id"], reverse=True)
    rejected = [row for row in facts if row["status"] == "rejected"]
    conclusions = [row for row in evidence_repo.list_conclusions(project_id, phase_key) if row["status"] == "active"]
    links = evidence_repo.conclusion_fact_links(project_id) if conclusions else {}

    facts_heading = f"# CHECKED FACTS FOR PHASE {phase_key} (newest first)"
    fixed = (len(facts_heading) + len("# CONCLUSIONS") + len(REJECTED_HEADING) + len(BRIEF_RULES)
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
        BRIEF_RULES,
    ])
    return {"text": text, "fact_count": len(active), "conclusion_count": len(conclusions), "shown_facts": shown,
            "rejected_count": len(rejected)}
