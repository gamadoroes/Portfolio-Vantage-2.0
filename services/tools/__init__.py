# services/tools/__init__.py
"""The Supervisor's and the board's only way to act on the app: explicit, checked, logged tools.

Each tool module registers its tools when imported; the `cards`, `review` and `state` imports below do that.
"""
from . import cards, review, state  # noqa: F401  (register the card, review and read tools)
from .registry import (  # noqa: F401
    CALLERS,
    HTTP_STATUS,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    all_tools,
    claude_tools,
    clip_for_log,
    get_tool,
    register,
    run_tool,
)
