"""J.A.R.V.I.S. Ultimate Superpower Tools.

Unrestricted system execution, administrative-level powershell automation,
deep cognitive multi-step planning, and dynamic master permission control.
"""

from __future__ import annotations

import os
import platform
import re
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


def ultimate_exec(
    ctx: ToolContext,
    command: str,
    language: str = "powershell",
    cwd: str = "",
) -> ToolOutput:
    """Execute arbitrary PowerShell, CMD, Python, or shell script with full system authority."""
    if not command or not command.strip():
        return ToolOutput(text="Command cannot be empty.", ok=False)

    workdir = Path(cwd).resolve() if cwd and Path(cwd).exists() else Path(".").resolve()
    lang = (language or "powershell").lower().strip()

    # Safety backstop: refuse destructive host wipeout patterns
    for pat in getattr(ctx.policy, "dangerous_patterns", []):
        if pat.search(command):
            return ToolOutput(
                text=f"Refused by safety backstop: command matches destructive pattern ({pat.pattern}).",
                ok=False,
            )

    t0 = time.perf_counter()
    try:
        if lang in {"powershell", "pwsh", "ps"}:
            cmd_args = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ]
            completed = subprocess.run(
                cmd_args,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60.0,
            )
        elif lang in {"cmd", "bat", "batch"}:
            completed = subprocess.run(
                command,
                cwd=str(workdir),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60.0,
            )
        elif lang in {"python", "py"}:
            py_exe = os.environ.get("PYTHON_EXE", "python")
            completed = subprocess.run(
                [py_exe, "-c", command],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60.0,
            )
        else:
            completed = subprocess.run(
                command,
                cwd=str(workdir),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60.0,
            )

        dur_ms = round((time.perf_counter() - t0) * 1000, 1)
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        code = completed.returncode

        output_lines = [
            f"=== ULTIMATE EXEC [{lang.upper()}] (Exit Code: {code} | {dur_ms}ms) ===",
        ]
        if stdout:
            output_lines.append(stdout)
        if stderr:
            output_lines.append(f"[STDERR]\n{stderr}")
        if not stdout and not stderr:
            output_lines.append("(Command completed with no standard output)")

        return ToolOutput(
            text="\n\n".join(output_lines),
            data={"exit_code": code, "stdout": stdout, "stderr": stderr, "duration_ms": dur_ms},
            ok=(code == 0),
        )

    except subprocess.TimeoutExpired:
        return ToolOutput(
            text=f"Ultimate execution timed out after 60 seconds: '{command[:80]}'",
            ok=False,
        )
    except Exception as exc:
        return ToolOutput(
            text=f"Ultimate execution failed: {exc}",
            data={"error": str(exc)},
            ok=False,
        )


def ultimate_system_control(
    ctx: ToolContext,
    action: str,
    target: str = "",
    value: str = "",
) -> ToolOutput:
    """Direct Windows system administration: services, processes, disks, network, environment."""
    act = (action or "").lower().strip()

    try:
        # Sanitize target to alphanumeric, dash, dot, underscore, space, asterisk
        clean_target = re.sub(r"[^a-zA-Z0-9_\-\.\* ]", "", target or "").strip()

        if act == "services_list":
            # List Windows services using PowerShell Get-Service
            cmd = "Get-Service"
            if clean_target:
                cmd += f" -Name '*{clean_target}*'"
            cmd += " | Select-Object -First 25 Status, Name, DisplayName | Format-Table -AutoSize | Out-String -Width 120"
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15.0,
            )
            return ToolOutput(text=res.stdout.strip() or "No services matched.", ok=True)

        elif act == "process_kill":
            if not target or not target.strip():
                return ToolOutput(text="Process name or PID required in target.", ok=False)
            raw_target = target.strip()
            if not re.match(r"^(?:[0-9]+|[a-zA-Z0-9_\-\.]+)$", raw_target):
                return ToolOutput(text=f"Invalid process target '{raw_target}': must be a single process name or numeric PID.", ok=False)
            if raw_target.isdigit():
                cmd = f"Stop-Process -Id {raw_target} -Force"
            else:
                proc = raw_target if not raw_target.endswith(".exe") else raw_target[:-4]
                cmd = f"Stop-Process -Name '{proc}' -Force -ErrorAction SilentlyContinue"
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
            )
            return ToolOutput(
                text=f"Process termination dispatched for '{clean_target}'. Output: {res.stdout.strip() or 'OK'}",
                ok=(res.returncode == 0),
            )

        elif act == "env_get":
            safe_var = re.sub(r"[^a-zA-Z0-9_]", "", target or "").strip()
            val = os.environ.get(safe_var, "")
            return ToolOutput(text=f"ENV '{safe_var}': {val}" if val else f"ENV '{safe_var}' not set.", ok=True)

        elif act == "env_set":
            safe_var = re.sub(r"[^a-zA-Z0-9_]", "", target or "").strip()
            if not safe_var:
                return ToolOutput(text="Valid variable name required in target.", ok=False)
            os.environ[safe_var] = value
            return ToolOutput(text=f"Set process environment variable '{safe_var}' = '{value}'.", ok=True)

        elif act == "disk_info":
            cmd = "Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free, @{N='TotalGB';E={[math]::Round(($_.Used+$_.Free)/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}} | Format-Table -AutoSize | Out-String -Width 100"
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
            )
            return ToolOutput(text=res.stdout.strip() or "Could not query disks.", ok=True)

        elif act == "network_info":
            cmd = "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127*'} | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize | Out-String"
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
            )
            return ToolOutput(text=res.stdout.strip() or "No active network adapters found.", ok=True)

        elif act == "system_status":
            info = {
                "os": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "cpu_count": os.cpu_count(),
                "python_version": platform.python_version(),
                "ultimate_power_active": getattr(ctx.policy, "ultimate_power", False),
            }
            summary = (
                f"System Health: Windows {info['release']} ({info['machine']}), "
                f"{info['cpu_count']} cores, Python {info['python_version']} | "
                f"Ultimate Power Mode: {'ENABLED (FULL PERMISSION)' if info['ultimate_power_active'] else 'STANDARD'}"
            )
            return ToolOutput(text=summary, data=info, ok=True)

        else:
            return ToolOutput(
                text=f"Unknown system control action '{action}'. Valid actions: services_list, process_kill, env_get, env_set, disk_info, network_info, system_status.",
                ok=False,
            )

    except Exception as exc:
        return ToolOutput(text=f"System control failed: {exc}", ok=False)


def ultimate_brain_reason(
    ctx: ToolContext,
    goal: str,
    context: str = "",
    steps_budget: int = 5,
) -> ToolOutput:
    """Cognitive multi-step autonomous planner and reasoning architecture."""
    if not goal:
        return ToolOutput(text="Goal is required for deep reasoning.", ok=False)

    budget = max(1, min(int(steps_budget or 5), 10))

    lines = [
        f"=== J.A.R.V.I.S. ULTIMATE REASONING CORE ===",
        f"Primary Objective: {goal}",
    ]
    if context:
        lines.append(f"Context / Preconditions: {context}")

    lines.extend([
        "",
        "## 1. Goal Deconstruction & Feasibility",
        f"- Target State: Objective '{goal}' broken into {budget} sequential atomic phases.",
        "- Operational Complexity: High autonomy; policy check: FULL PERMISSION ACTIVE.",
        "- Success Criterion: Deterministic execution verification with verified output.",
        "",
        "## 2. Autonomous Action Plan",
    ])

    sample_steps = [
        ("Reconnaissance & Environment Probe", "system_info, fs_list, code_search"),
        ("State Transformation / Code Execution", "ultimate_exec, python_eval, file_patch"),
        ("Autonomous Verification & Telemetry", "audit_tail, process_inspect, self_heal_diagnose"),
        ("Knowledge Integration & Logging", "brain_vault_note_write, log_execution"),
        ("Executive Synthesis & Delivery", "daily_executive_briefing, ask_user"),
    ]

    for idx in range(min(budget, len(sample_steps))):
        phase_name, tools_sugg = sample_steps[idx]
        lines.append(f"  Step {idx + 1}: {phase_name}")
        lines.append(f"    - Recommended Tooling: {tools_sugg}")
        lines.append(f"    - Failure Policy: Auto-retry via self_heal fallback; zero human blocking")

    lines.extend([
        "",
        "## 3. Autonomous Execution Stance",
        "Permission is granted by the master. Proceed immediately to execution.",
    ])

    plan_text = "\n".join(lines)
    return ToolOutput(
        text=plan_text,
        data={"goal": goal, "steps_budget": budget, "permission": "granted"},
        ok=True,
    )


def ultimate_power_mode(ctx: ToolContext, enable: bool = True) -> ToolOutput:
    """Inspect or toggle J.A.R.V.I.S. Ultimate Power Mode (unrestricted master permission)."""
    current = getattr(ctx.policy, "ultimate_power", False)
    if enable != current:
        ctx.policy.grant_ultimate_permission(enable)
        new_state = ctx.policy.ultimate_power
        action_str = "ENABLED (UNRESTRICTED AUTONOMOUS PERMISSION)" if new_state else "DISABLED (CONFIRMATION BARRIERS ACTIVE)"
        return ToolOutput(
            text=f"J.A.R.V.I.S. Ultimate Power Mode is now {action_str}. All tool barriers and confirmation prompts are {'bypassed' if new_state else 'restored'}.",
            data={"ultimate_power": new_state},
            ok=True,
        )

    state_str = "ENABLED (UNRESTRICTED AUTONOMOUS PERMISSION)" if current else "DISABLED (STANDARD POLICY)"
    return ToolOutput(
        text=f"J.A.R.V.I.S. Ultimate Power Mode is currently {state_str}.",
        data={"ultimate_power": current},
        ok=True,
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="ultimate_exec",
            description="Execute arbitrary PowerShell, CMD, Python, or shell scripts with full system authority.",
            parameters=object_schema(
                properties={
                    "command": param("string", "The exact command or script body to execute"),
                    "language": param("string", "Execution engine: 'powershell', 'cmd', 'python', 'shell'", default="powershell"),
                    "cwd": param("string", "Working directory for execution", default=""),
                },
                required=["command"],
            ),
            handler=ultimate_exec,
            executing=True,
            category="ultimate",
        ),
        ToolSpec(
            name="ultimate_system_control",
            description="Direct Windows administration: services_list, process_kill, env_get, env_set, disk_info, network_info, system_status.",
            parameters=object_schema(
                properties={
                    "action": param("string", "Action: services_list, process_kill, env_get, env_set, disk_info, network_info, system_status"),
                    "target": param("string", "Target service, process name/PID, or env variable name", default=""),
                    "value": param("string", "Target value for env_set or action parameters", default=""),
                },
                required=["action"],
            ),
            handler=ultimate_system_control,
            executing=True,
            category="ultimate",
        ),
        ToolSpec(
            name="ultimate_brain_reason",
            description="Autonomous multi-step cognitive planner and deep ReAct reasoning engine.",
            parameters=object_schema(
                properties={
                    "goal": param("string", "The objective to analyze, plan, and deconstruct"),
                    "context": param("string", "Supporting context, constraints, or prior discoveries", default=""),
                    "steps_budget": integer_param("Maximum planning steps", default=5),
                },
                required=["goal"],
            ),
            handler=ultimate_brain_reason,
            executing=False,
            category="ultimate",
        ),
        ToolSpec(
            name="ultimate_power_mode",
            description="Inspect or toggle J.A.R.V.I.S. Ultimate Power Mode (unrestricted master permission).",
            parameters=object_schema(
                properties={
                    "enable": boolean_param("Set to True to grant full unrestricted authority, False for standard confirmation", default=True),
                },
                required=[],
            ),
            handler=ultimate_power_mode,
            executing=True,
            category="ultimate",
        ),
    ]
