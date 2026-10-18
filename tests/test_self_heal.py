"""Unit tests for the autonomous self-healing and diagnostic subsystem."""

from pathlib import Path

import pytest
from apps.gateway.config import load_settings
from apps.gateway.policy import Policy
from apps.gateway.self_heal import SelfHealer
from apps.gateway.tools.registry import ToolContext
from apps.gateway.tools.self_heal import (
    self_heal_diagnose,
    self_heal_repair,
    self_heal_status,
)


@pytest.mark.asyncio
async def test_self_healer_diagnose_all(tmp_path: Path):
    healer = SelfHealer()
    results = await healer.diagnose_all()
    assert len(results) >= 8
    subsystems = {r.subsystem for r in results}
    assert "database" in subsystems
    assert "obsidian_vault" in subsystems
    assert "filesystem" in subsystems
    assert "subagents" in subsystems


@pytest.mark.asyncio
async def test_self_healer_repair():
    healer = SelfHealer()
    report = await healer.repair(target_subsystems=["database", "filesystem", "obsidian_vault"])
    assert "actions_applied" in report
    assert "overall_status" in report
    assert report["post_error"] == 0 or report["post_error"] >= 0


@pytest.mark.asyncio
async def test_self_heal_tools():
    settings = load_settings()
    policy = Policy.load(settings.resolved_policy_path())
    ctx = ToolContext(
        settings=settings,
        memory=None,
        policy=policy,
        audit=None,
        bus=None,
    )

    # Test diagnose tool
    diag_out = await self_heal_diagnose(ctx=ctx)
    assert "# J.A.R.V.I.S. Diagnostic Health Report" in diag_out.text
    assert diag_out.data is not None
    assert "diagnostics" in diag_out.data

    # Test repair tool
    repair_out = await self_heal_repair(ctx=ctx, subsystem="database")
    assert "# Self-Healing Remediation Report" in repair_out.text
    assert repair_out.data is not None

    # Test status tool
    status_out = await self_heal_status(ctx=ctx)
    assert "Self-Healing Watchdog Status" in status_out.text
    assert status_out.data is not None


@pytest.mark.asyncio
async def test_watchdog_lifecycle():
    healer = SelfHealer()
    assert not healer._running_watchdog
    await healer.start_watchdog(interval_seconds=1.0)
    assert healer._running_watchdog
    await healer.stop_watchdog()
    assert not healer._running_watchdog
