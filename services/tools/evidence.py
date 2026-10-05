# services/tools/evidence.py
"""Facts, the web pages they cite, and conclusions: saving them (Supervisor and system), searching them
(anyone) and rejecting or restoring them (the person only). Nothing is ever deleted; reject is a status."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from db.repositories import evidence_repo, research_runs_repo, research_work_items_repo

from .. import evidence_service
from .activity import record_user_action
from .registry import Tool, ToolError, register
from .types import CardId, RowId, Text, ToolInput

URL_CHARS, SOURCE_TITLE_CHARS, PUBLISHER_CHARS, DATE_CHARS = 2000, 300, 200, 40
CLAIM_CHARS, QUOTE_CHARS, AS_OF_CHARS = 500, 500, 40
CONCLUSION_CHARS, MAX_FACTS_PER_CONCLUSION = 600, 10
SEARCH_CHARS, MAX_SEARCH_WORDS, MAX_SEARCH_RESULTS = 200, 10, 20
RUN_ID_CHARS = 100
PhaseKey = Literal["1", "2", "3", "4", "5", "6", "7"]
SAVERS = frozenset({"supervisor", "system"})
ALL_CALLERS = frozenset({"supervisor", "user", "system"})


def is_web_address(url):
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def claim_key(claim):
    """A claim as compared for duplicates: lower case, single spaces, no trailing punctuation."""
    return " ".join(str(claim or "").lower().split()).rstrip(" .,;:!?")


class SaveSourceInput(ToolInput):
    url: Text(URL_CHARS, min_chars=1) = Field(description="The page's address, starting with http:// or https://.")
    title: Text(SOURCE_TITLE_CHARS, min_chars=1)
    publisher: Text(PUBLISHER_CHARS) = ""
    published_date: Text(DATE_CHARS) = ""

    @field_validator("url")
    @classmethod
    def _web_address(cls, value):
        if not is_web_address(value):
            raise ValueError("use a web address that starts with http:// or https://")
        return value


def save_source(ctx, inputs):
    source_id, created = evidence_repo.get_or_create_source(
        ctx.project_id, inputs.url, inputs.title,
        publisher=inputs.publisher or None, published_date=inputs.published_date or None,
    )
    return {"source_id": source_id, "created": created}


class SaveEvidenceInput(ToolInput):
    task_id: CardId
    claim: Text(CLAIM_CHARS, min_chars=1)
    quote: Text(QUOTE_CHARS) = ""
    source_id: RowId | None = None
    as_of: Text(AS_OF_CHARS) = ""
    run_id: Text(RUN_ID_CHARS) | None = None


def save_evidence(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    if not card["phase_key"]:
        raise ToolError("conflict", "This research has no phase, so its facts have nowhere to go.")
    if inputs.source_id is not None:
        source = evidence_repo.get_source(inputs.source_id)
        if source is None or source["project_id"] != ctx.project_id:
            raise ToolError("not_found", f"No such page: {inputs.source_id}", ["source_id"])
    if inputs.run_id is not None:
        run = research_runs_repo.get(inputs.run_id)
        if run is None or run["project_id"] != ctx.project_id or run["research_work_item_id"] != inputs.task_id:
            raise ToolError("not_found", f"No such run for this research: {inputs.run_id}", ["run_id"])
    key = claim_key(inputs.claim)
    if not key:
        raise ToolError("invalid_input", "The claim has no words.", ["claim"])
    fact_id, created = evidence_repo.get_or_create_fact(
        ctx.project_id, card["phase_key"], inputs.task_id, inputs.claim, key,
        quote=inputs.quote or None, cited_source_id=inputs.source_id, as_of=inputs.as_of or None,
        run_id=inputs.run_id,
    )
    return {"fact_id": fact_id, "created": created, "status": evidence_repo.get_fact(fact_id)["status"]}


class CreateFindingInput(ToolInput):
    task_id: CardId
    text: Text(CONCLUSION_CHARS, min_chars=1)
    fact_ids: list[RowId] = Field(min_length=1, max_length=MAX_FACTS_PER_CONCLUSION)


def create_finding(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    if not card["phase_key"]:
        raise ToolError("conflict", "This research has no phase, so its conclusions have nowhere to go.")
    fact_ids = list(dict.fromkeys(inputs.fact_ids))
    for fact_id in fact_ids:
        fact = evidence_repo.get_fact(fact_id)
        if fact is None or fact["project_id"] != ctx.project_id:
            raise ToolError("not_found", f"No such fact: {fact_id}", ["fact_ids"])
        if fact["status"] != "active":
            raise ToolError("conflict", f"Fact {fact_id} was rejected, so it can't support a conclusion.", ["fact_ids"])
    conclusion_id = evidence_repo.create_conclusion(ctx.project_id, card["phase_key"], inputs.task_id, inputs.text, fact_ids)
    return {"conclusion_id": conclusion_id}


class SearchInput(ToolInput):
    words: Text(SEARCH_CHARS) = Field(default="", description="Words to look for. Leave empty for the newest facts.")
    phase_key: PhaseKey | None = None
    limit: int = Field(default=10, ge=1, le=MAX_SEARCH_RESULTS)


def search_existing_evidence(ctx, inputs):
    words = inputs.words.split()[:MAX_SEARCH_WORDS]
    rows = evidence_repo.search_facts(ctx.project_id, words, inputs.phase_key, inputs.limit)
    return {"facts": [evidence_service.fact_dict(row) for row in rows]}


class StatusInput(ToolInput):
    kind: Literal["fact", "conclusion"]
    id: RowId
    action: Literal["reject", "restore"]


_KINDS = {
    "fact": (evidence_repo.get_fact, evidence_repo.set_fact_status),
    "conclusion": (evidence_repo.get_conclusion, evidence_repo.set_conclusion_status),
}


def update_evidence_status(ctx, inputs):
    get_row, set_status = _KINDS[inputs.kind]
    row = get_row(inputs.id)
    if row is None or row["project_id"] != ctx.project_id:
        raise ToolError("not_found", f"No such {inputs.kind}: {inputs.id}")
    status = "rejected" if inputs.action == "reject" else "active"
    if row["status"] != status:
        set_status(inputs.id, status)
        record_user_action(ctx, f"user_{inputs.action}_{inputs.kind}")
    return {"id": inputs.id, "status": status}


register(Tool(
    name="save_source",
    description="Record a web page that facts cite. The same address again returns the page already saved.",
    input_model=SaveSourceInput, handler=save_source, callers=SAVERS,
))
register(Tool(
    name="save_evidence",
    description="Save one cited fact from a research. Its phase comes from the research. "
                "The same claim again returns the fact already saved.",
    input_model=SaveEvidenceInput, handler=save_evidence, callers=SAVERS, id_fields=("task_id",),
))
register(Tool(
    name="create_finding",
    description="Save a short conclusion for a research's phase, citing 1 to 10 of the project's active facts.",
    input_model=CreateFindingInput, handler=create_finding, callers=SAVERS, id_fields=("task_id",),
))
register(Tool(
    name="search_existing_evidence",
    description="Search the project's active facts by words in the claim, quote or page title. Newest first.",
    input_model=SearchInput, handler=search_existing_evidence, callers=ALL_CALLERS,
))
register(Tool(
    name="update_evidence_status",
    description="Reject or restore a fact or a conclusion. Only the person can do this.",
    input_model=StatusInput, handler=update_evidence_status, callers=frozenset({"user"}),
))
