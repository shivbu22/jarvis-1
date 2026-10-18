"""Tool package.

:func:`build_registry` is the single place a tool becomes available, which is
also the single place to look when asking "what can Jarvis actually do?".
"""

from __future__ import annotations

from typing import Any

from ..policy import Policy
from . import (
    agentic,
    browser,
    calendar,
    command_learning,
    desktop,
    evolution_tools,
    finance_extended,
    intelligence,
    interaction,
    leads,
    mail,
    media,
    memory_tools,
    obsidian_brain,
    operator,
    productivity,
    profile_tools,
    research,
    scheduler,
    self_heal,
    skills,
    speaker,
    subagents,
    system,
    trading,
    ultimate,
    vision,
    web,
)
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
MODULES = (
    media,
    ultimate,
    web,
    memory_tools,
    command_learning,
    obsidian_brain,
    interaction,
    system,
    desktop,
    browser,
    vision,
    mail,
    calendar,
    agentic,
    subagents,
    speaker,
    self_heal,
    trading,
    leads,
    operator,
    research,
    finance_extended,
    productivity,
    intelligence,
    skills,
    scheduler,
    profile_tools,
    evolution_tools,
)

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
