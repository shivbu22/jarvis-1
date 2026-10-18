"""The policy gate -- the only code allowed to decide that a tool may run.

The brain emits tool *intents*. Nothing in this process executes an intent until
:meth:`Policy.screen` has returned an allowed decision, so every safety property
the README claims is enforced here and nowhere else. Two ideas carry that:

**Tiers.** Every tool name resolves to exactly one tier, most-restrictive-first:
a `deny` listing beats a `confirm` listing beats an `auto` listing, each of which
may be an exact name or a glob (`mcp__*__delete_*`). A name that matches nothing
gets ``default_tier`` -- ``confirm`` -- so a new tool is never silently powerful.

**Execution surfaces.** Regex deny-lists are scanned against the arguments of
tools that can *execute something* (shell, app launch, filesystem, browser,
anything from an external MCP server). They are deliberately NOT scanned against
text-only tools: blocking the string "rm -rf /" inside a saved note or an email
body would be theatre, since no interpreter ever sees those bytes. This is what
stops a prompt injection in a web page from reaching a command line.

The scan is a backstop, not the primary defence. ``shell_run`` additionally
requires its executable to be allow-listed, refuses shell metacharacters, and
jails its working directory to configured roots, so a command must pass three
independent checks to actually run.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

Tier = str  # "auto" | "confirm" | "deny"

# Most restrictive first: the first tier that claims a tool wins.
TIER_PRECEDENCE: tuple[str, ...] = ("deny", "confirm", "auto")

# Used when config/policy.yaml is missing or unparseable. Fails closed: nothing
# is auto-approved, everything asks first.
DEFAULT_POLICY: dict[str, Any] = {
    "version": 1,
    "default_tier": "confirm",
    "tiers": {
        "auto": [
            "system_info",
            "web_search",
            "web_news",
            "get_weather",
            "memory_remember",
            "memory_recall",
            "memory_list",
            "learn_custom_command",
            "list_custom_commands",
            "delete_custom_command",
            "fs_list",
            "fs_read",
            "clipboard_read",
            "mail_list_unread",
            "mail_read",
            "calendar_list",
            "audit_tail",
            "browser_read",
            "browser_get_elements",
        ],
        "confirm": [
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
            "browser_scroll",
            "browser_press_key",
            "browser_navigate",
            "browser_eval_js",
            "browser_screenshot",
            "browser_close",
            "desktop_hotkey",
            "desktop_type_text",
            "desktop_mouse_action",
            "desktop_window_control",
            "desktop_screenshot",
            "ultimate_exec",
            "ultimate_system_control",
        ],
        "deny": ["shell_run_admin", "registry_write", "disk_format"],
    },
    "dangerous_patterns": [
        r"(?i)(?<![\w-])-enc(?:odedcommand)?\b",
        r"(?i)\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)\b",
        r"(?i)\brm\s+-[a-z]*r\b",
        r"(?i)\b(?:mkfs|fdisk|diskpart)\b",
        r"(?i)\bformat\s+[a-z]:",
        r"(?i)\bcipher\s+/w\b",
        r"(?i)\bvssadmin\s+delete\b",
        r"(?i)\bgit\s+push\b[^\n]*(?:--force\b|(?<![\w-])-f(?![\w-]))",
        r"(?i)\bgit\s+(?:reset\s+--hard|clean\s+-[a-z]*f|filter-branch)\b",
        r"(?i)\breg(?:\.exe)?\s+delete\b",
        r"(?i)\b(?:shutdown|reboot)\s+(?:/s|/r|-h|-r|now)\b",
        r"(?i)\b(?:stop|disable)-service\b",
        r"(?i)\bset-executionpolicy\b",
        r"(?i)\bremove-item\b[^\n]*-recurse[^\n]*-force",
        r"(?i)\bdel\s+/[sfq]\b",
        r":\(\)\s*\{.*\}\s*;\s*:",
        # PowerShell -EncodedCommand smuggles arbitrary code past every pattern
        # as base64. Refused wherever it appears on an execution surface. The
        # lookbehind (not \b) is deliberate: \b before a '-' can never match,
        # because '-' is a non-word character on both sides.
        r"(?i)(?<![\w-])-enc(?:odedcommand)?\b",
    ],
    # Friendly names the model may use for app_launch, mapped to something
    # launchable: an executable on PATH, an absolute path, or a URI handler.
    # Anything not listed here still works if it is an explicit path or is on
    # PATH -- the map exists so "open chrome" does not depend on guessing.
    "apps": {
        "chrome": "chrome",
        "google chrome": "chrome",
        "edge": "msedge",
        "microsoft edge": "msedge",
        "firefox": "firefox",
        "notepad": "notepad",
        "calculator": "calc",
        "calc": "calc",
        "explorer": "explorer",
        "file explorer": "explorer",
        "files": "explorer",
        "vscode": "code",
        "vs code": "code",
        "code": "code",
        "terminal": "wt",
        "windows terminal": "wt",
        "powershell": "powershell",
        "task manager": "taskmgr",
        "settings": "ms-settings:",
        "snipping tool": "snippingtool",
        "paint": "mspaint",
        "spotify": "spotify",
    },
    # Refused for any tool with an execution surface. Meaningful for writes and
    # commands; NOT applied to pure reads (see Policy.screen), because blocking a
    # read of a temp file accomplishes nothing and breaks ordinary work.
    "protected_paths": [
        r"C:\Windows",
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        r"C:\ProgramData",
        r"C:\$Recycle.Bin",
    ],
    # Credentials and key material. Reading these is not blocked outright -- the
    # master may legitimately ask -- but it is never automatic: any tool that
    # touches them is forced to ask first, because a read can be exfiltrated to a
    # cloud brain or an email.
    "sensitive_paths": [
        ".env",
        r"C:\Users\ahiva\.ssh",
        r"C:\Users\ahiva\.aws",
        r"C:\Users\ahiva\.gnupg",
    ],
    "shell": {
        "enabled": True,
        # Real executables only. Shell builtins (dir, echo, type, cd) are
        # deliberately absent: they can only run under `cmd /c`, which would
        # reintroduce the parsing layer this design exists to avoid. Use the
        # fs_* tools to list and read files instead.
        "allowed_executables": [
            "git", "node", "npm", "npx", "python", "python3", "uv", "pip", "pytest",
            "ruff", "where", "whoami", "hostname", "tasklist", "curl", "ffmpeg",
            "tree", "findstr",
        ],
        "workdir_roots": [
            r"C:\Users\ahiva\OneDrive\Desktop",
            r"C:\Users\ahiva\Documents",
            r"C:\Users\ahiva\Downloads",
            r"%TEMP%",
        ],
        "timeout_seconds": 20,
        "max_output_chars": 8000,
        "allow_chained_commands": False,
    },
}

# Characters that turn one command into several, redirect output, or evaluate a
# subshell. Rejected when allow_chained_commands is false. A bare "&" and "^"
# are included because they chain in cmd.exe even though the process is started
# without a shell, and "%" because variable expansion is evaluated before the
# program sees its arguments.
_SHELL_METACHARACTERS = (
    "&&", "&", "||", "|", ";", ">", "<", "^", "%", "`", "$(", "${", "\n", "\r",
)


@dataclass(frozen=True)
class Decision:
    """The verdict for one tool call. ``allowed`` alone decides execution."""

    tool: str
    tier: Tier
    allowed: bool
    needs_confirmation: bool
    reason: str
    pattern: str | None = None

    @property
    def refused(self) -> bool:
        return not self.allowed

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "tier": self.tier,
            "allowed": self.allowed,
            "needs_confirmation": self.needs_confirmation,
            "reason": self.reason,
            "pattern": self.pattern,
        }


@dataclass(frozen=True)
class ShellPolicy:
    enabled: bool = True
    allowed_executables: tuple[str, ...] = ()
    workdir_roots: tuple[Path, ...] = ()
    timeout_seconds: float = 20.0
    max_output_chars: int = 8000
    allow_chained_commands: bool = False

    def allows_executable(self, executable: str) -> bool:
        name = Path(executable.strip().strip('"')).name.lower()
        for suffix in (".exe", ".cmd", ".bat", ".ps1"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name in {e.lower() for e in self.allowed_executables}

    def check_command(self, command: str) -> str | None:
        """Return a refusal reason, or None when the command shape is acceptable."""
        if not self.enabled:
            return "shell execution is disabled in config/policy.yaml"
        stripped = (command or "").strip()
        if not stripped:
            return "empty command"
        if not self.allow_chained_commands:
            for token in _SHELL_METACHARACTERS:
                if token in stripped:
                    return f"chained commands or redirection are not allowed (found {token!r})"
        executable = _first_token(stripped)
        if not executable:
            return "could not determine an executable"
        if not self.allows_executable(executable):
            return (
                f"{Path(executable).name!r} is not in shell.allowed_executables "
                "(config/policy.yaml)"
            )
        return None

    def resolve_workdir(self, cwd: str | None) -> Path:
        """Jail a working directory to the configured roots.

        Raises:
            PermissionError: when the path escapes every configured root.
        """
        if not self.workdir_roots:
            raise PermissionError("no shell.workdir_roots configured; refusing to run anywhere")
        candidate = Path(os.path.expandvars(cwd)).expanduser() if cwd else self.workdir_roots[0]
        resolved = candidate.resolve()
        for root in self.workdir_roots:
            root_resolved = root.resolve()
            if resolved == root_resolved or root_resolved in resolved.parents:
                return resolved
        allowed = ", ".join(str(r) for r in self.workdir_roots)
        raise PermissionError(f"{resolved} is outside the allowed shell roots ({allowed})")


class Policy:
    """Loaded once at startup, hot-reloadable, and fail-closed at every step."""

    def __init__(self, data: dict[str, Any] | None = None, *, source: str = "defaults") -> None:
        self.source = source
        # The default-sourced instances label themselves "defaults (...)", so a
        # prefix test is what actually distinguishes a loaded file from a
        # fallback -- comparing against the bare word "defaults" silently reported
        # a missing policy file as loaded, which hid the fail-closed path.
        self.loaded_from_file = bool(source) and not source.startswith("defaults")
        data = data or DEFAULT_POLICY
        self.default_tier: str = str(data.get("default_tier", "confirm")).lower()
        self.tiers: dict[str, tuple[str, ...]] = {
            tier: tuple(str(x) for x in (data.get("tiers") or {}).get(tier, ()))
            for tier in TIER_PRECEDENCE
        }
        self.dangerous_patterns: tuple[re.Pattern[str], ...] = _compile_all(
            data.get("dangerous_patterns")
        )
        self.protected_paths: tuple[str, ...] = tuple(
            str(p).rstrip("\\/") for p in (data.get("protected_paths") or ())
        )
        self.sensitive_paths: tuple[str, ...] = tuple(
            str(p).rstrip("\\/") for p in (data.get("sensitive_paths") or ())
        )
        self.shell = _build_shell_policy(data.get("shell") or {})
        # Where the file tools may write. Falls back to the shell roots so a
        # single list governs both, while still allowing them to diverge.
        write_roots = _build_roots(data.get("file_write_roots"))
        self.write_roots: tuple[Path, ...] = write_roots or self.shell.workdir_roots
        self.apps: dict[str, str] = {
            str(key).strip().lower(): str(value).strip()
            for key, value in (data.get("apps") or {}).items()
            if str(key).strip()
        }
        # Ultimate Power / Master Full Permission Mode
        # When enabled by the master, all tools are authorized without confirmation barriers
        self.ultimate_power: bool = bool(data.get("ultimate_power", False) if data else False)

    def grant_ultimate_permission(self, enabled: bool = True) -> None:
        """Grant or revoke unrestricted autonomous permission mode."""
        self.ultimate_power = enabled

    @property
    def ultimate_permission(self) -> bool:
        return getattr(self, "ultimate_power", False)

    # -- construction -----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Policy:
        """Load a policy file. A missing or broken file degrades, never crashes."""
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return cls(None, source=f"defaults (no file at {path})")
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            return cls(None, source=f"defaults (unparseable {path.name}: {exc.__class__.__name__})")
        if not isinstance(data, dict):
            return cls(None, source=f"defaults ({path.name} is not a mapping)")
        return cls(data, source=str(path))

    # -- tier resolution --------------------------------------------------

    def tier_for(self, tool: str) -> str:
        """Resolve a tool name to a tier. Exact names beat globs; deny wins."""
        for tier in TIER_PRECEDENCE:
            if tool in self.tiers[tier]:
                return tier
        for tier in TIER_PRECEDENCE:
            for pattern in self.tiers[tier]:
                if any(ch in pattern for ch in "*?[") and fnmatch.fnmatch(tool, pattern):
                    return tier
        return self.default_tier

    def screen(self, tool: str, args: dict[str, Any] | None = None, *, executing: bool = True) -> Decision:
        """Decide whether one tool call may run.

        Args:
            tool: the tool name.
            args: the arguments the model supplied.
            executing: True when the call has a side effect or an execution
                surface (writing, deleting, launching, running, browsing). The
                regex deny-list and `protected_paths` then apply to its arguments.
                Pure reads pass False: a path is not a command, and refusing to
                read a file under AppData protects nothing.
        """
        args = args or {}
        strings = _arg_strings(args)

        # Master Ultimate Permission Gate: when ultimate_power is granted, confirmation barriers
        # are lifted while destructive host corruption is safely gated
        if getattr(self, "ultimate_power", False):
            if executing:
                for pattern in self.dangerous_patterns:
                    if any(pattern.search(value) for value in strings):
                        return Decision(
                            tool=tool,
                            tier="deny",
                            allowed=False,
                            needs_confirmation=False,
                            reason=f"refused: arguments match a destructive pattern ({pattern.pattern})",
                            pattern=pattern.pattern,
                        )
            return Decision(
                tool=tool,
                tier="auto",
                allowed=True,
                needs_confirmation=False,
                reason="master_ultimate_permission_granted",
            )

        # Credentials: allowed, but never silently. Checked for every tool, reads
        # included, because the risk here is disclosure rather than destruction.
        sensitive = self._path_hit(strings, self.sensitive_paths)

        if executing:
            for pattern in self.dangerous_patterns:
                if any(pattern.search(value) for value in strings):
                    return Decision(
                        tool=tool,
                        tier="deny",
                        allowed=False,
                        needs_confirmation=False,
                        reason=(
                            "refused: arguments match a destructive pattern "
                            f"({pattern.pattern})"
                        ),
                        pattern=pattern.pattern,
                    )

            protected = self._path_hit(strings, self.protected_paths)
            if protected is not None:
                return Decision(
                    tool=tool,
                    tier="deny",
                    allowed=False,
                    needs_confirmation=False,
                    reason=f"refused: {protected} is a protected system path",
                    pattern=protected,
                )

        if sensitive is not None and self.tier_for(tool) == "auto":
            return Decision(
                tool=tool,
                tier="confirm",
                allowed=True,
                needs_confirmation=True,
                reason=f"{sensitive} holds credentials, so this needs approval",
                pattern=sensitive,
            )

        tier = self.tier_for(tool)
        if tier == "deny":
            return Decision(
                tool=tool,
                tier=tier,
                allowed=False,
                needs_confirmation=False,
                reason="refused: this tool is on the deny list in config/policy.yaml",
            )
        if tier == "confirm":
            return Decision(
                tool=tool,
                tier=tier,
                allowed=True,
                needs_confirmation=True,
                reason="requires confirmation before it runs",
            )
        return Decision(
            tool=tool,
            tier="auto",
            allowed=True,
            needs_confirmation=False,
            reason="read-only, runs without asking",
        )

    def check_write_path(self, path: str | Path) -> str | None:
        """Return a refusal reason when writing to `path` is not permitted.

        Applied on top of the tier system: a confirmed write is still refused if
        the destination is a protected system path or outside the configured
        write roots, because "the master said yes" should not mean "anywhere".
        """
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, ValueError) as exc:
            return f"{path!r} is not a usable path ({exc.__class__.__name__})"

        protected = self._protected_hit([str(resolved)])
        if protected is not None:
            return f"{protected} is a protected system path"

        if not self.write_roots:
            return "no file_write_roots are configured, so no write target is trusted"
        for root in self.write_roots:
            try:
                root_resolved = root.resolve()
            except OSError:
                continue
            if resolved == root_resolved or root_resolved in resolved.parents:
                return None
        allowed = ", ".join(str(r) for r in self.write_roots)
        return f"{resolved} is outside the allowed write roots ({allowed})"

    def resolve_app(self, name: str) -> str:
        """Map a friendly app name to a launch target, or pass an explicit one through."""
        candidate = (name or "").strip()
        if not candidate:
            return ""
        return self.apps.get(candidate.lower(), candidate)

    def _path_hit(self, strings: list[str], needles: tuple[str, ...]) -> str | None:
        if not needles:
            return None
        for value in strings:
            lowered = value.lower().replace("/", "\\")
            for needle in needles:
                if needle.lower().replace("/", "\\") in lowered:
                    return needle
        return None

    def _protected_hit(self, strings: list[str]) -> str | None:
        """Kept for :meth:`check_write_path`, which is write-specific by nature."""
        return self._path_hit(strings, self.protected_paths)

    # -- introspection ----------------------------------------------------

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "loaded_from_file": self.loaded_from_file,
            "default_tier": self.default_tier,
            "tiers": {tier: list(names) for tier, names in self.tiers.items()},
            "dangerous_patterns": [p.pattern for p in self.dangerous_patterns],
            "protected_paths": list(self.protected_paths),
            "sensitive_paths": list(self.sensitive_paths),
            "write_roots": [str(p) for p in self.write_roots],
            "apps": dict(self.apps),
            "shell": {
                "enabled": self.shell.enabled,
                "allowed_executables": list(self.shell.allowed_executables),
                "workdir_roots": [str(p) for p in self.shell.workdir_roots],
                "timeout_seconds": self.shell.timeout_seconds,
                "allow_chained_commands": self.shell.allow_chained_commands,
            },
        }


def _compile_all(patterns: Any) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for raw in patterns or ():
        try:
            compiled.append(re.compile(str(raw)))
        except re.error:
            # A bad regex must never disable the rest of the deny-list.
            continue
    return tuple(compiled)


def _build_roots(entries: Any) -> tuple[Path, ...]:
    roots: list[Path] = []
    for entry in entries or ():
        try:
            roots.append(Path(os.path.expandvars(str(entry))))
        except (TypeError, ValueError):
            continue
    return tuple(roots)


def _build_shell_policy(raw: dict[str, Any]) -> ShellPolicy:
    roots = _build_roots(raw.get("workdir_roots"))
    return ShellPolicy(
        enabled=bool(raw.get("enabled", True)),
        allowed_executables=tuple(str(x) for x in raw.get("allowed_executables") or ()),
        workdir_roots=tuple(roots),
        timeout_seconds=float(raw.get("timeout_seconds", 20)),
        max_output_chars=int(raw.get("max_output_chars", 8000)),
        allow_chained_commands=bool(raw.get("allow_chained_commands", False)),
    )


def _arg_strings(args: dict[str, Any]) -> list[str]:
    """Every scalar in the call, as text.

    Patterns are matched per value rather than against a serialised blob so that a
    match can be reported with the field it came from, and so that a harmless
    value cannot be split by JSON quoting into looking like a dangerous one.
    """
    strings: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif value is not None:
            strings.append(str(value))

    walk(args)
    return strings


def _first_token(command: str) -> str:
    """First whitespace-delimited token, honouring simple quoting."""
    command = command.strip()
    if not command:
        return ""
    if command[0] in {'"', "'"}:
        quote = command[0]
        end = command.find(quote, 1)
        if end > 0:
            return command[1:end]
    return command.split()[0]
