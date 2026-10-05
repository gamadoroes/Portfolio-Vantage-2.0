# services/tools/research_facts.py
"""Facts and conclusions from one research report, saved in one batch through child calls to save_source,
save_evidence and create_finding. Each item is checked on its own, so one bad fact never stops the others;
the result says exactly what was saved and what was skipped."""
import re
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from db.repositories import research_runs_repo, research_work_items_repo

from .evidence import (
    AS_OF_CHARS,
    CLAIM_CHARS,
    CONCLUSION_CHARS,
    DATE_CHARS,
    MAX_FACTS_PER_CONCLUSION,
    PUBLISHER_CHARS,
    QUOTE_CHARS,
    SOURCE_TITLE_CHARS,
    URL_CHARS,
    is_web_address,
)
from .registry import Tool, ToolError, register, run_tool
from .types import CardId, Clipped, ToolInput

MAX_FACTS, MAX_CONCLUSIONS = 25, 3
OPTIONS_REPORT = "Facts are not taken from the options report: it is written from other research, not from sources."
NO_CLAIM = "It has no claim."
REJECTED = "It matches a fact you rejected."
NO_TEXT = "It has no text."
NO_FACTS_LEFT = "None of the facts it cites were saved."
BAD_ADDRESS = "Saved without its source: the address does not start with http:// or https://."


def as_object_list(value):
    """A list from a field that should be one: one object becomes a list of one; anything else that is not a
    list becomes empty. (Garbled strings are recovered by the Supervisor before the tool is called.)"""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _numbers(value):
    """Fact numbers sent loosely: 2, "2", 2.0, "1, 2", ["1", 2]."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, float):
        return [int(value)] if value.is_integer() else []
    if isinstance(value, str):
        return [int(n) for n in re.findall(r"\d+", value)]
    if isinstance(value, (list, tuple)):
        return [n for item in value if not isinstance(item, (list, tuple)) for n in _numbers(item)]
    return []


@model_validator(mode="before")
@classmethod
def _objects_only(cls, value):
    """Shared by both item models: anything that is not an object becomes an empty item, skipped with a reason."""
    return value if isinstance(value, dict) else {}


class FactItem(ToolInput):
    claim: Clipped(CLAIM_CHARS) = Field(default="", description="One short, checkable claim the report makes.")
    quote: Clipped(QUOTE_CHARS) = Field(default="", description="The report's own words for it, if short.")
    source_url: Clipped(URL_CHARS) = Field(default="", description="Address of the page it came from (http or https).")
    source_title: Clipped(SOURCE_TITLE_CHARS) = Field(default="", description="Title of that page.")
    publisher: Clipped(PUBLISHER_CHARS) = ""
    published_date: Clipped(DATE_CHARS) = ""
    as_of: Clipped(AS_OF_CHARS) = Field(default="", description="The date or year the claim applies to, e.g. 2026.")

    _only_objects = _objects_only


class ConclusionItem(ToolInput):
    text: Clipped(CONCLUSION_CHARS) = Field(default="", description="A short conclusion for this phase.")
    fact_numbers: Annotated[list[int], BeforeValidator(_numbers)] = Field(
        default_factory=list, description="The facts it rests on: their positions in your facts list, counting from 1.")

    _only_objects = _objects_only


FactList = Annotated[list[FactItem], BeforeValidator(as_object_list), Field(
    max_length=MAX_FACTS, description="The report's key facts (at most 25), each with the page it came from.")]
ConclusionList = Annotated[list[ConclusionItem], BeforeValidator(as_object_list), Field(
    max_length=MAX_CONCLUSIONS, description="Up to 3 short conclusions for this phase, each citing its facts.")]


class RecordFactsInput(ToolInput):
    task_id: CardId
    facts: FactList = []
    conclusions: ConclusionList = []


def _call(ctx, name, inputs):
    return run_tool(name, ctx.caller, ctx.project_name, inputs, parent_call_id=ctx.call_id)


def _skip(skipped, item, reason, saved=False):
    skipped.append({"item": item, "reason": reason, "saved": saved})


def _save_fact(ctx, task_id, run_id, number, fact, skipped):
    """The fact's id if it was saved (or already known and active), else None. Adds to skipped as needed."""
    item = f"fact {number}"
    if not fact.claim:
        _skip(skipped, item, NO_CLAIM)
        return None
    source_id = None
    if fact.source_url:
        if not is_web_address(fact.source_url):
            _skip(skipped, item, BAD_ADDRESS, saved=True)
        else:
            source = _call(ctx, "save_source", {
                "url": fact.source_url, "title": fact.source_title or fact.source_url[:SOURCE_TITLE_CHARS],
                "publisher": fact.publisher, "published_date": fact.published_date,
            })
            if source.ok:
                source_id = source.data["source_id"]
            else:
                _skip(skipped, item, f"Saved without its source: {source.error['message']}", saved=True)
    saved = _call(ctx, "save_evidence", {"task_id": task_id, "claim": fact.claim, "quote": fact.quote,
                                         "source_id": source_id, "as_of": fact.as_of, "run_id": run_id})
    if not saved.ok:
        _skip(skipped, item, saved.error["message"])
        return None
    if saved.data["status"] != "active":
        _skip(skipped, item, REJECTED)
        return None
    return saved.data["fact_id"]


def record_research_facts(ctx, inputs):
    card = research_work_items_repo.get(inputs.task_id)
    if card["research_method"] == "SYNTHESIS":
        raise ToolError("conflict", OPTIONS_REPORT)
    run = research_runs_repo.find_latest_for_work_item(inputs.task_id)
    run_id = run["id"] if run is not None else None
    fact_ids, conclusion_ids, skipped = [], [], []
    by_number = {}  # position in this batch (from 1) -> saved, active fact id
    for number, fact in enumerate(inputs.facts, start=1):
        fact_id = _save_fact(ctx, inputs.task_id, run_id, number, fact, skipped)
        if fact_id is not None:
            by_number[number] = fact_id
            if fact_id not in fact_ids:
                fact_ids.append(fact_id)
    for number, conclusion in enumerate(inputs.conclusions, start=1):
        item = f"conclusion {number}"
        if not conclusion.text:
            _skip(skipped, item, NO_TEXT)
            continue
        cited = list(dict.fromkeys(by_number[n] for n in conclusion.fact_numbers if n in by_number))
        if not cited:
            _skip(skipped, item, NO_FACTS_LEFT)
            continue
        made = _call(ctx, "create_finding", {"task_id": inputs.task_id, "text": conclusion.text,
                                             "fact_ids": cited[:MAX_FACTS_PER_CONCLUSION]})
        if made.ok:
            conclusion_ids.append(made.data["conclusion_id"])
        else:
            _skip(skipped, item, made.error["message"])
    return {"fact_ids": fact_ids, "conclusion_ids": conclusion_ids, "skipped": skipped}


register(Tool(
    name="record_research_facts",
    description="Save the key facts (with the pages they came from) and up to 3 conclusions from one finished "
                "research report. Each item is checked on its own; anything unusable is skipped with a reason.",
    input_model=RecordFactsInput, handler=record_research_facts,
    callers=frozenset({"supervisor"}), id_fields=("task_id",),
))
