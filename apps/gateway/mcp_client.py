"""External MCP servers, surfaced as extra tools.

Both transports are supported -- stdio subprocesses *and* HTTP (SSE or
streamable) -- which is one place this goes beyond the reference project, whose
client could only reach HTTP/SSE servers.

Three design decisions worth stating, because each of them is a way this could
have gone wrong:

**Sessions are held open by a task, not by the caller.** An MCP session lives
inside an async context manager that must stay entered for the connection's whole
life, so opening one per tool call would re-launch the server every time. Each
server gets a supervisor task that enters the context, publishes its tools, and
then waits until shutdown.

**Every tool is namespaced `mcp__<server>__<tool>`.** Two servers can both expose
`read_file` without colliding, and policy can address them by glob -- which is how
`mcp__*__drop_*` can be denied outright while `mcp__filesystem__read_text_file`
runs without asking.

**External tools are treated as execution surfaces.** Their arguments are scanned
by the deny-list and they default to needing confirmation, because this project
cannot know what a third-party server does with them. `allowed_tools` is applied
*before* that, so a tool a server offers is not even registered unless it is on
the list -- an unregistered tool cannot be called, injected, or reasoned about.

A server that fails to start is reported in /health and skipped. One broken
integration must never stop the assistant from booting.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .tools.registry import ToolContext, ToolOutput, ToolSpec, sanitise_tool_name

logger = logging.getLogger(__name__)

NAMESPACE = "mcp"
START_TIMEOUT_SECONDS = 30.0
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    auth: dict[str, Any] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ("*",)
    enabled: bool = True
    timeout: float = START_TIMEOUT_SECONDS

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MCPServerConfig:
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError("every MCP server needs a name")
        transport = str(raw.get("transport") or ("http" if raw.get("url") else "stdio")).lower()
        if transport in {"http", "sse", "streamable", "streamable-http"}:
            transport = "http"
        if transport not in {"stdio", "http"}:
            raise ValueError(f"{name}: unknown transport {transport!r}")

        return cls(
            name=_clean_name(name),
            transport=transport,
            command=str(raw.get("command") or ""),
            args=[str(argument) for argument in raw.get("args") or []],
            env=_expand_env(raw.get("env") or {}),
            cwd=str(raw.get("cwd") or ""),
            url=str(raw.get("url") or ""),
            headers=_expand_env(raw.get("headers") or {}),
            auth=dict(raw.get("auth") or {}),
            allowed_tools=tuple(str(pattern) for pattern in raw.get("allowed_tools") or ["*"]),
            enabled=bool(raw.get("enabled", True)),
            timeout=float(raw.get("timeout") or START_TIMEOUT_SECONDS),
        )

    def allows(self, tool_name: str) -> bool:
        return any(fnmatch.fnmatch(tool_name, pattern) for pattern in self.allowed_tools)

    def validate(self) -> str | None:
        """Return a reason this server cannot be started, or None."""
        if self.transport == "stdio" and not self.command:
            return "stdio server has no 'command'"
        if self.transport == "http" and not self.url:
            return "http server has no 'url'"
        return None

    def resolved_headers(self) -> dict[str, str]:
        """Headers with auth folded in, so secrets stay out of the config file."""
        headers = dict(self.headers)
        if not self.auth:
            return headers

        kind = str(self.auth.get("type") or "secret_key").lower()
        env_var = str(self.auth.get("env_var") or "")
        secret = os.environ.get(env_var, "") if env_var else ""
        if not secret:
            return headers

        header = str(self.auth.get("header") or ("Authorization" if kind == "bearer" else "X-Api-Key"))
        scheme = str(self.auth.get("scheme") or ("Bearer" if kind == "bearer" else ""))
        headers[header] = f"{scheme} {secret}".strip()
        return headers

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "enabled": self.enabled,
            "allowed_tools": list(self.allowed_tools),
            "command": self.command if self.transport == "stdio" else "",
            "url": self.url if self.transport == "http" else "",
            "has_auth": bool(self.auth),
        }


def _clean_name(raw: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_") or "server"


def _expand_env(raw: Any) -> dict[str, str]:
    """Expand ${VAR} in values so config can reference secrets without holding them."""
    expanded: dict[str, str] = {}
    for key, value in dict(raw or {}).items():
        text = str(value)
        expanded[str(key)] = _ENV_PATTERN.sub(
            lambda match: os.environ.get(match.group(1), ""), text
        )
    return expanded


class MCPServerConnection:
    """One server: supervisor task, live session, registered tools."""

    def __init__(self, config: MCPServerConfig, registry: Any, audit: Any) -> None:
        self.config = config
        self.registry = registry
        self.audit = audit
        self.session: Any = None
        self.error: str | None = None
        self.tool_count = 0
        self._ready = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None
        self._stopping = asyncio.Event()

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> bool:
        self._task = asyncio.create_task(self._supervise(), name=f"mcp-{self.config.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.config.timeout)
        except TimeoutError:
            self.error = f"timed out after {self.config.timeout:g}s while starting"
            await self.stop()
            return False
        return self.session is not None

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
        self.registry.unregister_prefix(f"{NAMESPACE}__{self.config.name}__")
        self.session = None
        self.tool_count = 0

    async def _supervise(self) -> None:
        """Enter the transport, publish tools, then hold the session open."""
        try:
            async with self._transport() as streams:
                read_stream, write_stream = streams[0], streams[1]
                from mcp.client.session import ClientSession

                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self.session = session
                    listed = await session.list_tools()
                    self._register(listed.tools or [])
                    self._ready.set()
                    await self._stopping.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            logger.warning("MCP server %s failed: %s", self.config.name, self.error)
            if self.audit is not None:
                self.audit.record("mcp_server_failed", server=self.config.name, error=self.error)
        finally:
            self._ready.set()

    def _transport(self) -> Any:
        if self.config.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env or None,
                cwd=self.config.cwd or None,
            )
            return stdio_client(params)

        from mcp.client.sse import sse_client

        headers = self.config.resolved_headers() or None
        return sse_client(self.config.url, headers=headers)

    # -- tools ------------------------------------------------------------

    def _register(self, tools: list[Any]) -> None:
        registered = 0
        skipped: list[str] = []
        for tool in tools:
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            if not self.config.allows(name):
                skipped.append(name)
                continue
            self.registry.register(self._spec(tool, name))
            registered += 1

        self.tool_count = registered
        if self.audit is not None:
            self.audit.record(
                "mcp_server_ready",
                server=self.config.name,
                transport=self.config.transport,
                registered=registered,
                skipped=skipped,
            )
        logger.info(
            "MCP server %s: %d tool(s) registered, %d filtered out by allowed_tools",
            self.config.name, registered, len(skipped),
        )

    def _spec(self, tool: Any, tool_name: str) -> ToolSpec:
        namespaced = sanitise_tool_name(f"{NAMESPACE}__{self.config.name}__{tool_name}")
        schema = getattr(tool, "input_schema", None) or {}

        return ToolSpec(
            name=namespaced,
            description=(
                f"[{self.config.name}] {getattr(tool, 'description', '') or tool_name}"
            ).strip(),
            parameters=_normalise_schema(schema),
            handler=self._handler(tool_name),
            # Conservative: this project cannot audit what a third-party server
            # does, so its arguments are scanned and its tools ask before running.
            executing=True,
            category=f"mcp:{self.config.name}",
            meta={"server": self.config.name, "tool": tool_name},
        )

    def _handler(self, tool_name: str):
        async def handler(ctx: ToolContext, **arguments: Any) -> ToolOutput:
            return await self.call_tool(tool_name, arguments)

        return handler

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolOutput:
        session = self.session
        if session is None:
            return ToolOutput(
                text=f"The {self.config.name} server is not connected ({self.error or 'stopped'}).",
                ok=False,
            )
        try:
            result = await session.call_tool(tool_name, arguments or {})
        except Exception as exc:
            return ToolOutput(text=f"{self.config.name}/{tool_name} failed: {exc}", ok=False)

        text = _render_content(getattr(result, "content", None), getattr(result, "structured_content", None))
        is_error = bool(getattr(result, "is_error", False))
        return ToolOutput(text=text or "(no output)", ok=not is_error)

    def describe(self) -> dict[str, Any]:
        return {
            **self.config.describe(),
            "connected": self.session is not None,
            "tools_registered": self.tool_count,
            "error": self.error,
        }


def _normalise_schema(schema: Any) -> dict[str, Any]:
    """Make a server-supplied JSON Schema safe for every provider."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    parameters = dict(schema)
    parameters.setdefault("type", "object")
    if parameters.get("type") == "object":
        properties = parameters.get("properties")
        if not isinstance(properties, dict):
            parameters["properties"] = {}
        # Gemini and Groq reject some loose keywords that servers commonly emit.
        for key in ("$schema", "additionalProperties", "definitions", "$defs"):
            parameters.pop(key, None)
    return parameters


def _render_content(content: Any, structured: Any) -> str:
    """Flatten an MCP result into text the model can read."""
    parts: list[str] = []
    for block in content or []:
        kind = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if kind == "text":
            text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            if text:
                parts.append(str(text))
        elif kind in {"image", "audio"}:
            parts.append(f"({kind} content omitted)")
        elif kind == "resource":
            parts.append("(embedded resource omitted)")
        else:
            parts.append(str(block))

    if structured:
        with contextlib.suppress(TypeError, ValueError):
            parts.append(json.dumps(structured, default=str)[:2000])
    return "\n".join(part for part in parts if part).strip()


class MCPManager:
    """Loads config/mcp_servers.yaml and keeps every server running."""

    def __init__(self, config_path: Path, registry: Any, audit: Any) -> None:
        self.config_path = config_path
        self.registry = registry
        self.audit = audit
        self.connections: list[MCPServerConnection] = []
        self.notes: list[str] = []

    def load_configs(self) -> list[MCPServerConfig]:
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return []
        except (OSError, yaml.YAMLError) as exc:
            self.notes.append(f"could not read {self.config_path.name}: {exc}")
            return []

        configs: list[MCPServerConfig] = []
        for entry in raw.get("servers") or []:
            if not isinstance(entry, dict):
                continue
            try:
                config = MCPServerConfig.from_dict(entry)
            except ValueError as exc:
                self.notes.append(str(exc))
                continue
            if not config.enabled:
                continue
            problem = config.validate()
            if problem:
                self.notes.append(f"{config.name}: {problem}")
                continue
            configs.append(config)
        return configs

    async def start(self) -> list[str]:
        configs = self.load_configs()
        if not configs:
            return []

        for config in configs:
            connection = MCPServerConnection(config, self.registry, self.audit)
            ok = await connection.start()
            self.connections.append(connection)
            if not ok:
                self.notes.append(
                    f"MCP server '{config.name}' is unavailable ({connection.error})"
                )
            else:
                self.notes.append(
                    f"MCP server '{config.name}': {connection.tool_count} tool(s) added"
                )
        return self.notes

    async def stop(self) -> None:
        for connection in self.connections:
            with contextlib.suppress(Exception):
                await connection.stop()
        self.connections.clear()

    @property
    def tools_registered(self) -> int:
        return sum(connection.tool_count for connection in self.connections)

    def describe(self) -> dict[str, Any]:
        return {
            "config": str(self.config_path),
            "servers": [connection.describe() for connection in self.connections],
            "tools_registered": self.tools_registered,
        }
