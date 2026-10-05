# services/tools/registry.py
"""The single door to every tool: caller check, input validation, project scoping,
clean failures and a log row for every call."""
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import anthropic
import openai
from pydantic import BaseModel, ValidationError

from db.repositories import projects_repo, research_work_items_repo, tool_calls_repo

from .schema import claude_schema

CALLERS = ("supervisor", "user", "system")
ERROR_CODES = ("not_allowed", "invalid_input", "not_found", "conflict", "unavailable", "internal")
HTTP_STATUS = {"not_allowed": 403, "invalid_input": 400, "not_found": 404, "conflict": 409,
               "unavailable": 503, "internal": 500}
LOG_STRING_LIMIT = 500
LOG_JSON_LIMIT = 4000
_CLIP_MARK = "...[clipped]"


class ToolError(Exception):
    """An expected failure a handler reports, with one of ERROR_CODES."""

    def __init__(self, code, message, fields=None):
        if code not in ERROR_CODES:
            # TypeError, not ValueError: a bad code is a programming bug and must surface as `internal`.
            raise TypeError(f"Unknown tool error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = list(fields or [])


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict | None = None
    error: dict | None = None
    call_id: int | None = None


@dataclass(frozen=True)
class ToolContext:
    project_name: str
    project_id: int
    caller: str
    call_id: int


@dataclass
class Tool:
    name: str
    description: str
    input_model: type
    handler: Callable
    callers: frozenset
    id_fields: tuple = field(default_factory=tuple)


_REGISTRY = {}


def register(tool):
    if not set(tool.callers) <= set(CALLERS):
        raise ValueError(f"{tool.name}: unknown caller in {sorted(tool.callers)}")
    if tool.name in _REGISTRY:
        raise ValueError(f"Tool already registered: {tool.name}")
    if not (isinstance(tool.input_model, type) and issubclass(tool.input_model, BaseModel)):
        raise ValueError(f"{tool.name}: input_model must be a pydantic model")
    unknown_ids = [n for n in tool.id_fields if n not in tool.input_model.model_fields]
    if unknown_ids:  # a typo here would silently switch off project scoping
        raise ValueError(f"{tool.name}: id_fields not on the input model: {unknown_ids}")
    _REGISTRY[tool.name] = tool
    return tool


def get_tool(name):
    return _REGISTRY.get(name)


def all_tools():
    return dict(_REGISTRY)


def claude_tools(names):
    """Anthropic tool definitions for exactly these tools (KeyError if one is unknown)."""
    return [
        {"name": _REGISTRY[n].name, "description": _REGISTRY[n].description,
         "input_schema": claude_schema(_REGISTRY[n].input_model)}
        for n in names
    ]


def _clip_value(value):
    if isinstance(value, str):
        return value if len(value) <= LOG_STRING_LIMIT else value[:LOG_STRING_LIMIT] + _CLIP_MARK
    if isinstance(value, dict):
        return {str(k): _clip_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip_value(v) for v in value]
    return value


def clip_for_log(obj):
    text = json.dumps(_clip_value(obj), default=str)
    return text if len(text) <= LOG_JSON_LIMIT else text[:LOG_JSON_LIMIT] + _CLIP_MARK


def _error(code, message, fields=None):
    return {"code": code, "message": message, "fields": list(fields or [])}


def _validation_error(exc):
    fields = [".".join(str(p) for p in e["loc"]) or "input" for e in exc.errors()]
    first = exc.errors()[0]
    return _error("invalid_input", f"Check {', '.join(dict.fromkeys(fields))}: {first['msg']}", fields)


def _card_ids(tool, parsed):
    ids = []
    for name in tool.id_fields:
        value = getattr(parsed, name, None)
        if value is None:
            continue
        ids.extend(value if isinstance(value, (list, tuple)) else [value])
    return ids


def run_tool(name, caller, project_name, inputs=None, parent_call_id=None, menu=None):
    started = time.monotonic()
    project_id = projects_repo.get_id(project_name) if project_name else None
    if project_id is None:
        return ToolResult(ok=False, error=_error("not_found", "No such project."))
    raw = inputs if isinstance(inputs, dict) else {}
    call_id = tool_calls_repo.create(project_id, name, caller, clip_for_log(raw), parent_call_id=parent_call_id)

    def finish(ok, data=None, error=None, work_item_id=None, log_message=None):
        # Once the call row exists, run_tool never raises: a logging failure must not
        # turn a finished (possibly paid) action into a reported failure, or vice versa.
        try:
            tool_calls_repo.finish(
                call_id, ok,
                error_code=error["code"] if error else None,
                error_message=(log_message or error["message"]) if error else None,
                result_json=clip_for_log(data) if ok else None,
                duration_ms=int((time.monotonic() - started) * 1000),
                research_work_item_id=work_item_id,
            )
        except Exception as log_exc:
            print(f"[tools] {name}: could not write the call log: {type(log_exc).__name__}: {log_exc}")
        return ToolResult(ok=ok, data=data if ok else None, error=error, call_id=call_id)

    def internal(exc, work_item_id=None):
        detail = f"{type(exc).__name__}: {exc}"
        print(f"[tools] {name} failed: {detail}")
        return finish(False, error=_error("internal", f"Something went wrong running {name}."),
                      work_item_id=work_item_id, log_message=detail[:LOG_STRING_LIMIT])

    if menu is not None and name not in menu:  # checked first, so an invented tool name is logged as "not on this menu"
        return finish(False, error=_error("not_allowed", f"{name} is not on this menu."))
    tool = _REGISTRY.get(name)
    if tool is None:
        return finish(False, error=_error("not_allowed", f"There is no tool called {name}."))
    if caller not in tool.callers:
        return finish(False, error=_error("not_allowed", f"{name} cannot be used by the {caller}."))
    try:
        parsed = tool.input_model.model_validate(raw)
    except ValidationError as exc:
        return finish(False, error=_validation_error(exc))
    except Exception as exc:
        return internal(exc)

    try:
        ids = _card_ids(tool, parsed)
        for card_id in ids:
            row = research_work_items_repo.get(card_id)
            if row is None or row["project_id"] != project_id:
                return finish(False, error=_error("not_found", f"No such research: {card_id}"))
    except Exception as exc:
        return internal(exc)
    work_item_id = ids[0] if len(ids) == 1 else None

    ctx = ToolContext(project_name=project_name, project_id=project_id, caller=caller, call_id=call_id)
    try:
        data = tool.handler(ctx, parsed) or {}
    except ToolError as exc:
        return finish(False, error=_error(exc.code, exc.message, exc.fields), work_item_id=work_item_id)
    except (anthropic.APIError, openai.APIError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(f"[tools] {name}: outside service error: {detail}")
        return finish(False, error=_error("unavailable", f"{name} could not reach an outside service just now. Try again shortly."),
                      work_item_id=work_item_id, log_message=detail[:LOG_STRING_LIMIT])
    except (ValidationError, json.JSONDecodeError, UnicodeError) as exc:
        # These are ValueErrors too, but they are bugs or bad data, not "the card is in the wrong state".
        return internal(exc, work_item_id)
    except ValueError as exc:  # the services raise ValueError when a card is not in a state that allows the action
        return finish(False, error=_error("conflict", str(exc)), work_item_id=work_item_id)
    except Exception as exc:
        return internal(exc, work_item_id)
    return finish(True, data=data, work_item_id=work_item_id)
