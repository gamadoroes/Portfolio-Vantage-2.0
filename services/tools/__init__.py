# services/tools/__init__.py
"""The Supervisor's and the board's only way to act on the app: explicit, checked, logged tools.

Each tool module registers its tools when imported; the `cards`, `evidence`, `review`, `runs`, `state` and `status`
imports below do that.
"""
from . import cards, evidence, review, runs, state, status  # noqa: F401  (register every tool)
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
