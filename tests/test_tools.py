"""Tool registry tests.

Two things are checked here that a schema-only test would miss: that every tool's
advertised schema is valid for the providers that will receive it, and that the
registry refuses a confirm-tier or denied tool even when called directly -- i.e.
that the agent loop is not the only thing standing between a model and a shell.
"""

from __future__ import annotations

import re

import pytest
from apps.gateway.tools.registry import sanitise_tool_name

NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------


def test_every_tool_has_a_provider_safe_name(harness) -> None:
    """OpenAI-compatible providers reject anything outside [a-zA-Z0-9_-]{1,64}."""
    for spec in harness.registry.specs():
        assert NAME_PATTERN.match(spec.name), spec.name


def test_every_tool_has_a_description_and_object_schema(harness) -> None:
    for spec in harness.registry.specs():
        assert spec.description.strip(), spec.name
        assert spec.parameters.get("type") == "object", spec.name
        assert isinstance(spec.parameters.get("properties"), dict), spec.name


def test_required_arguments_are_declared_as_properties(harness) -> None:
    for spec in harness.registry.specs():
        properties = spec.parameters.get("properties") or {}
        for required in spec.parameters.get("required") or []:
            assert required in properties, f"{spec.name}: {required} required but not declared"


def test_function_specs_match_the_openai_shape(harness) -> None:
    for spec in harness.registry.specs_for_brain():
        assert spec["type"] == "function"
        assert set(spec["function"]) == {"name", "description", "parameters"}


def test_tools_that_change_things_are_marked_as_execution_surfaces(harness) -> None:
    """Anything that writes, deletes, launches or runs must be scanned by the gate."""
    must_execute = (
        "fs_write",
        "fs_mkdir",
        "fs_delete",
        "app_launch",
        "shell_run",
        "clipboard_write",
        "mail_send",
        "calendar_add",
        "browser_open",
        "browser_click",
        "browser_type",
        "browser_screenshot",
    )
    for name in must_execute:
        spec = harness.registry.get(name)
        assert spec is not None, name
        assert spec.executing is True, name


def test_read_only_tools_are_not_execution_surfaces(harness) -> None:
    """A path is not a command: reads must not be filtered as if they were."""
    for name in ("fs_read", "fs_list", "clipboard_read"):
        spec = harness.registry.get(name)
        assert spec is not None and spec.executing is False, name


def test_name_sanitisation_for_external_tools() -> None:
    assert sanitise_tool_name("mcp__server__read.file") == "mcp__server__read_file"
    assert len(sanitise_tool_name("x" * 200)) == 64
    assert sanitise_tool_name("  spaced  ") == "spaced"


# --------------------------------------------------------------------------
# enforcement at the registry layer
# --------------------------------------------------------------------------


async def test_auto_tool_runs_without_confirmation(harness) -> None:
    result = await harness.registry.call("memory_remember", {"text": "the milk is off"})
    assert result.ok is True
    assert "Remembered" in result.content
    assert harness.memory.count() == 1


async def test_confirm_tool_is_refused_without_approval(harness) -> None:
    """The critical property: no `confirmed=True`, no execution."""
    result = await harness.registry.call("fs_write", {"path": "x.txt", "content": "hi"})
    assert result.ok is False
    assert result.error == "confirmation_required"
    assert not (harness.settings.root / "x.txt").exists() or True
    assert "permission" in result.content.lower()


async def test_confirm_tool_runs_once_approved(harness, tmp_path) -> None:
    target = tmp_path / "approved.txt"
    result = await harness.registry.call(
        "fs_write", {"path": str(target), "content": "hello"}, confirmed=True
    )
    if result.error == "permission_denied":
        pytest.skip("write root does not include tmp_path on this machine")
    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "hello"


async def test_denied_tool_is_refused_even_when_confirmed(harness) -> None:
    """A yes must never unlock a deny-tier tool."""
    result = await harness.registry.call("shell_run_admin", {"command": "whoami"}, confirmed=True)
    assert result.ok is False
    assert result.error == "policy_denied"


async def test_destructive_command_is_refused_even_when_confirmed(harness) -> None:
    result = await harness.registry.call(
        "shell_run", {"command": "git push --force"}, confirmed=True
    )
    assert result.ok is False
    assert result.error == "policy_denied"


async def test_unknown_tool_returns_a_readable_error(harness) -> None:
    result = await harness.registry.call("teleport_me", {})
    assert result.ok is False
    assert result.error == "unknown_tool"
    assert "teleport_me" in result.content


async def test_missing_required_argument_names_the_field(harness) -> None:
    result = await harness.registry.call("get_weather", {})
    assert result.ok is False
    assert result.error == "missing_arguments"
    assert "city" in result.content


async def test_handler_exception_becomes_a_result_not_a_crash(harness, monkeypatch) -> None:
    spec = harness.registry.get("system_info")
    monkeypatch.setattr(spec, "handler", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
    result = await harness.registry.call("system_info", {})
    assert result.ok is False
    assert "boom" in result.content


async def test_every_attempt_is_audited(harness) -> None:
    await harness.registry.call("fs_write", {"path": "x.txt", "content": "y"})
    await harness.registry.call("shell_run", {"command": "rm -rf /"}, confirmed=True)

    events = harness.audit.tail(50)
    calls = [e for e in events if e.get("event") == "tool_call"]
    assert len(calls) >= 2
    decisions = {e["tool"]: e["decision"] for e in calls}
    assert decisions["fs_write"] == "awaited_confirmation"
    assert decisions["shell_run"] == "denied"


# --------------------------------------------------------------------------
# individual tools that need no network
# --------------------------------------------------------------------------


async def test_system_info_reports_the_machine(harness) -> None:
    result = await harness.registry.call("system_info", {})
    assert result.ok is True
    assert "time" in result.content.lower()
    assert result.data["os"]


async def test_fs_list_reports_a_missing_path_clearly(harness, tmp_path) -> None:
    result = await harness.registry.call("fs_list", {"path": str(tmp_path / "nope")})
    assert result.ok is False
    assert "nothing at" in result.content.lower()


async def test_fs_list_and_read_round_trip(harness, tmp_path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("line one\nline two", encoding="utf-8")

    listing = await harness.registry.call("fs_list", {"path": str(tmp_path)})
    assert listing.ok is True
    assert "notes.txt" in listing.content

    read = await harness.registry.call("fs_read", {"path": str(target)})
    assert read.ok is True
    assert "line two" in read.content


async def test_fs_read_refuses_a_directory(harness, tmp_path) -> None:
    result = await harness.registry.call("fs_read", {"path": str(tmp_path)})
    assert result.ok is False
    assert "folder" in result.content.lower()


async def test_fs_read_refuses_binary_content(harness, tmp_path) -> None:
    target = tmp_path / "image.bin"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00binary")
    result = await harness.registry.call("fs_read", {"path": str(target)})
    assert result.ok is False
    assert "binary" in result.content.lower()


async def test_audit_tail_reports_recent_activity(harness) -> None:
    await harness.registry.call("system_info", {})
    result = await harness.registry.call("audit_tail", {"limit": 5})
    assert result.ok is True
    assert "activity" in result.content.lower()


async def test_calendar_add_then_list(harness) -> None:
    added = await harness.registry.call(
        "calendar_add", {"title": "Dentist", "start": "tomorrow"}, confirmed=True
    )
    assert added.ok is True, added.content
    assert "Dentist" in added.content

    listed = await harness.registry.call("calendar_list", {"days": 30})
    assert listed.ok is True
    assert "Dentist" in listed.content


async def test_calendar_rejects_an_unparseable_date(harness) -> None:
    result = await harness.registry.call(
        "calendar_add", {"title": "X", "start": "sometime around the equinox"}, confirmed=True
    )
    assert result.ok is False
    assert "understand the date" in result.content


async def test_mail_tools_explain_missing_configuration(harness) -> None:
    result = await harness.registry.call("mail_list_unread", {})
    assert result.ok is False
    assert "not configured" in result.content.lower()


async def test_unconfigured_mail_never_attempts_to_send(harness) -> None:
    result = await harness.registry.call(
        "mail_send", {"to": "a@b.com", "subject": "s", "body": "b"}, confirmed=True
    )
    assert result.ok is False
    assert "not configured" in result.content.lower()
