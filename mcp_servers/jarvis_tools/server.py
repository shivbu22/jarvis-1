"""Expose Jarvis to other MCP clients (Cursor, Claude Desktop, another agent).

The interesting part is what this server *cannot* do. Every tool call goes through
the same registry, and `confirmed` is never passed -- so any tool in the confirm
tier comes back as "needs the master's permission" rather than running. There is
no code path here that can reach a shell, a write, or an email send, because there
is no human present to approve one.

That is deliberate and not a limitation to be worked around later. An external
client holding the assistant's credentials must not be able to act on its owner's
machine unattended; it can read, search, remember, and *ask*.

    python -m mcp_servers.jarvis_tools.server

Register it with e.g.:

    {
      "mcpServers": {
        "jarvis": {
          "command": "C:\\\\Users\\\\ahiva\\\\AppData\\\\Local\\\\Jarvis\\\\venv\\\\Scripts\\\\python.exe",
          "args": ["-m", "mcp_servers.jarvis_tools.server"],
          "cwd": "C:\\\\Users\\\\ahiva\\\\OneDrive\\\\Desktop\\\\jarvis"
        }
      }
    }
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.gateway.service import JarvisService
import contextlib

try:
    from mcp.server.mcpserver import MCPServer
except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
    raise SystemExit(
        "The MCP SDK is required. Install it with 'uv pip install -r requirements.txt'."
    ) from exc

server = MCPServer(
    name="jarvis",
    version="0.1.0",
    instructions=(
        "A local-first personal assistant. Read-only tools work directly. Anything "
        "that changes the machine or sends something needs the owner's spoken "
        "permission, which cannot be given through this interface, so those calls "
        "will be refused. Use jarvis_ask to hold a conversation instead."
    ),
)

_service: JarvisService | None = None


def set_service(service: JarvisService) -> None:
    """Inject a service. Used by tests so they do not boot a real gateway."""
    global _service
    _service = service


async def _get_service() -> JarvisService:
    global _service
    if _service is None:
        _service = JarvisService()
        await _service.start()
    return _service


async def _run_tool(name: str, arguments: dict[str, Any]) -> str:
    """Call a tool through the registry. `confirmed` is deliberately never True."""
    service = await _get_service()
    result = await service.registry.call(name, arguments)
    if result.ok:
        return result.content
    if result.error == "confirmation_required":
        return (
            "REFUSED: this action needs the owner's permission, which cannot be given "
            "through an MCP client. Ask them to do it by voice or through the orb."
        )
    if result.error == "policy_denied":
        return f"REFUSED by policy: {result.content}"
    return f"ERROR ({result.error}): {result.content}"


async def _shutdown() -> None:
    global _service
    if _service is not None:
        await _service.stop()
        _service = None


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------


@server.tool(description="Ask Jarvis something. Runs a full turn: searching, memory, tools.")
async def jarvis_ask(text: str) -> str:
    service = await _get_service()
    result = await service.turn_text(text, session_id="mcp", allow_confirmation=False, speak=False)
    lines = [result.reply]
    turn = result.turn
    if turn is not None:
        for attempt in turn.tools:
            lines.append(f"  [tool] {attempt.name} ({attempt.decision})")
    return "\n".join(line for line in lines if line)


@server.tool(description="Save a durable note in Jarvis's memory.")
async def jarvis_remember(text: str, tags: str = "") -> str:
    return await _run_tool("memory_remember", {"text": text, "tags": tags})


@server.tool(description="Search Jarvis's saved notes.")
async def jarvis_recall(query: str, limit: int = 5) -> str:
    return await _run_tool("memory_recall", {"query": query, "limit": limit})


@server.tool(description="List Jarvis's most recent notes.")
async def jarvis_notes(limit: int = 10) -> str:
    return await _run_tool("memory_list", {"limit": limit})


@server.tool(description="Current weather for a city, via Open-Meteo.")
async def jarvis_weather(city: str) -> str:
    return await _run_tool("get_weather", {"city": city})


@server.tool(description="Search the web.")
async def jarvis_search(query: str, max_results: int = 5) -> str:
    return await _run_tool("web_search", {"query": query, "max_results": max_results})


@server.tool(description="Current news headlines, optionally on a topic.")
async def jarvis_news(query: str = "", max_results: int = 5) -> str:
    return await _run_tool("web_news", {"query": query, "max_results": max_results})


@server.tool(description="Facts about the machine Jarvis runs on.")
async def jarvis_system_info() -> str:
    return await _run_tool("system_info", {})


@server.tool(description="What has Jarvis actually done? Includes refusals and confirmations.")
async def jarvis_audit(limit: int = 20) -> str:
    return await _run_tool("audit_tail", {"limit": limit})


@server.tool(description="List upcoming calendar events.")
async def jarvis_calendar(days: int = 7) -> str:
    return await _run_tool("calendar_list", {"days": days})


@server.tool(description="Read a text file. Refused for binary files and oversized files.")
async def jarvis_read_file(path: str, max_chars: int = 8000) -> str:
    return await _run_tool("fs_read", {"path": path, "max_chars": max_chars})


@server.tool(description="List a folder.")
async def jarvis_list_files(path: str) -> str:
    return await _run_tool("fs_list", {"path": path})


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main() -> None:
    """Run over stdio. Some clients only start the process, never a handshake."""
    import atexit

    def _cleanup() -> None:
        import asyncio

        with contextlib.suppress(Exception):
            asyncio.run(_shutdown())

    atexit.register(_cleanup)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
