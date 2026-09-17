"""Tool package.

:func:`build_registry` is the single place a tool becomes available, which is
also the single place to look when asking "what can Jarvis actually do?".
"""

from __future__ import annotations

from typing import Any

from ..policy import Policy
from . import browser, calendar, desktop, mail, memory_tools, system, web
from .registry import (
    ToolContext,
    ToolOutput,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    object_schema,
    param,
    sanitise_tool_name,
)

# Order is presentation order in /health, nothing more.
MODULES = (web, memory_tools, system, desktop, browser, mail, calendar)

__all__ = [
    "MODULES",
    "ToolContext",
    "ToolOutput",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "build_registry",
    "object_schema",
    "param",
    "sanitise_tool_name",
]


def build_registry(policy: Policy, ctx: ToolContext | None = None) -> ToolRegistry:
    """Create a registry containing every built-in tool, tiers already resolved."""
    registry = ToolRegistry(policy, ctx)
    for module in MODULES:
        build_specs: Any = module.build_specs
        registry.register_all(build_specs())
    return registry
