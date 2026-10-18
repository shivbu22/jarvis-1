"""Self-Healing and Autonomous Diagnostic Tools for J.A.R.V.I.S. Gateway."""

from __future__ import annotations

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param


async def self_heal_diagnose(ctx: ToolContext) -> ToolOutput:
    """Run comprehensive system health diagnostics across all tiers."""
    from ..self_heal import SelfHealer

    healer = getattr(getattr(ctx, "service", None), "self_healer", None)
    if healer is None:
        healer = SelfHealer(settings=getattr(ctx, "settings", None))

    results = await healer.diagnose_all()
    matrix_lines = [
        "# J.A.R.V.I.S. Diagnostic Health Report",
        "",
        "| Subsystem | Status | Latency | Details |",
        "|---|---|---|---|",
    ]
    status_emojis = {
        "healthy": "✅ Healthy",
        "degraded": "⚠️ Degraded",
        "error": "❌ Error",
        "unknown": "❓ Unknown",
    }
    for r in results:
        status_label = status_emojis.get(r.status, r.status)
        lat = r.metrics.get("latency_ms", 0.0) if r.metrics else 0.0
        matrix_lines.append(
            f"| **{r.subsystem.upper()}** | {status_label} | {lat:.1f}ms | {r.details} |"
        )

    text_out = "\n".join(matrix_lines)
    data_out = {
        "diagnostics": [
            {
                "subsystem": r.subsystem,
                "status": r.status,
                "details": r.details,
                "metrics": r.metrics,
                "remediation_available": r.remediation_available,
            }
            for r in results
        ]
    }
    return ToolOutput(text=text_out, data=data_out)


async def self_heal_repair(
    ctx: ToolContext,
    subsystem: str = "all",
    dry_run: bool = False,
) -> ToolOutput:
    """Execute autonomous self-repair and remediation routines."""
    from ..self_heal import SelfHealer

    healer = getattr(getattr(ctx, "service", None), "self_healer", None)
    if healer is None:
        healer = SelfHealer(settings=getattr(ctx, "settings", None))

    targets = None if not subsystem or subsystem.lower() == "all" else [subsystem.strip()]
    report = await healer.repair(target_subsystems=targets, dry_run=dry_run)

    lines = [
        "# Self-Healing Remediation Report",
        f"- **Mode**: {'Dry Run (Simulated)' if dry_run else 'Live Repair'}",
        f"- **Overall Status**: {report.get('overall_status', 'unknown').upper()}",
        f"- **Pre-Repair Status**: {report.get('pre_status', 'unknown')}",
        f"- **Actions Applied**: {len(report.get('actions_applied', []))}",
        "",
    ]
    if report.get("actions_applied"):
        lines.append("### Applied Remediation Actions")
        for act in report["actions_applied"]:
            if isinstance(act, dict):
                status_symbol = "✅" if act.get("success", True) else "❌"
                lines.append(f"- {status_symbol} **{act.get('subsystem', 'core')}**: {act.get('action')}")
                if act.get("error"):
                    lines.append(f"  *Error*: `{act.get('error')}`")
            else:
                lines.append(f"- ✅ {act}")
    else:
        lines.append("*No degradation detected. All targeted systems are already in healthy state.*")

    text_out = "\n".join(lines)
    return ToolOutput(text=text_out, data=report)


async def self_heal_status(ctx: ToolContext) -> ToolOutput:
    """Retrieve self-healing telemetry, watchdog status, and recent remediations."""
    from ..self_heal import SelfHealer

    healer = getattr(getattr(ctx, "service", None), "self_healer", None)
    if healer is None:
        healer = SelfHealer(settings=getattr(ctx, "settings", None))

    telemetry = healer.get_telemetry()
    lines = [
        "# Self-Healing Watchdog Status",
        f"- **Watchdog Active**: {'🟢 RUNNING' if telemetry.get('watchdog_running') else '⚪ IDLE'}",
        f"- **Watchdog Interval**: {telemetry.get('watchdog_interval_s')}s",
        f"- **Total Watchdog Cycles**: {telemetry.get('total_watchdog_runs')}",
        f"- **Last Run Status**: {telemetry.get('last_run_status', 'never')}",
        f"- **Last Run Time**: {telemetry.get('last_run_timestamp', 'never')}",
        f"- **Cumulative Repairs Applied**: {telemetry.get('remediations_count')}",
    ]

    history = telemetry.get("recent_remediations", [])
    if history:
        lines.extend(["", "### Recent Remediations"])
        for h in history[-5:]:
            lines.append(f"- [{h.get('timestamp')}] {h.get('subsystem')}: {h.get('action')}")

    text_out = "\n".join(lines)
    return ToolOutput(text=text_out, data=telemetry)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="self_heal_diagnose",
            description=(
                "Run a comprehensive deep-diagnostic check across all subsystems: "
                "database integrity, brain models, STT whisper engine, TTS neural synthesis, "
                "wake detector, Obsidian Brain knowledge graph, subagents, and filesystem."
            ),
            parameters=object_schema({}, []),
            handler=self_heal_diagnose,
            executing=True,
            category="system",
        ),
        ToolSpec(
            name="self_heal_repair",
            description=(
                "Autonomously repair and remediate degraded subsystems. Fixes database corruption, "
                "rebuilds broken Obsidian wikilinks, clears orphaned subagent tasks, resets failed "
                "brain connections, and cleans stale runtime artifacts."
            ),
            parameters=object_schema(
                {
                    "subsystem": param(
                        "subsystem",
                        "string",
                        "Subsystem to repair ('all', 'database', 'obsidian_vault', 'subagents', 'brain_router', 'filesystem'). Defaults to 'all'.",
                        default="all",
                    ),
                    "dry_run": param(
                        "dry_run",
                        "boolean",
                        "If true, simulates repairs without making changes.",
                        default=False,
                    ),
                },
                [],
            ),
            handler=self_heal_repair,
            executing=True,
            category="system",
        ),
        ToolSpec(
            name="self_heal_status",
            description="Inspect real-time health telemetry, autonomous watchdog cycle state, and recent remediations.",
            parameters=object_schema({}, []),
            handler=self_heal_status,
            executing=False,
            category="system",
        ),
    ]
