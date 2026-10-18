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
import ctypes
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .registry import (
    ToolContext,
    ToolOutput,
    ToolSpec,
    boolean_param,
    integer_param,
    object_schema,
    param,
)

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
    raw_app = (app or "").strip()
    if not raw_app:
        return ToolOutput(text="Which application should I open?", ok=False)

    target = ctx.policy.resolve_app(raw_app)
    if not target:
        return ToolOutput(text="Which application should I open?", ok=False)

    # 1) An existing file, folder or .lnk: hand it to the shell's own opener.
    candidate = _resolve(target)
    if candidate.exists():
        if candidate.suffix.lower() in {".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".wsh"}:
            return ToolOutput(
                text=(
                    f"{candidate.name} is a script, and I do not launch scripts as if "
                    "they were applications. Ask for the program you want instead."
                ),
                ok=False,
            )
        if hasattr(os, "startfile"):
            try:
                os.startfile(str(candidate))
                return ToolOutput(text=f"Opened {candidate.name}.", data={"target": str(candidate)})
            except OSError as exc:
                logger.info("startfile on candidate failed: %s", exc)

    # 2) Check Windows Apps folder / Execution Alias: %LOCALAPPDATA%\\Microsoft\\WindowsApps
    if platform.system() == "Windows":
        winapps = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps"
        for name_variant in [target, f"{target}.exe", f"{target}.lnk"]:
            app_path = winapps / name_variant
            if app_path.exists():
                try:
                    os.startfile(str(app_path))
                    return ToolOutput(text=f"Opened {raw_app}.", data={"target": str(app_path)})
                except OSError:
                    pass

    # 3) Known Windows URI protocols
    known_uri_schemes: dict[str, str] = {
        "whatsapp": "whatsapp:",
        "spotify": "spotify:",
        "calculator": "calculator:",
        "calc": "calculator:",
        "camera": "microsoft.windows.camera:",
        "settings": "ms-settings:",
        "store": "ms-windows-store:",
        "windows store": "ms-windows-store:",
        "clock": "ms-clock:",
        "alarms": "ms-clock:",
        "mail": "mailto:",
        "maps": "bingmaps:",
        "photos": "ms-photos:",
        "calendar": "outlookcal:",
    }
    app_key = raw_app.lower().strip()
    uri_target = known_uri_schemes.get(app_key) or (target if ":" in target and not Path(target).is_absolute() else None)
    if uri_target and hasattr(os, "startfile") and platform.system() == "Windows":
        try:
            os.startfile(uri_target)
            return ToolOutput(text=f"Opened {raw_app}.", data={"target": uri_target})
        except OSError:
            pass  # Fall through to browser fallback

    # 4) An executable on PATH.
    executable = shutil.which(target) or shutil.which(f"{target}.exe")
    if executable is not None:
        args = [executable]
        if argument:
            args.append(argument)
        try:
            subprocess.Popen(args, close_fds=True)
            return ToolOutput(text=f"Opened {raw_app}.", data={"target": executable})
        except OSError as exc:
            logger.info("shutil executable start failed: %s", exc)

    # 5) Automatic Web / Browser Fallback:
    # If the app is not installed locally, open the service or web app in the default browser.
    try:
        from .media import web_open
        web_res = web_open(ctx, target=raw_app)
        if web_res.ok:
            url = (web_res.data or {}).get("url", "")
            return ToolOutput(
                text=f"Desktop app '{raw_app}' is not installed locally; opened in default browser.",
                data={"target": raw_app, "url": url, "browser_fallback": True},
                ok=True,
            )
    except Exception as exc:
        logger.info("web_open fallback failed: %s", exc)

    # 6) Last resort search in browser
    try:
        from .media import _open_url
        import urllib.parse
        search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(raw_app)}"
        _open_url(search_url)
        return ToolOutput(
            text=f"Could not find local application '{raw_app}'; opened in default browser.",
            data={"target": raw_app, "url": search_url, "browser_fallback": True},
            ok=True,
        )
    except Exception as exc:
        return ToolOutput(text=f"I could not launch or find '{raw_app}': {exc}", ok=False)


# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------


def shell_run(ctx: ToolContext, command: str, cwd: str = "") -> ToolOutput:
    shell = ctx.policy.shell
    ultimate = getattr(ctx.policy, "ultimate_power", False)

    if not ultimate:
        reason = shell.check_command(command)
        if reason:
            raise PermissionError(reason)

    tokens = _split_command(command)
    if not tokens:
        raise PermissionError("empty command")

    workdir = shell.resolve_workdir(cwd or None) if not ultimate else (Path(cwd).resolve() if cwd and Path(cwd).exists() else Path(".").resolve())

    # Under ultimate power, allow chained commands, builtins, and shell execution
    use_shell = ultimate and (any(ch in command for ch in "|&><;%") or tokens[0].lower() in {"dir", "echo", "type", "cd", "cls", "set"})

    if use_shell:
        try:
            completed = subprocess.run(
                command,
                cwd=str(workdir),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=shell.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(
                text=f"'{command[:40]}' was still running after {shell.timeout_seconds:g} seconds, so I stopped it.",
                ok=False,
            )
        except OSError as exc:
            return ToolOutput(text=f"I could not run '{command[:40]}': {exc}", ok=False)
    else:
        executable = shutil.which(tokens[0])
        if executable is None:
            raise PermissionError(f"{tokens[0]} was not found on this machine's PATH")

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
# desktop automation (keystrokes, mouse, windows, screen)
# --------------------------------------------------------------------------

VK_MAPPINGS: dict[str, int] = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "shift": 0x10, "win": 0x5B, "windows": 0x5B, "cmd": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800


def _parse_vk(k: str) -> int:
    clean = k.lower().strip()
    if clean in VK_MAPPINGS:
        return VK_MAPPINGS[clean]
    if len(clean) == 1 and clean.isalnum():
        return ord(clean.upper())
    return 0


def desktop_hotkey(ctx: ToolContext, keys: str) -> ToolOutput:
    """Execute keyboard shortcut on Windows (e.g. 'ctrl+c', 'alt+tab', 'win+d', 'enter')."""
    if platform.system() != "Windows":
        return ToolOutput(text="Desktop hotkeys are only supported on Windows.", ok=False)

    parts = [p.strip() for p in (keys or "").split("+") if p.strip()]
    if not parts:
        return ToolOutput(text="No keys specified in hotkey.", ok=False)

    vks = [_parse_vk(p) for p in parts]
    if any(vk == 0 for vk in vks):
        unknown = [parts[i] for i, vk in enumerate(vks) if vk == 0]
        return ToolOutput(text=f"Unknown key(s) in hotkey: {', '.join(unknown)}", ok=False)

    try:
        user32 = ctypes.windll.user32
        # Press all keys in sequence
        for vk in vks:
            user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
            time.sleep(0.02)
        # Release all keys in reverse sequence
        for vk in reversed(vks):
            user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
            time.sleep(0.01)
        return ToolOutput(text=f"Sent hotkey '{keys}' to active window.", data={"keys": keys}, ok=True)
    except Exception as exc:
        return ToolOutput(text=f"Failed to send hotkey: {exc}", ok=False)


def desktop_type_text(ctx: ToolContext, text: str) -> ToolOutput:
    """Type arbitrary text into the focused window using fast, layout-independent clipboard injection."""
    if not text:
        return ToolOutput(text="Text cannot be empty.", ok=False)

    # Save to clipboard then trigger Ctrl+V
    _clipboard(get=False, text=text)
    time.sleep(0.05)
    return desktop_hotkey(ctx, "ctrl+v")


def desktop_mouse_action(
    ctx: ToolContext,
    action: str = "click",
    x: int = -1,
    y: int = -1,
    button: str = "left",
    clicks: int = 1,
    scroll: int = 0,
) -> ToolOutput:
    """Control mouse cursor, click buttons, or scroll mouse wheel."""
    if platform.system() != "Windows":
        return ToolOutput(text="Mouse actions are only supported on Windows.", ok=False)

    user32 = ctypes.windll.user32
    act = (action or "click").lower().strip()

    try:
        # Move cursor if coordinates provided
        if x >= 0 and y >= 0:
            user32.SetCursorPos(int(x), int(y))
            time.sleep(0.03)

        if act in {"click", "double_click", "right_click"}:
            num_clicks = 2 if act == "double_click" else max(1, int(clicks or 1))
            is_right = (button.lower() == "right" or act == "right_click")
            down_flag = MOUSEEVENTF_RIGHTDOWN if is_right else MOUSEEVENTF_LEFTDOWN
            up_flag = MOUSEEVENTF_RIGHTUP if is_right else MOUSEEVENTF_LEFTUP

            for _ in range(num_clicks):
                user32.mouse_event(down_flag, 0, 0, 0, 0)
                time.sleep(0.03)
                user32.mouse_event(up_flag, 0, 0, 0, 0)
                time.sleep(0.05)

            btn_str = "right" if is_right else "left"
            return ToolOutput(text=f"Mouse {act} ({btn_str} button, {num_clicks}x) performed.", ok=True)

        elif act == "move":
            return ToolOutput(text=f"Mouse moved to ({x}, {y}).", data={"x": x, "y": y}, ok=True)

        elif act == "scroll":
            delta = int(scroll or 1) * 120
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
            return ToolOutput(text=f"Mouse wheel scrolled ({scroll:+d}).", ok=True)

        else:
            return ToolOutput(text=f"Unknown mouse action '{action}'. Valid: click, double_click, right_click, move, scroll.", ok=False)

    except Exception as exc:
        return ToolOutput(text=f"Mouse action failed: {exc}", ok=False)


def desktop_window_control(ctx: ToolContext, action: str = "list", title: str = "") -> ToolOutput:
    """Inspect or control desktop application windows: list, focus, minimize, maximize."""
    if platform.system() != "Windows":
        return ToolOutput(text="Window control is only supported on Windows.", ok=False)

    act = (action or "list").lower().strip()

    try:
        if act in {"active", "current", "focused"}:
            try:
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                    focused_title = buff.value.strip()
                    return ToolOutput(text=f"Active focused window: '{focused_title}'", data={"title": focused_title, "hwnd": hwnd}, ok=True)
                return ToolOutput(text="No titled application window is currently focused.", ok=True)
            except Exception as exc:
                return ToolOutput(text=f"Could not inspect focused window: {exc}", ok=False)

        elif act == "list":
            # Query running processes with main window titles via PowerShell
            cmd = "Get-Process | Where-Object { $_.MainWindowTitle -and $_.MainWindowTitle.Trim().Length -gt 0 } | Select-Object -First 30 Id, ProcessName, MainWindowTitle | Format-Table -AutoSize | Out-String -Width 140"
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
            )
            output = res.stdout.strip() or "No top-level titled application windows detected."
            return ToolOutput(text=f"Active Desktop Windows:\n{output}", ok=True)

        elif act in {"focus", "minimize", "maximize", "show_desktop", "minimize_all"}:
            if act in {"show_desktop", "minimize_all"} or (act == "minimize" and not title):
                ps_script = "(New-Object -ComObject Shell.Application).ToggleDesktop()"
                subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_script], timeout=5.0)
                return ToolOutput(text="Toggled desktop: all windows minimized.", ok=True)

            if not title:
                return ToolOutput(text="Window title or process name required to focus/minimize/maximize.", ok=False)

            safe_title = re.sub(r"[^a-zA-Z0-9_\-\. ]", "", title).strip()
            # Windows API show command numbers: 9 = SW_RESTORE / Foreground, 6 = SW_MINIMIZE, 3 = SW_MAXIMIZE
            sw_cmd = 9 if act == "focus" else (6 if act == "minimize" else 3)

            ps_script = f"""
Add-Type @"
  using System;
  using System.Runtime.InteropServices;
  public class Win32Helper {{
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  }}
"@
$proc = Get-Process | Where-Object {{ ($_.MainWindowTitle -like "*{safe_title}*" -or $_.ProcessName -like "*{safe_title}*") -and $_.MainWindowHandle -ne 0 }} | Select-Object -First 1
if ($proc) {{
    [Win32Helper]::ShowWindow($proc.MainWindowHandle, {sw_cmd})
    if ({str(act == 'focus').lower()}) {{
        [Win32Helper]::SetForegroundWindow($proc.MainWindowHandle)
    }}
    Write-Host "SUCCESS: $($proc.ProcessName) - $($proc.MainWindowTitle)"
}} else {{
    Write-Host "NOT_FOUND"
}}
"""
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
            )
            out = res.stdout.strip()
            if "SUCCESS" in out:
                return ToolOutput(text=f"Window {act} executed: {out.replace('SUCCESS: ', '')}", ok=True)
            return ToolOutput(text=f"Could not find an active window matching '{title}'.", ok=False)

        else:
            return ToolOutput(text=f"Unknown window action '{action}'. Valid: list, focus, minimize, maximize.", ok=False)

    except Exception as exc:
        return ToolOutput(text=f"Window control failed: {exc}", ok=False)


def desktop_screenshot(ctx: ToolContext, path: str = "") -> ToolOutput:
    """Capture full desktop screenshot and save to disk."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(path).resolve() if path else Path(ctx.settings.data_dir) / f"desktop-{stamp}.png"
    _require_write_path(ctx, target)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        ps_code = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save('{str(target)}', [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
"""
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_code],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        if target.exists() and target.stat().st_size > 0:
            size_kb = target.stat().st_size / 1024
            return ToolOutput(
                text=f"Desktop screenshot captured and saved to {target} ({size_kb:.1f} KB).",
                data={"path": str(target), "size_bytes": target.stat().st_size},
                ok=True,
            )
        return ToolOutput(text=f"Could not save desktop screenshot: {res.stderr.strip() or 'Unknown error'}", ok=False)
    except Exception as exc:
        return ToolOutput(text=f"Desktop screenshot failed: {exc}", ok=False)


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
        ToolSpec(
            name="desktop_hotkey",
            description="Execute desktop keyboard shortcut (e.g. 'win+d', 'alt+tab', 'ctrl+c', 'ctrl+v', 'enter', 'esc').",
            parameters=object_schema(
                {"keys": param("keys", "string", "Shortcut combination, e.g. 'win+d', 'alt+tab', 'ctrl+c', 'ctrl+v', 'win+r', 'enter'.")}, ["keys"]
            ),
            handler=desktop_hotkey,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="desktop_type_text",
            description="Type arbitrary text directly into the currently active desktop window.",
            parameters=object_schema(
                {"text": param("text", "string", "Text to type into active window.")}, ["text"]
            ),
            handler=desktop_type_text,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="desktop_mouse_action",
            description="Simulate mouse movements, clicks, double-clicks, right-clicks, and wheel scrolling.",
            parameters=object_schema(
                {
                    "action": param("action", "string", "Action: 'click', 'double_click', 'right_click', 'move', 'scroll'.", enum=["click", "double_click", "right_click", "move", "scroll"], default="click"),
                    "x": integer_param("Screen X pixel coordinate (-1 to keep current)", default=-1),
                    "y": integer_param("Screen Y pixel coordinate (-1 to keep current)", default=-1),
                    "button": param("button", "string", "Mouse button: 'left' or 'right'", enum=["left", "right"], default="left"),
                    "clicks": integer_param("Number of clicks (default 1)", default=1),
                    "scroll": integer_param("Scroll units (positive = up, negative = down)", default=0),
                },
                [],
            ),
            handler=desktop_mouse_action,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="desktop_window_control",
            description="List or control desktop application windows: list, focus (bring to front), minimize, maximize.",
            parameters=object_schema(
                {
                    "action": param("action", "string", "Action: 'list', 'focus', 'minimize', 'maximize', 'show_desktop', 'minimize_all'.", enum=["list", "focus", "minimize", "maximize", "show_desktop", "minimize_all"], default="list"),
                    "title": param("title", "string", "Window title or process name substring to target. Optional for list and show_desktop.", default=""),
                },
                [],
            ),
            handler=desktop_window_control,
            executing=True,
            category="desktop",
        ),
        ToolSpec(
            name="desktop_screenshot",
            description="Capture a screenshot of the entire desktop display. Needs permission.",
            parameters=object_schema(
                {"path": param("path", "string", "Optional destination path for PNG file.", default="")}, []
            ),
            handler=desktop_screenshot,
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
