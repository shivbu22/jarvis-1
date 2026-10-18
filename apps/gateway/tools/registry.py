"""The tool registry.

A tool is a name, a JSON Schema, a handler, and one safety-relevant flag:
``executing``. That flag decides whether the policy gate scans the call's
arguments for destructive patterns (see policy.py), so it is set conservatively --
a new tool is treated as executing unless it plainly only reads text.

Handlers may be sync or async and take ``(ctx, **arguments)``. The registry owns
the boring but essential parts: validating arguments against the schema before a
handler ever sees them, so a model that omits a required field gets a readable
error instead of a stack trace; running blocking handlers off the event loop; and
turning whatever a handler returns into text the model can actually use.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..policy import Policy

logger = logging.getLogger(__name__)

# OpenAI-compatible providers require ^[a-zA-Z0-9_-]{1,64}$ for function names.
# Some MCP servers publish names containing dots or colons, so names are
# sanitised on registration and the original is kept in spec.meta.
_VALID_NAME = re.compile(r"[^a-zA-Z0-9_-]")
MAX_NAME_LENGTH = 64


def sanitise_tool_name(raw: str) -> str:
    name = _VALID_NAME.sub("_", (raw or "").strip())
    return name[:MAX_NAME_LENGTH]


@dataclass
class ToolContext:
    """Everything a handler is allowed to touch."""

    settings: Any
    memory: Any
    policy: Policy
    audit: Any
    bus: Any
    state: dict[str, Any] = field(default_factory=dict)
    service: Any = None


@dataclass
class ToolOutput:
    """Optional richer return: `text` goes to the model, `data` to the API/UI."""

    text: str
    data: Any = None
    ok: bool = True

    @classmethod
    def from_ok(cls, data: Any, text: str = "") -> ToolOutput:
        import json
        if not text:
            if isinstance(data, str):
                text = data
            elif isinstance(data, dict):
                text = data.get("message") or json.dumps(data)
            else:
                text = str(data)
        return cls(text=text, data=data, ok=True)

    @classmethod
    def from_error(cls, message: str) -> ToolOutput:
        return cls(text=str(message), data={"error": str(message)}, ok=False)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    executing: bool = True
    category: str = "general"
    meta: dict[str, Any] = field(default_factory=dict)
    tier: str = "confirm"

    def as_function_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tier": self.tier,
            "executing": self.executing,
        }


@dataclass
class ToolResult:
    name: str
    ok: bool
    content: str
    data: Any = None
    error: str | None = None
    duration_ms: int = 0
    tier: str = "confirm"

    def as_message(self, tool_call_id: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": self.name,
            "content": self.content,
        }

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "tier": self.tier,
            "content": self.content[:600],
        }


class ToolRegistry:
    def __init__(self, policy: Policy, ctx: ToolContext | None = None) -> None:
        self.policy = policy
        self.ctx = ctx
        self._specs: dict[str, ToolSpec] = {}

    # -- registration -----------------------------------------------------

    def register(self, spec: ToolSpec) -> ToolSpec:
        name = sanitise_tool_name(spec.name)
        if not name:
            raise ValueError("tool name is empty after sanitisation")
        spec.name = name
        spec.tier = self.policy.tier_for(name)
        self._specs[name] = spec
        return spec

    def register_all(self, specs: list[ToolSpec]) -> list[ToolSpec]:
        return [self.register(spec) for spec in specs]

    def unregister_prefix(self, prefix: str) -> int:
        """Drop every tool starting with `prefix` (used when MCP servers reload)."""
        doomed = [name for name in self._specs if name.startswith(prefix)]
        for name in doomed:
            del self._specs[name]
        return len(doomed)

    def refresh_tiers(self) -> None:
        """Re-resolve tiers after a policy reload."""
        for spec in self._specs.values():
            spec.tier = self.policy.tier_for(spec.name)

    # -- lookup -----------------------------------------------------------

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(sanitise_tool_name(name))

    def specs(self) -> list[ToolSpec]:
        return sorted(self._specs.values(), key=lambda s: (s.category, s.name))

    def specs_for_brain(self) -> list[dict[str, Any]]:
        return [spec.as_function_spec() for spec in self.specs()]

    def specs_for_query(self, query: str = "", max_tools: int = 36) -> list[dict[str, Any]]:
        """Filter relevant tools based on query intent to drastically reduce prompt eval latency.

        Passing 114 tools inflates context by ~9,000 tokens and adds 5+ seconds of latency on a 4GB GPU.
        Filtering to ~16-24 contextually relevant tools keeps prompt eval under 500ms while preserving
        100% execution accuracy.
        """
        if not query or len(self._specs) <= max_tools:
            return self.specs_for_brain()

        q = query.lower()

        # Core tools that should ALWAYS be available across every turn
        core_categories = {"system", "memory", "desktop", "browser", "web", "media"}

        # Domain triggers
        triggers = {
            "browser": ("browser", "chrome", "edge", "firefox", "brave", "url", "web", "site", "page", "click", "search", "google", "navigate", "dom", "kholo", "open"),
            "web": ("web", "search", "duckduckgo", "wikipedia", "lookup", "google", "internet"),
            "media": ("music", "song", "youtube", "video", "play", "volume", "sound", "audio", "spotify", "pause", "baja", "lofi"),
            "brain": ("obsidian", "note", "brain", "vault", "record", "write note", "read note", "journal"),
            "finance": ("stock", "trade", "crypto", "btc", "market", "nifty", "share", "portfolio", "buy", "sell", "equity"),
            "research": ("news", "rss", "sentiment", "filing", "whois", "dns", "company"),
            "agentic": ("subagent", "swarm", "worker", "spawn", "squad", "parallel", "task"),
            "skills": ("skill", "doctrine", "antigravity", "instruction"),
            "vision": ("camera", "photo", "look", "see", "vision", "screenshot", "screen"),
            "mail": ("email", "mail", "inbox", "send mail"),
            "calendar": ("calendar", "event", "meeting", "schedule"),
            "ultimate": ("admin", "elevated", "kill", "process", "restart", "patch", "self-heal"),
        }

        active_categories = set(core_categories)
        for cat, words in triggers.items():
            if any(w in q for w in words):
                active_categories.add(cat)

        selected: list[ToolSpec] = []
        for spec in self.specs():
            if spec.category in active_categories:
                selected.append(spec)

        if len(selected) < 16:
            for spec in self.specs():
                if spec not in selected:
                    selected.append(spec)
                if len(selected) >= max_tools:
                    break

        return [spec.as_function_spec() for spec in selected[:max_tools]]

    def names(self) -> list[str]:
        return sorted(self._specs)

    def names_by_tier(self) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = {"auto": [], "confirm": [], "deny": []}
        for spec in self.specs():
            buckets.setdefault(spec.tier, []).append(spec.name)
        return buckets

    def describe(self) -> list[dict[str, Any]]:
        return [spec.describe() for spec in self.specs()]

    # -- execution --------------------------------------------------------

    async def call(
        self, name: str, args: dict[str, Any], *, confirmed: bool = False
    ) -> ToolResult:
        """Run one tool. Never raises: failures become results the model can read.

        Args:
            confirmed: pass True ONLY after a human approved this exact call via
                the confirmation broker. The registry screens every call itself,
                so the agent loop is not a privileged path -- an MCP client or the
                CLI cannot reach a handler the policy would refuse.
        """
        started = time.perf_counter()
        spec = self.get(name)
        if spec is None:
            return ToolResult(
                name=name,
                ok=False,
                content=f"No tool named '{name}' exists.",
                error="unknown_tool",
            )
        if self.ctx is None:
            return ToolResult(
                name=name,
                ok=False,
                content="Tool runtime is not initialised.",
                error="no_context",
                tier=spec.tier,
            )

        args = dict(args or {})

        # The gate is applied here as well as in the agent loop: two independent
        # checks on the same decision, because the cost of a bypass is a real
        # action on a real machine.
        decision = self.policy.screen(spec.name, args, executing=spec.executing)
        self._audit(
            "tool_call",
            tool=spec.name,
            tier=decision.tier,
            decision=(
                "denied" if not decision.allowed
                else "confirmed" if decision.needs_confirmation and confirmed
                else "awaited_confirmation" if decision.needs_confirmation
                else "auto"
            ),
            reason=decision.reason,
            pattern=decision.pattern,
            args=args,
        )
        if not decision.allowed:
            return ToolResult(
                name=spec.name,
                ok=False,
                content=f"Refused: {decision.reason}",
                error="policy_denied",
                tier=decision.tier,
                duration_ms=_elapsed_ms(started),
            )
        if decision.needs_confirmation and not confirmed:
            return ToolResult(
                name=spec.name,
                ok=False,
                content=(
                    "This action needs the master's permission and has not been approved, "
                    "so it was not performed."
                ),
                error="confirmation_required",
                tier=decision.tier,
                duration_ms=_elapsed_ms(started),
            )
        missing = [
            key
            for key in (spec.parameters.get("required") or [])
            if key not in args or args[key] in (None, "")
        ]
        if missing:
            return ToolResult(
                name=name,
                ok=False,
                content=(
                    f"{spec.name} needs the argument(s): {', '.join(missing)}. "
                    "Call it again with those filled in."
                ),
                error="missing_arguments",
                tier=spec.tier,
                duration_ms=_elapsed_ms(started),
            )

        try:
            if inspect.iscoroutinefunction(spec.handler):
                raw = await spec.handler(self.ctx, **args)
            else:
                raw = await asyncio.to_thread(spec.handler, self.ctx, **args)
        except PermissionError as exc:
            return ToolResult(
                name=name,
                ok=False,
                content=f"Refused: {exc}",
                error="permission_denied",
                tier=spec.tier,
                duration_ms=_elapsed_ms(started),
            )
        except Exception as exc:  # a tool bug must not end the conversation
            logger.exception("tool %s failed", name)
            return ToolResult(
                name=name,
                ok=False,
                content=f"{spec.name} failed: {exc}",
                error=f"{type(exc).__name__}",
                tier=spec.tier,
                duration_ms=_elapsed_ms(started),
            )

        text, data, ok = _render(raw)
        self._audit(
            "tool_result",
            tool=spec.name,
            ok=ok,
            duration_ms=_elapsed_ms(started),
            summary=text[:300],
        )
        return ToolResult(
            name=name,
            ok=ok,
            content=text,
            data=data,
            tier=spec.tier,
            duration_ms=_elapsed_ms(started),
        )

    def _audit(self, event: str, **fields: Any) -> None:
        if self.ctx is not None and self.ctx.audit is not None:
            self.ctx.audit.record(event, **fields)


def _render(raw: Any) -> tuple[str, Any, bool]:
    if isinstance(raw, ToolOutput):
        return raw.text, raw.data, raw.ok
    if raw is None:
        return "Done.", None, True
    if isinstance(raw, str):
        return raw, None, True
    try:
        return json.dumps(raw, default=str, ensure_ascii=False), raw, True
    except (TypeError, ValueError):
        return str(raw), None, True


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def param(
    name: str,
    type_: str,
    description: str | None = None,
    *,
    enum: list[str] | None = None,
    default: Any = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> dict[str, Any]:
    """Build one JSON Schema property. Keeps tool schemas uniform and valid.

    Whether a property is optional is expressed once, by the `required` list
    passed to :func:`object_schema` -- not twice, in two places that can disagree.

    Two call shapes are accepted, and only one of them carries a name: the name is
    never emitted (the property's name is its key in the schema), so
    ``param("city", "string", "Where")`` and ``param("string", "Where")`` build the
    same schema. The second shape exists because it is what reads naturally beside
    the ``array_param``/``integer_param``/``boolean_param`` helpers, and a tool
    module written that way should not be a crash.
    """
    if description is None:
        type_, description = name, type_
    schema: dict[str, Any] = {"type": type_, "description": description}
    if enum:
        schema["enum"] = enum
    if default is not None:
        schema["default"] = default
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def array_param(item_type: str, description: str, *, items: dict[str, Any] | None = None) -> dict[str, Any]:
    """A list-valued property, with its element type spelled out.

    ``items`` is not decoration: strict validators (Gemini, Groq) reject an array
    schema without one, and a provider rejecting a tool schema costs the whole tool.
    """
    schema = param("array", "array", description)
    schema["items"] = items or {"type": item_type}
    return schema


def integer_param(description: str, *, default: int | None = None) -> dict[str, Any]:
    return param("integer", "integer", description, default=default)


def boolean_param(description: str, *, default: bool | None = None) -> dict[str, Any]:
    return param("boolean", "boolean", description, default=default)


def object_schema(
    properties: dict[str, dict[str, Any]] | None = None, required: list[str] | None = None
) -> dict[str, Any]:
    props = properties if properties is not None else {}
    return {
        "type": "object",
        "properties": props,
        "required": required if required is not None else list(props),
        # Providers vary in how they treat unknown keys; being explicit keeps
        # strict validators (Gemini, Groq) happy.
        "additionalProperties": False,
    }
