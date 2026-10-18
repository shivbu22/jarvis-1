"""Unit tests for J.A.R.V.I.S. Subagent Swarm multi-agent tools."""

from unittest.mock import MagicMock

import pytest
from apps.gateway.tools.registry import ToolContext
from apps.gateway.tools.subagents import (
    build_specs,
    subagent_batch_run,
    subagent_list,
    subagent_spawn,
    subagent_status,
)


def test_subagents_specs() -> None:
    specs = build_specs()
    names = {s.name for s in specs}
    assert "subagent_spawn" in names
    assert "subagent_batch_run" in names
    assert "subagent_list" in names
    assert "subagent_status" in names
    assert "subagent_abort" in names


@pytest.mark.asyncio
async def test_subagent_spawn_and_status() -> None:
    ctx = MagicMock(spec=ToolContext)
    res_spawn = await subagent_spawn(
        ctx,
        goal="Audit trading profile entities in brain vault",
        context="Check user preferences for Indian stock markets",
        timeout_s=30,
        background=False,
    )
    assert res_spawn.ok
    assert "completed task" in res_spawn.text
    task_id = res_spawn.data["task_id"]

    res_status = subagent_status(ctx, task_id)
    assert res_status.ok
    assert task_id in res_status.text
    assert res_status.data["status"] == "completed"


@pytest.mark.asyncio
async def test_subagent_batch_run_parallel() -> None:
    ctx = MagicMock(spec=ToolContext)
    tasks = [
        {"goal": "Scan codebase for gateway config", "context": "Find settings"},
        {"goal": "Check Obsidian brain knowledge vault", "context": "Find entities"},
    ]
    res_batch = await subagent_batch_run(ctx, tasks, timeout_s=30)
    assert res_batch.ok
    assert "Parallel Sub-Agent Swarm completed (2/2 tasks succeeded)" in res_batch.text
    assert res_batch.data["completed"] == 2
    assert len(res_batch.data["results"]) == 2


def test_subagent_list() -> None:
    ctx = MagicMock(spec=ToolContext)
    res_list = subagent_list(ctx)
    assert res_list.ok
    assert "Recorded Sub-Agents" in res_list.text
