"""System tools: what machine am I on, and what have I actually been doing.

`system_info` is read-only and exists so the model stops guessing about the
machine it is running on -- which OS, how much disk is free, what the time is.
`audit_tail` exposes the audit log through conversation, because "what did you do
while I was away" is a question a trusted assistant must be able to answer.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param


def _total_memory_gb() -> float | None:
    """Physical RAM via the Win32 API; None on other platforms."""
    if platform.system() != "Windows":
        return None
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return round(status.ullTotalPhys / (1024**3), 1)
    except Exception:
        return None


def system_info(ctx: ToolContext) -> ToolOutput:
    disks = []
    for letter in ("C:\\", "D:\\"):
        try:
            usage = shutil.disk_usage(letter)
        except OSError:
            continue
        disks.append(
            {
                "drive": letter,
                "free_gb": round(usage.free / (1024**3), 1),
                "total_gb": round(usage.total / (1024**3), 1),
            }
        )

    info = {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "cpu_cores": os.cpu_count(),
        "ram_gb": _total_memory_gb(),
        "disks": disks,
        "time": time.strftime("%A, %d %B %Y, %H:%M"),
        "python": sys.version.split()[0],
        "jarvis_root": str(ctx.settings.root),
    }

    parts = [f"{info['os']} on {info['machine']}", f"user {info['user']}"]
    if info["cpu_cores"]:
        parts.append(f"{info['cpu_cores']} CPU cores")
    if info["ram_gb"]:
        parts.append(f"{info['ram_gb']} GB RAM")
    for disk in disks:
        parts.append(f"{disk['drive']} has {disk['free_gb']} GB free")
    parts.append(f"local time is {info['time']}")

    return ToolOutput(text=", ".join(parts) + ".", data=info)


def audit_tail(ctx: ToolContext, limit: int = 20, tool: str = "") -> ToolOutput:
    limit = max(1, min(int(limit or 20), 100))
    entries = ctx.audit.tail(limit * 3 if tool else limit)
    if tool:
        entries = [e for e in entries if e.get("tool") == tool][-limit:]

    if not entries:
        return ToolOutput(text="The audit log is empty.", data={"entries": []})

    lines = ["Recent activity:"]
    for entry in entries:
        when = time.strftime("%H:%M", time.localtime(float(entry.get("ts", 0))))
        event = entry.get("event", "?")
        if event == "tool_call":
            verdict = entry.get("decision", "")
            lines.append(f"{when} {entry.get('tool')} [{verdict}] {entry.get('summary', '')}".rstrip())
        elif event == "confirmation":
            state = "approved" if entry.get("approved") else f"refused ({entry.get('source')})"
            lines.append(f"{when} confirmation for {entry.get('tool')}: {state}")
        else:
            lines.append(f"{when} {event}")
    return ToolOutput(text="\n".join(lines), data={"entries": entries})


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="system_info",
            description=(
                "Facts about the machine Jarvis runs on: operating system, user, CPU "
                "cores, memory, free disk space and the local time."
            ),
            parameters=object_schema({}, []),
            handler=system_info,
            executing=False,
            category="system",
        ),
        ToolSpec(
            name="audit_tail",
            description=(
                "Read the log of what Jarvis has actually done, including refusals and "
                "confirmations. Use when asked what you did, or when something failed."
            ),
            parameters=object_schema(
                {
                    "limit": param(
                        "limit", "integer", "How many entries to return.",
                        default=20, minimum=1, maximum=100,
                    ),
                    "tool": param(
                        "tool", "string", "Optional tool name to filter by.", default="",
                    ),
                },
                [],
            ),
            handler=audit_tail,
            executing=False,
            category="system",
        ),
    ]
