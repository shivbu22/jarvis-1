"""Desktop tools -- the ones that change the world, so every one of them is guarded.

Design decisions worth stating, because they are the difference between an
assistant and a liability:

**Deletion is a move, not an unlink.** ``fs_delete`` relocates the target into
``data/trash/`` and reports where it went. The confirmation prompt already asks
permission, but a misheard name should still be recoverable -- "delete the old
reports" said once is not worth a permanent loss.

**The shell runs programs, not shell lines.** ``shell_run`` resolves its first
token through ``shutil.which`` and executes with ``shell=False``, so there is no
interpreter to reinterpret an argument. Shell builtins like ``dir`` and ``echo``
are intentionally unsupported; ``fs_list`` and ``fs_read`` cover them without a
parsing layer.

**Three independent checks gate every command**: the token must be allow-listed,
it must exist on PATH, and the working directory must resolve inside a configured
root. ``shell_run_admin`` exists purely so that an attempt on it is a *logged
refusal* rather than an unknown-tool error.
"""

from __future__ import annotations

import contextlib
import os
import platform
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

MAX_READ_BYTES = 8 * 1024 * 1024
DIRECTORY_ENTRY_LIMIT = 400


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _resolve(path: str) -> Path:
    return Path(os.path.expandvars(str(path))).expanduser()


def _require_write_path(ctx: ToolContext, target: Path) -> None:
    """Refuse a write/delete target that policy does not trust."""
    reason = ctx.policy.check_write_path(target)
    if reason:
        raise PermissionError(reason)


def _trash_dir(ctx: ToolContext) -> Path:
    trash = Path(ctx.settings.data_dir) / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    return trash


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


# --------------------------------------------------------------------------
# filesystem
# --------------------------------------------------------------------------


def fs_list(ctx: ToolContext, path: str = ".", pattern: str = "", limit: int = 200) -> ToolOutput:
    target = _resolve(path)
    if not target.exists():
        return ToolOutput(text=f"There is nothing at {target}.", ok=False)
    if target.is_file():
        stat = target.stat()
        return ToolOutput(
            text=f"{target} is a file of {_human_size(stat.st_size)}, modified "
            f"{time.strftime('%d %B %H:%M', time.localtime(stat.st_mtime))}.",
            data={"path": str(target), "type": "file"},
        )

    limit = max(1, min(int(limit or 200), DIRECTORY_ENTRY_LIMIT))
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as exc:
        return ToolOutput(text=f"I cannot read {target}: {exc}", ok=False)

    for child in children:
        if pattern and pattern.lower() not in child.name.lower():
            continue
        try:
            stat = child.stat()
            size = stat.st_size
            modified = stat.st_mtime
        except OSError:
            size, modified = 0, 0.0
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size_bytes": size,
                "modified": modified,
            }
        )
        if len(entries) >= limit:
            break

    if not entries:
        suffix = f" matching '{pattern}'" if pattern else ""
        return ToolOutput(text=f"{target} contains no entries{suffix}.", data={"path": str(target)})

    folders = [e["name"] for e in entries if e["type"] == "dir"]
    files = [e for e in entries if e["type"] == "file"]
    lines = [f"{target} contains {len(folders)} folders and {len(files)} files:"]
    for entry in entries[:60]:
        if entry["type"] == "dir":
            lines.append(f"- {entry['name']}/")
        else:
            lines.append(f"- {entry['name']} ({_human_size(entry['size_bytes'])})")
    if len(entries) > 60:
        lines.append(f"...and {len(entries) - 60} more.")

    return ToolOutput(
        text="\n".join(lines),
        data={"path": str(target), "count": len(entries), "entries": entries},
    )


def fs_read(ctx: ToolContext, path: str, max_chars: int = 8000) -> ToolOutput:
    target = _resolve(path)
    if not target.exists():
        return ToolOutput(text=f"There is nothing at {target}.", ok=False)
    if target.is_dir():
        return ToolOutput(text=f"{target} is a folder, not a file. Use fs_list.", ok=False)

    try:
        size = target.stat().st_size
    except OSError as exc:
        return ToolOutput(text=f"I cannot stat {target}: {exc}", ok=False)
    if size > MAX_READ_BYTES:
        return ToolOutput(
            text=f"{target} is {_human_size(size)}, too large to read as text.",
            ok=False,
        )

    try:
        raw = target.read_bytes()
    except OSError as exc:
        return ToolOutput(text=f"I could not read {target}: {exc}", ok=False)

    if b"\x00" in raw[:2048]:
        return ToolOutput(text=f"{target} looks like a binary file, so I will not read it.", ok=False)

    text = raw.decode("utf-8", errors="replace")
    max_chars = max(200, min(int(max_chars or 8000), 40000))
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[{len(text) - max_chars} more characters]"

    return ToolOutput(
        text=f"Contents of {target}:\n{text}",
        data={"path": str(target), "size_bytes": size, "text": text},
    )


def fs_write(
    ctx: ToolContext, path: str, content: str, mode: str = "overwrite"
) -> ToolOutput:
    target = _resolve(path)
    _require_write_path(ctx, target)
    if target.is_dir():
        return ToolOutput(text=f"{target} is a folder.", ok=False)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return ToolOutput(text=f"I could not write {target}: {exc}", ok=False)

    verb = "Appended to" if mode == "append" else "Wrote"
    return ToolOutput(
        text=f"{verb} {target} ({len(content)} characters).",
        data={"path": str(target), "bytes": len(content.encode('utf-8')), "mode": mode},
    )


def fs_mkdir(ctx: ToolContext, path: str) -> ToolOutput:
    target = _resolve(path)
    _require_write_path(ctx, target)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ToolOutput(text=f"I could not create {target}: {exc}", ok=False)
    return ToolOutput(text=f"Created the folder {target}.", data={"path": str(target)})


def fs_delete(ctx: ToolContext, path: str) -> ToolOutput:
    """Move a file or folder to data/trash, never unlink it."""
    target = _resolve(path)
    if not target.exists():
        return ToolOutput(text=f"There is nothing at {target}.", ok=False)
    _require_write_path(ctx, target)

    resolved = target.resolve()
    for root in ctx.policy.write_roots:
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        if resolved == root_resolved:
            return ToolOutput(
                text=f"{target} is a configured root folder and will not be deleted.", ok=False
            )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = _trash_dir(ctx) / f"{stamp}-{target.name}"
    counter = 1
    while destination.exists():
        destination = _trash_dir(ctx) / f"{stamp}-{counter}-{target.name}"
        counter += 1

    try:
        shutil.move(str(target), str(destination))
    except (OSError, shutil.Error) as exc:
        return ToolOutput(text=f"I could not remove {target}: {exc}", ok=False)

    return ToolOutput(
        text=(
            f"Moved {target} to the Jarvis trash ({destination}). "
            "It can be recovered from there."
        ),
        data={"path": str(target), "trashed_to": str(destination)},
    )


# --------------------------------------------------------------------------
# apps
# --------------------------------------------------------------------------


def app_launch(ctx: ToolContext, app: str, argument: str = "") -> ToolOutput:
    target = ctx.policy.resolve_app(app)
    if not target:
        return ToolOutput(text="Which application should I open?", ok=False)

    # 1) An existing file, folder or .lnk: hand it to the shell's own opener.
    candidate = _resolve(target)
    if candidate.exists():
        if not hasattr(os, "startfile"):
            return ToolOutput(text="Opening files is only supported on Windows.", ok=False)
        try:
            os.startfile(str(candidate))
        except OSError as exc:
            return ToolOutput(text=f"I could not open {candidate}: {exc}", ok=False)
        return ToolOutput(text=f"Opened {candidate.name}.", data={"target": str(candidate)})

    # 2) A URI handler such as ms-settings:.
    if ":" in target and os.sep not in target and "/" not in target:
        if not hasattr(os, "startfile"):
            return ToolOutput(text="URI launching is only supported on Windows.", ok=False)
        try:
            os.startfile(target)
        except OSError as exc:
            return ToolOutput(text=f"I could not open {target}: {exc}", ok=False)
        return ToolOutput(text=f"Opened {target}.", data={"target": target})

    # 3) An executable on PATH.
    executable = shutil.which(target)
    if executable is None:
        known = ", ".join(sorted(ctx.policy.apps)[:12])
        return ToolOutput(
            text=(
                f"I could not find an application called '{app}'. "
                f"Known names include: {known}. "
                "Add more in config/policy.yaml under 'apps'."
            ),
            ok=False,
        )

    args = [executable]
    if argument:
        args.append(argument)
    try:
        subprocess.Popen(args, close_fds=True)
    except OSError as exc:
        return ToolOutput(text=f"I could not start {app}: {exc}", ok=False)
    return ToolOutput(text=f"Opened {app}.", data={"target": executable})


# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------


def shell_run(ctx: ToolContext, command: str, cwd: str = "") -> ToolOutput:
    shell = ctx.policy.shell

    reason = shell.check_command(command)
    if reason:
        raise PermissionError(reason)

    tokens = _split_command(command)
    if not tokens:
        raise PermissionError("empty command")

    executable = shutil.which(tokens[0])
    if executable is None:
        raise PermissionError(f"{tokens[0]} was not found on this machine's PATH")

    workdir = shell.resolve_workdir(cwd or None)  # raises PermissionError when outside

    try:
        completed = subprocess.run(
            [executable, *tokens[1:]],
            cwd=str(workdir),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=shell.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ToolOutput(
            text=f"'{tokens[0]}' was still running after {shell.timeout_seconds:g} seconds, so I stopped it.",
            ok=False,
        )
    except OSError as exc:
        return ToolOutput(text=f"I could not run '{tokens[0]}': {exc}", ok=False)

    limit = shell.max_output_chars
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    body = stdout
    if stderr:
        body = f"{body}\n[stderr]\n{stderr}" if body else stderr
    if len(body) > limit:
        body = body[:limit] + f"\n...[truncated, {len(body) - limit} more characters]"
    if not body:
        body = "(no output)"

    return ToolOutput(
        text=f"'{command}' exited with code {completed.returncode} in {workdir}:\n{body}",
        data={
            "command": command,
            "cwd": str(workdir),
            "exit_code": completed.returncode,
            "stdout": stdout[:limit],
            "stderr": stderr[:limit],
        },
        ok=completed.returncode == 0,
    )


def shell_run_admin(ctx: ToolContext, command: str = "") -> ToolOutput:
    """Unreachable by design: policy denies this name before dispatch.

    It exists so that an attempt to escalate is recorded as a refusal with a
    reason, and so that the handler itself refuses if the policy were ever
    misconfigured to allow it.
    """
    raise PermissionError(
        "I will not run commands with administrator privileges. "
        "Ask me for the exact command and run it yourself if it is needed."
    )


def registry_write(ctx: ToolContext, key: str = "", value: str = "") -> ToolOutput:
    """Denied by policy. Present so the refusal is explicit and logged."""
    raise PermissionError("I do not modify the Windows registry.")


def disk_format(ctx: ToolContext, drive: str = "") -> ToolOutput:
    """Denied by policy. Present so the refusal is explicit and logged."""
    raise PermissionError("I will not format a drive.")


def _split_command(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError as exc:
        raise PermissionError(f"I could not parse that command: {exc}") from exc
    cleaned: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
            token = token[1:-1]
        if token:
            cleaned.append(token)
    return cleaned


# --------------------------------------------------------------------------
# clipboard
# --------------------------------------------------------------------------


def clipboard_read(ctx: ToolContext) -> ToolOutput:
    text = _clipboard(get=True)
    if text is None:
        return ToolOutput(
            text="I cannot reach the clipboard (no desktop session available).", ok=False
        )
    if not text.strip():
        return ToolOutput(text="The clipboard is empty.", data={"text": ""})
    preview = text if len(text) <= 2000 else text[:2000] + "..."
    return ToolOutput(text=f"The clipboard contains:\n{preview}", data={"text": text})


def clipboard_write(ctx: ToolContext, text: str) -> ToolOutput:
    if _clipboard(get=False, text=text) is None:
        return ToolOutput(text="I cannot reach the clipboard.", ok=False)
    return ToolOutput(text=f"Copied {len(text)} characters to the clipboard.", data={"text": text})


def _clipboard(*, get: bool, text: str = "") -> str | None:
    """Read or write the system clipboard via tkinter, which ships with CPython."""
    if platform.system() != "Windows":
        return None
    try:
        import tkinter
    except ImportError:
        return None
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        if get:
            try:
                return str(root.clipboard_get())
            except tkinter.TclError:
                return ""
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        return text
    except tkinter.TclError:
        return None
    finally:
        if root is not None:
            with contextlib.suppress(tkinter.TclError):
                root.destroy()


# --------------------------------------------------------------------------
# specs
# --------------------------------------------------------------------------


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="fs_list",
            description="List the contents of a folder, with sizes.",
            parameters=object_schema(
                {
                    "path": param("path", "string", "Folder to list.", default="."),
                    "pattern": param(
                        "pattern", "string", "Optional case-insensitive name filter.",
                        default="",
                    ),
                    "limit": param(
                        "limit", "integer", "Maximum entries to return.",
                        default=200, minimum=1, maximum=400,
                    ),
                },
                [],
            ),
            handler=fs_list,
            # Reads are not an execution surface: a path is not a command, so the
            # deny-list and protected_paths do not apply. Writing is guarded
            # separately by Policy.check_write_path inside the handlers below.
            executing=False,
            category="desktop",
        ),
        ToolSpec(
            name="fs_read",
            description="Read a text file and return its contents.",
            parameters=object_schema(
                {
                    "path": param("path", "string", "File to read."),
                    "max_chars": param(
                        "max_chars", "integer", "Maximum characters to return.",
                        default=8000, minimum=200, maximum=40000,
                    ),
                },
                ["path"],
            ),
            handler=fs_read,
            executing=False,  # see fs_list
            category="desktop",
        ),
        ToolSpec(
            name="fs_write",
            description=(
                "Create or overwrite a text file. Needs the master's permission. "
                "Use mode=append to add to an existing file."
            ),
            parameters=object_schema(
                {
                    "path": param("path", "string", "File to write."),
                    "content": param("content", "string", "Text to write."),
                    "mode": param(
                        "mode", "string", "overwrite replaces the file, append adds to it.",
                        enum=["overwrite", "append"], default="overwrite",
                    ),
                },
                ["path", "content"],
            ),
            handler=fs_write,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="fs_mkdir",
            description="Create a folder, including parents. Needs permission.",
            parameters=object_schema(
                {"path": param("path", "string", "Folder to create.")}, ["path"]
            ),
            handler=fs_mkdir,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="fs_delete",
            description=(
                "Remove a file or folder. Needs permission. The item is moved to "
                "Jarvis's own trash folder rather than erased, and the location is "
                "reported so it can be recovered."
            ),
            parameters=object_schema(
                {"path": param("path", "string", "File or folder to remove.")}, ["path"]
            ),
            handler=fs_delete,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="app_launch",
            description=(
                "Open an application, file, folder or Windows settings page. Needs "
                "permission. Accepts friendly names such as chrome, notepad, "
                "calculator, explorer, vscode."
            ),
            parameters=object_schema(
                {
                    "app": param("app", "string", "Application name or path to open."),
                    "argument": param(
                        "argument", "string", "Optional argument, such as a file to open.",
                        default="",
                    ),
                },
                ["app"],
            ),
            handler=app_launch,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="shell_run",
            description=(
                "Run a command-line program on this machine and return its output. "
                "Needs permission. Only allow-listed executables can be run, the "
                "working directory must be inside the configured roots, and shell "
                "chaining such as && or | is rejected."
            ),
            parameters=object_schema(
                {
                    "command": param("command", "string", "The full command line to run."),
                    "cwd": param(
                        "cwd", "string", "Optional working directory inside the allowed roots.",
                        default="",
                    ),
                },
                ["command"],
            ),
            handler=shell_run,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="clipboard_read",
            description="Read the current clipboard contents.",
            parameters=object_schema({}, []),
            handler=clipboard_read,
            executing=False,  # reading the clipboard has no side effect
            category="desktop",
        ),
        ToolSpec(
            name="clipboard_write",
            description="Copy text to the clipboard. Needs permission.",
            parameters=object_schema(
                {"text": param("text", "string", "Text to place on the clipboard.")}, ["text"]
            ),
            handler=clipboard_write,
            executing=True,
            category="desktop",
        ),
        # --- registered only so that attempts are refused and logged ---
        ToolSpec(
            name="shell_run_admin",
            description="Run a command with administrator privileges. Always refused.",
            parameters=object_schema(
                {"command": param("command", "string", "Command line.", default="")}, []
            ),
            handler=shell_run_admin,
            executing=True,
            category="desktop-restricted",
        ),
        ToolSpec(
            name="registry_write",
            description="Modify the Windows registry. Always refused.",
            parameters=object_schema(
                {
                    "key": param("key", "string", "Registry key.", default=""),
                    "value": param("value", "string", "Value to write.", default=""),
                },
                [],
            ),
            handler=registry_write,
            executing=True,
            category="desktop-restricted",
        ),
        ToolSpec(
            name="disk_format",
            description="Format a drive. Always refused.",
            parameters=object_schema(
                {"drive": param("drive", "string", "Drive letter.", default="")}, []
            ),
            handler=disk_format,
            executing=True,
            category="desktop-restricted",
        ),
    ]
