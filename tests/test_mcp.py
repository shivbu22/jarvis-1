"""MCP tests.

The load-bearing one is `test_external_clients_cannot_execute_confirm_tier_tools`:
it asserts the property that makes it safe to hand this assistant's toolset to
another program at all.
"""

from __future__ import annotations

import pytest
from apps.gateway.mcp_client import (
    MCPManager,
    MCPServerConfig,
    _normalise_schema,
    _render_content,
)

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def test_config_parses_a_stdio_server() -> None:
    config = MCPServerConfig.from_dict(
        {
            "name": "filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\tmp"],
            "allowed_tools": ["list_directory", "read_*"],
        }
    )
    assert config.name == "filesystem"
    assert config.transport == "stdio"
    assert config.allows("read_text_file")
    assert config.allows("list_directory")
    assert not config.allows("write_file")


def test_transport_is_inferred_from_url() -> None:
    assert MCPServerConfig.from_dict({"name": "x", "url": "http://h/sse"}).transport == "http"
    assert MCPServerConfig.from_dict({"name": "x", "command": "npx"}).transport == "stdio"


def test_server_names_are_normalised() -> None:
    assert MCPServerConfig.from_dict({"name": "My Server!", "command": "x"}).name == "my_server"


def test_a_nameless_server_is_rejected() -> None:
    with pytest.raises(ValueError):
        MCPServerConfig.from_dict({"command": "npx"})


def test_incomplete_transports_are_rejected() -> None:
    assert MCPServerConfig.from_dict({"name": "a", "transport": "stdio"}).validate() is not None
    assert MCPServerConfig.from_dict({"name": "b", "transport": "http"}).validate() is not None
    assert MCPServerConfig.from_dict({"name": "c", "command": "npx"}).validate() is None


def test_environment_placeholders_are_expanded(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TEST_TOKEN", "s3cret")
    config = MCPServerConfig.from_dict(
        {
            "name": "remote",
            "url": "http://x",
            "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
        }
    )
    assert config.headers["Authorization"] == "Bearer s3cret"


def test_missing_environment_variables_expand_to_nothing(monkeypatch) -> None:
    """A missing secret must not become the literal string '${VAR}' on the wire."""
    monkeypatch.delenv("MCP_ABSENT", raising=False)
    config = MCPServerConfig.from_dict(
        {"name": "remote", "url": "http://x", "headers": {"X-Key": "${MCP_ABSENT}"}}
    )
    assert config.headers["X-Key"] == ""


def test_auth_secrets_are_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MCP_K8S_SECRET", "abc123")
    config = MCPServerConfig.from_dict(
        {
            "name": "k8s",
            "url": "http://x",
            "auth": {"type": "secret_key", "env_var": "MCP_K8S_SECRET"},
        }
    )
    headers = config.resolved_headers()
    assert headers["X-Api-Key"] == "abc123"

    bearer = MCPServerConfig.from_dict(
        {"name": "k8s", "url": "http://x", "auth": {"type": "bearer", "env_var": "MCP_K8S_SECRET"}}
    ).resolved_headers()
    assert bearer["Authorization"] == "Bearer abc123"


def test_auth_with_no_secret_adds_no_header(monkeypatch) -> None:
    monkeypatch.delenv("MCP_NOPE", raising=False)
    config = MCPServerConfig.from_dict(
        {"name": "k8s", "url": "http://x", "auth": {"type": "secret_key", "env_var": "MCP_NOPE"}}
    )
    assert config.resolved_headers() == {}


def test_config_file_is_loaded_and_filtered(tmp_path) -> None:
    path = tmp_path / "mcp.yaml"
    path.write_text(
        """
servers:
  - name: enabled-one
    command: npx
    args: ["-y", "thing"]
  - name: disabled-one
    command: npx
    enabled: false
  - name: broken-one
    transport: stdio
  - command: npx
""",
        encoding="utf-8",
    )
    manager = MCPManager(path, registry=None, audit=None)
    configs = manager.load_configs()

    assert [config.name for config in configs] == ["enabled_one"]
    assert any("broken_one" in note for note in manager.notes)
    assert any("name" in note for note in manager.notes)


def test_a_missing_config_file_is_not_an_error(tmp_path) -> None:
    manager = MCPManager(tmp_path / "absent.yaml", registry=None, audit=None)
    assert manager.load_configs() == []
    assert manager.notes == []


def test_an_unparseable_config_file_is_reported_not_fatal(tmp_path) -> None:
    path = tmp_path / "mcp.yaml"
    path.write_text("servers: [oops\n", encoding="utf-8")
    manager = MCPManager(path, registry=None, audit=None)
    assert manager.load_configs() == []
    assert manager.notes


def test_the_shipped_config_parses_and_ships_no_servers() -> None:
    """The default must be inert: nothing connects until you add a server."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manager = MCPManager(root / "config" / "mcp_servers.yaml", registry=None, audit=None)
    assert manager.load_configs() == []
    assert manager.notes == []


# --------------------------------------------------------------------------
# schema and content handling
# --------------------------------------------------------------------------


def test_loose_server_schemas_are_normalised() -> None:
    schema = _normalise_schema(
        {"$schema": "http://json-schema.org/draft-07/schema#", "properties": {"a": {"type": "string"}}}
    )
    assert schema["type"] == "object"
    assert "$schema" not in schema
    assert schema["properties"]["a"]["type"] == "string"


def test_a_missing_schema_becomes_an_empty_object() -> None:
    assert _normalise_schema(None) == {"type": "object", "properties": {}}
    assert _normalise_schema({"type": "object"})["properties"] == {}


def test_text_content_is_flattened() -> None:
    class Block:
        type = "text"
        text = "the answer"

    assert _render_content([Block()], None) == "the answer"


def test_non_text_content_is_stated_not_silently_dropped() -> None:
    class Block:
        type = "image"

    rendered = _render_content([Block()], None)
    assert "omitted" in rendered


def test_structured_content_is_appended() -> None:
    rendered = _render_content([], {"temperature": 31})
    assert "temperature" in rendered


# --------------------------------------------------------------------------
# the guarantee
# --------------------------------------------------------------------------


async def test_external_clients_cannot_execute_confirm_tier_tools(harness) -> None:
    """An MCP client can read and ask, but never act: no human, no approval."""
    from mcp_servers.jarvis_tools import server as mcp_server

    class FakeService:
        def __init__(self, registry):
            self.registry = registry

    mcp_server.set_service(FakeService(harness.registry))  # type: ignore[arg-type]
    try:
        refused = await mcp_server._run_tool("fs_write", {"path": "x.txt", "content": "y"})
        assert refused.startswith("REFUSED")
        assert "permission" in refused.lower()

        denied = await mcp_server._run_tool("shell_run_admin", {"command": "whoami"})
        assert denied.startswith("REFUSED by policy")

        destructive = await mcp_server._run_tool("shell_run", {"command": "rm -rf /"})
        assert "REFUSED" in destructive

        # Reads still work, or the server would be pointless.
        ok = await mcp_server._run_tool("system_info", {})
        assert ok and not ok.startswith(("REFUSED", "ERROR"))
    finally:
        mcp_server.set_service(None)  # type: ignore[arg-type]


def test_every_exposed_mcp_tool_is_read_only_or_conversational() -> None:
    """Guard against someone later adding a write tool to the MCP surface."""
    import inspect

    from mcp_servers.jarvis_tools import server as mcp_server

    source = inspect.getsource(mcp_server)
    risky = ("fs_write", "fs_delete", "shell_run", "mail_send", "app_launch", "calendar_add")
    for name in risky:
        assert f'_run_tool("{name}"' not in source, f"{name} must not be exposed over MCP"
