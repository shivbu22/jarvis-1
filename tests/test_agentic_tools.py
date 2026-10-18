"""Tests for agentic and autonomous tool suite."""

from __future__ import annotations

from apps.gateway.tools.agentic import (
    code_search,
    file_patch,
    process_inspect,
    python_eval,
    task_plan_manage,
)


def test_python_eval(harness) -> None:
    # Math expression
    res = python_eval(harness.ctx, "2 ** 10 + 24")
    assert res.ok is True
    assert "1048" in res.text

    # Code with print
    res2 = python_eval(harness.ctx, "print('Hello Autonomous Jarvis')\nresult = 42")
    assert res2.ok is True
    assert "Hello Autonomous Jarvis" in res2.text
    assert "42" in res2.text


def test_code_search(harness, tmp_path) -> None:
    test_file = tmp_path / "sample.py"
    test_file.write_text("def jarvis_autonomous_logic():\n    return 'success'\n", encoding="utf-8")

    res = code_search(harness.ctx, query="jarvis_autonomous", path=str(tmp_path))
    assert res.ok is True
    assert "sample.py:1" in res.text
    assert len(res.data["matches"]) == 1


def test_task_plan_manage(harness) -> None:
    # Create task
    res_create = task_plan_manage(
        harness.ctx,
        action="create",
        title="Deploy Server",
        steps="Check ports;Build container;Verify health",
    )
    assert res_create.ok is True
    task_id = res_create.data["id"]
    assert len(res_create.data["steps"]) == 3

    # List tasks
    res_list = task_plan_manage(harness.ctx, action="list")
    assert res_list.ok is True
    assert "Deploy Server" in res_list.text

    # Complete task
    res_complete = task_plan_manage(harness.ctx, action="complete", task_id=task_id)
    assert res_complete.ok is True

    # Delete task
    res_del = task_plan_manage(harness.ctx, action="delete", task_id=task_id)
    assert res_del.ok is True


def test_process_inspect(harness) -> None:
    res = process_inspect(harness.ctx, limit=10)
    assert res.ok is True
    assert "Active Processes" in res.text
    assert len(res.data["processes"]) > 0


def test_file_patch(harness, tmp_path) -> None:
    test_file = tmp_path / "config.txt"
    test_file.write_text("server_port = 8000\nmode = dev\n", encoding="utf-8")

    res = file_patch(
        harness.ctx,
        path=str(test_file),
        search="mode = dev",
        replace="mode = production",
    )
    assert res.ok is True
    assert test_file.read_text(encoding="utf-8") == "server_port = 8000\nmode = production\n"
