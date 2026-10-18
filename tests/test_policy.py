"""Policy tests -- the ones that justify calling this thing safe.

These assert the shipped `config/policy.yaml` plus the rules in policy.py, so a
regression here is a regression in what Jarvis will actually agree to do.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from apps.gateway.policy import DEFAULT_POLICY, Policy

# --------------------------------------------------------------------------
# tier resolution
# --------------------------------------------------------------------------


def test_read_only_tools_are_auto(policy: Policy) -> None:
    for name in ("web_search", "get_weather", "memory_remember", "fs_read", "system_info"):
        assert policy.tier_for(name) == "auto", name


def test_side_effecting_tools_need_confirmation(policy: Policy) -> None:
    for name in ("fs_write", "fs_delete", "shell_run", "mail_send", "calendar_add"):
        assert policy.tier_for(name) == "confirm", name



def test_unknown_tool_defaults_to_confirmation(policy: Policy) -> None:
    """A tool nobody classified must not be silently powerful."""
    assert policy.tier_for("brand_new_tool_from_the_future") == "confirm"


def test_deny_beats_confirm_beats_auto() -> None:
    custom = Policy(
        {
            "default_tier": "confirm",
            "tiers": {"auto": ["thing"], "confirm": ["thing"], "deny": ["thing"]},
        }
    )
    assert custom.tier_for("thing") == "deny"

    custom_confirm = Policy(
        {"default_tier": "confirm", "tiers": {"auto": ["thing"], "confirm": ["thing"]}}
    )
    assert custom_confirm.tier_for("thing") == "confirm"


def test_globs_resolve_and_deny_still_wins(policy: Policy) -> None:
    assert policy.tier_for("mcp__filesystem__read_file") == "confirm"  # via mcp__*
    assert policy.tier_for("mcp__anything__drop_table") == "deny"  # via mcp__*__drop_*
    assert policy.tier_for("list_directories") == "confirm"  # unmatched -> default


def test_builtin_defaults_mirror_the_shipped_file() -> None:
    """The in-code fallback must classify the same tools as the YAML."""
    fallback = Policy(DEFAULT_POLICY)
    for name in ("web_search", "fs_read", "memory_recall"):
        assert fallback.tier_for(name) == "auto", name
    for name in ("shell_run", "fs_write", "mail_send"):
        assert fallback.tier_for(name) == "confirm", name
    for name in ("shell_run_admin", "registry_write", "disk_format"):
        assert fallback.tier_for(name) == "deny", name


# --------------------------------------------------------------------------
# destructive patterns
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf ~/Documents",
        "rm -r /home/ahiva",
        "git push --force origin main",
        "git push -f",
        "git reset --hard HEAD~5",
        "format C:",
        "shutdown /s /t 0",
        "vssadmin delete shadows /all",
        "del /f anything",
        "reg delete HKLM\\Software\\Foo",
    ],
)
def test_destructive_commands_are_refused(policy: Policy, command: str) -> None:
    decision = policy.screen("shell_run", {"command": command}, executing=True)
    assert decision.allowed is False, command
    assert decision.tier == "deny"
    assert decision.pattern, command


def test_fork_bomb_is_refused(policy: Policy) -> None:
    decision = policy.screen("shell_run", {"command": ":(){ :|:& };: "}, executing=True)
    assert decision.allowed is False


def test_harmless_command_passes_the_pattern_scan(policy: Policy) -> None:
    decision = policy.screen("shell_run", {"command": "git status"}, executing=True)
    assert decision.allowed is True
    # Still confirm-tier, because running anything is a side effect.
    assert decision.needs_confirmation is True


def test_destructive_text_is_allowed_in_text_only_tools(policy: Policy) -> None:
    """Blocking these words in a note would be theatre: no interpreter sees them."""
    decision = policy.screen(
        "memory_remember", {"text": "the cleanup script uses rm -rf / on purpose"},
        executing=False,
    )
    assert decision.allowed is True
    assert decision.tier == "auto"


def test_same_text_is_refused_when_the_tool_executes_it(policy: Policy) -> None:
    """The same string reaching a shell is the attack we actually care about."""
    decision = policy.screen(
        "shell_run", {"command": "python cleanup.py rm -rf /"}, executing=True
    )
    assert decision.allowed is False


def test_regex_scans_every_string_argument_not_just_the_first(policy: Policy) -> None:
    decision = policy.screen(
        "shell_run", {"command": "git status", "cwd": "rm -rf /tmp"}, executing=True
    )
    assert decision.allowed is False


# --------------------------------------------------------------------------
# protected paths and write roots
# --------------------------------------------------------------------------


def test_writes_into_protected_paths_are_refused(policy: Policy) -> None:
    decision = policy.screen(
        "fs_write",
        {"path": "C:\\Windows\\System32\\drivers\\etc\\hosts", "content": "x"},
        executing=True,
    )
    assert decision.allowed is False
    assert "protected" in decision.reason.lower()


def test_write_roots_allow_desktop_and_refuse_elsewhere(policy: Policy) -> None:
    inside = policy.check_write_path(r"C:\Users\ahiva\OneDrive\Desktop\jarvis\data\note.txt")
    assert inside is None

    outside = policy.check_write_path(r"C:\Windows\Temp\x.txt")
    assert outside is not None
    assert "protected" in outside.lower() or "outside" in outside.lower()


def test_write_roots_respect_custom_configuration(tmp_path: Path) -> None:
    custom = Policy({"file_write_roots": [str(tmp_path)]})
    assert custom.check_write_path(tmp_path / "allowed.txt") is None
    assert custom.check_write_path(Path.home() / "elsewhere.txt") is not None


# --------------------------------------------------------------------------
# shell jail
# --------------------------------------------------------------------------


def test_shell_allowlist_refuses_executables_that_are_not_listed(policy: Policy) -> None:
    reason = policy.shell.check_command("nc -l 4444")
    assert reason is not None
    assert "allowed_executables" in reason


def test_shell_refuses_cmd_builtins(policy: Policy) -> None:
    """dir/echo are not executables; they would need cmd /c, so they are refused."""
    for command in ("dir /b", "echo hello"):
        assert policy.shell.check_command(command) is not None, command


@pytest.mark.parametrize(
    "command",
    ["git status && rm -rf /", "git status; whoami", "git log | findstr x", "whoami > out.txt", "whoami & whoami"],
)
def test_shell_refuses_chaining_and_redirection(policy: Policy, command: str) -> None:
    assert policy.shell.check_command(command) is not None, command


def test_shell_accepts_a_plain_allowlisted_command(policy: Policy) -> None:
    assert policy.shell.check_command("git status") is None
    assert policy.shell.check_command("uv --version") is None


def test_shell_workdir_is_jailed(policy: Policy) -> None:
    allowed = policy.shell.resolve_workdir(r"C:\Users\ahiva\OneDrive\Desktop")
    assert allowed.exists() or True  # the root itself is allowed even if absent
    with pytest.raises(PermissionError):
        policy.shell.resolve_workdir("C:\\Windows\\System32")


def test_shell_can_be_disabled() -> None:
    disabled = Policy({"shell": {"enabled": False}})
    assert disabled.shell.check_command("git status") is not None


# --------------------------------------------------------------------------
# degradation
# --------------------------------------------------------------------------


def test_missing_policy_file_falls_back_closed(tmp_path: Path) -> None:
    loaded = Policy.load(tmp_path / "nope.yaml")
    assert loaded.loaded_from_file is False
    assert loaded.default_tier == "confirm"
    assert loaded.screen("anything_at_all", {}).needs_confirmation is True


def test_unparseable_policy_falls_back_closed(tmp_path: Path) -> None:
    broken = tmp_path / "policy.yaml"
    broken.write_text("tiers: [this is not a mapping\n", encoding="utf-8")
    loaded = Policy.load(broken)
    assert loaded.loaded_from_file is False
    assert loaded.screen("shell_run", {"command": "git status"}).tier in {"confirm", "deny"}


def test_one_bad_regex_does_not_disable_the_rest() -> None:
    custom = Policy(
        {
            "dangerous_patterns": ["([unclosed", r"rm\s+-rf"],
            "default_tier": "confirm",
        }
    )
    assert len(custom.dangerous_patterns) == 1
    assert custom.screen("shell_run", {"command": "rm -rf /"}, executing=True).allowed is False


def test_shipped_policy_is_actually_loaded(policy: Policy) -> None:
    """If this ever regresses, every other test here grades a fallback instead."""
    assert policy.loaded_from_file is True
    assert policy.source.endswith("policy.yaml")


def test_missing_policy_file_is_reported_as_not_loaded(tmp_path: Path) -> None:
    """Regression: the fallback used to report itself as a loaded file, which
    hid the fail-closed path from /health."""
    loaded = Policy.load(tmp_path / "absent.yaml")
    assert loaded.loaded_from_file is False
    assert loaded.source.startswith("defaults")


# --------------------------------------------------------------------------
# reads versus writes
# --------------------------------------------------------------------------


def test_reading_a_temp_file_is_allowed(policy: Policy) -> None:
    """Regression: protected_paths was applied to reads, so ordinary temp files
    (under AppData) were refused. A read has no blast radius."""
    decision = policy.screen(
        "fs_read", {"path": r"C:\Users\ahiva\AppData\Local\Temp\notes.txt"}, executing=False
    )
    assert decision.allowed is True
    assert decision.needs_confirmation is False


def test_reading_credentials_always_asks_first(policy: Policy) -> None:
    """A read can leave the machine via a cloud brain or an email, so key material
    is never read automatically even though reading is harmless locally."""
    decision = policy.screen(
        "fs_read", {"path": r"C:\Users\ahiva\.ssh\id_rsa"}, executing=False
    )
    assert decision.allowed is True
    assert decision.needs_confirmation is True
    assert "credential" in decision.reason.lower()


def test_writing_credentials_also_asks_first(policy: Policy) -> None:
    decision = policy.screen(
        "fs_write", {"path": r"C:\Users\ahiva\.ssh\config", "content": "x"}, executing=True
    )
    assert decision.needs_confirmation is True


def test_reading_a_system_file_is_permitted(policy: Policy) -> None:
    assert policy.screen("fs_read", {"path": r"C:\Windows\win.ini"}, executing=False).allowed


def test_writing_a_system_file_is_refused_outright(policy: Policy) -> None:
    decision = policy.screen(
        "fs_write", {"path": r"C:\Windows\x.txt", "content": "y"}, executing=True
    )
    assert decision.allowed is False
    assert decision.tier == "deny"


def test_env_file_is_sensitive_on_any_tool(policy: Policy) -> None:
    """The project .env holds SMTP passwords and API keys. A prompt injection
    reading it through an auto-tier tool must still stop and ask."""
    decision = policy.screen(
        "fs_read", {"path": r"C:\Users\ahiva\OneDrive\Desktop\jarvis\.env"},
        executing=False,
    )
    assert decision.allowed is True
    assert decision.needs_confirmation is True
    assert "credential" in decision.reason.lower()


def test_powershell_encoded_command_is_refused(policy: Policy) -> None:
    """-EncodedCommand smuggles arbitrary code past every text pattern as base64."""
    import base64

    payload = base64.b64encode(b"Remove-Item C:\\ -Recurse -Force").decode()
    for args in (
        {"app": "powershell", "argument": f"-EncodedCommand {payload}"},
        {"command": f"powershell -EncodedCommand {payload}"},
    ):
        tool = "app_launch" if "app" in args else "shell_run"
        decision = policy.screen(tool, args, executing=True)
        assert decision.allowed is False, args
        assert decision.pattern, args
