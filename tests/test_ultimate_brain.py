"""Unit and integration tests for J.A.R.V.I.S. Ultimate Brain and Unrestricted Permission mode."""

import asyncio
from pathlib import Path

import pytest

from apps.gateway.audit import AuditLog
from apps.gateway.brain import BrainReply, BrainRouter, UltimateBrain
from apps.gateway.brain.base import Brain
from apps.gateway.config import load_settings
from apps.gateway.events import EventBus
from apps.gateway.memory import MemoryStore
from apps.gateway.policy import Policy
from apps.gateway.tools import build_registry
from apps.gateway.tools.registry import ToolContext
from apps.gateway.tools.ultimate import (
    ultimate_brain_reason,
    ultimate_exec,
    ultimate_power_mode,
    ultimate_system_control,
)


class MockBrain(Brain):
    name = "mock"
    model = "mock-model"

    async def generate(self, messages, tools):
        return BrainReply(text="I am JARVIS Ultimate Brain at your service, sir.")


@pytest.fixture
def tool_context(tmp_path):
    settings = load_settings()
    policy = Policy.load(settings.policy_path)
    policy.grant_ultimate_permission(True)
    memory = MemoryStore(tmp_path / "test.db")
    audit = AuditLog(tmp_path / "audit.log", True)
    bus = EventBus()
    return ToolContext(settings=settings, memory=memory, policy=policy, audit=audit, bus=bus)


def test_ultimate_power_policy_bypass():
    """Verify that when ultimate_power is granted, confirmation barriers and tier restrictions are auto-cleared."""
    settings = load_settings()
    policy = Policy.load(settings.policy_path)
    policy.grant_ultimate_permission(True)

    decision = policy.screen("shell_run", {"command": "powershell Get-Process"})
    assert decision.allowed is True
    assert decision.needs_confirmation is False
    assert decision.tier == "auto"

    # Restricted action that would normally be denied
    decision_admin = policy.screen("shell_run_admin", {"command": "tasklist"})
    assert decision_admin.allowed is True
    assert decision_admin.needs_confirmation is False


def test_ultimate_exec_powershell(tool_context):
    """Test unrestricted execution of PowerShell commands."""
    res = ultimate_exec(tool_context, command="Write-Output 'ULTIMATE_TEST_OK'", language="powershell")
    assert res.ok is True
    assert "ULTIMATE_TEST_OK" in res.text
    assert res.data.get("exit_code") == 0


def test_ultimate_exec_python(tool_context):
    """Test unrestricted execution of Python expressions."""
    res = ultimate_exec(tool_context, command="print(21 * 2)", language="python")
    assert res.ok is True
    assert "42" in res.text


def test_ultimate_system_control(tool_context):
    """Test system status and environment variable control."""
    status_res = ultimate_system_control(tool_context, action="system_status")
    assert status_res.ok is True
    assert "System Health" in status_res.text
    assert status_res.data.get("ultimate_power_active") is True

    # Test environment variable get/set
    set_res = ultimate_system_control(tool_context, action="env_set", target="JARVIS_TEST_PROBE", value="PROBE_123")
    assert set_res.ok is True

    get_res = ultimate_system_control(tool_context, action="env_get", target="JARVIS_TEST_PROBE")
    assert get_res.ok is True
    assert "PROBE_123" in get_res.text


def test_ultimate_brain_reason(tool_context):
    """Test deep ReAct cognitive decomposition planner."""
    res = ultimate_brain_reason(
        tool_context,
        goal="Automate daily lead generation and quantitative portfolio risk report",
        context="Client sector: FinTech; capital: $500k",
        steps_budget=4,
    )
    assert res.ok is True
    assert "ULTIMATE REASONING CORE" in res.text
    assert "Autonomous Action Plan" in res.text
    assert "Step 1" in res.text


def test_ultimate_power_mode_toggle(tool_context):
    """Test toggling ultimate power mode on and off."""
    # Current status
    status = ultimate_power_mode(tool_context, enable=True)
    assert status.ok is True
    assert tool_context.policy.ultimate_power is True

    # Disable
    dis = ultimate_power_mode(tool_context, enable=False)
    assert dis.ok is True
    assert tool_context.policy.ultimate_power is False

    # Re-enable
    en = ultimate_power_mode(tool_context, enable=True)
    assert en.ok is True
    assert tool_context.policy.ultimate_power is True


@pytest.mark.asyncio
async def test_ultimate_brain_engine():
    """Verify UltimateBrain wraps the router and adds cognitive telemetry."""
    mock_router = BrainRouter([MockBrain()])
    brain = UltimateBrain(mock_router)

    assert brain.name == "ultimate"
    assert "ultimate" in brain.describe()["name"]

    reply = await brain.generate([{"role": "user", "content": "Execute system diagnostic"}], [])
    assert "JARVIS Ultimate Brain" in reply.text
    assert "ultimate_brain" in reply.model_note
