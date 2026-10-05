# services/tools/types.py
"""Input building blocks shared by every tool."""
import json
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from ..text_utils import as_text_list


class ToolInput(BaseModel):
    """Base for tool inputs. Unknown extra fields are ignored rather than failing the call."""

    model_config = ConfigDict(extra="ignore")


def Text(max_chars, min_chars=0):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=min_chars, max_length=max_chars)]


def text_list(max_items, max_item_chars):
    """A list of short strings that also accepts one string (split into lines, bullets removed)."""
    item = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_item_chars)]
    return Annotated[list[item], BeforeValidator(as_text_list), Field(max_length=max_items)]


Title = Text(200, min_chars=1)

# A research card id: bounded so a huge integer is rejected as bad input, not passed to SQLite.
CardId = Annotated[int, Field(ge=1, le=2**63 - 1)]

# A row id for facts, pages and conclusions: bounded like CardId.
RowId = Annotated[int, Field(ge=1, le=2**63 - 1)]


def _clip_to(limit):
    def clip(value):
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        return str(value).strip()[:limit]
    return clip


def Clipped(max_chars):
    """Text that is cut to max_chars instead of refused, for model output that is saved item by item."""
    return Annotated[str, BeforeValidator(_clip_to(max_chars)), StringConstraints(max_length=max_chars)]
