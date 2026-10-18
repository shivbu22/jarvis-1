"""Tests for the four new subsystems: scheduler, notifications, context, and profile."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. Scheduler Tests
# ---------------------------------------------------------------------------

class TestTaskScheduler:
    def test_add_reminder(self):
        from apps.gateway.scheduler import TaskScheduler

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test_sched.db"
            sched = TaskScheduler(db_path=db_path)

            task = sched.add_reminder("Call mom", fire_at=time.time() + 300)
            assert task.task_id.startswith("rem-")
            assert task.kind == "reminder"
            assert task.message == "Call mom"

            tasks = sched.list_tasks()
            assert len(tasks) == 1
            assert tasks[0]["task_id"] == task.task_id

    def test_add_cron(self):
        from apps.gateway.scheduler import TaskScheduler

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test_sched.db"
            sched = TaskScheduler(db_path=db_path)

            task = sched.add_cron("*/5 * * * *", message="Check stocks", max_repeats=10)
            assert task.task_id.startswith("cron-")
            assert task.kind == "cron"
            assert task.cron_expr == "*/5 * * * *"
            assert task.max_repeats == 10

    def test_cancel_task(self):
        from apps.gateway.scheduler import TaskScheduler

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test_sched.db"
            sched = TaskScheduler(db_path=db_path)

            task = sched.add_reminder("Test", fire_at=time.time() + 600)
            assert len(sched.list_tasks()) == 1
            assert sched.cancel(task.task_id)
            assert len(sched.list_tasks()) == 0
            assert not sched.cancel("nonexistent-id")

    def test_persistence(self):
        from apps.gateway.scheduler import TaskScheduler

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test_sched.db"

            # Create and save
            sched1 = TaskScheduler(db_path=db_path)
            sched1.add_reminder("Persistent", fire_at=time.time() + 1000)

            # Reload
            sched2 = TaskScheduler(db_path=db_path)
            tasks = sched2.list_tasks()
            assert len(tasks) == 1
            assert tasks[0]["message"] == "Persistent"


class TestCronMatching:
    def test_wildcard(self):
        from apps.gateway.scheduler import cron_matches
        from datetime import datetime, timezone

        dt = datetime(2025, 6, 15, 10, 30, tzinfo=timezone.utc)
        assert cron_matches("* * * * *", dt)

    def test_exact_match(self):
        from apps.gateway.scheduler import cron_matches
        from datetime import datetime, timezone

        dt = datetime(2025, 6, 15, 9, 0, tzinfo=timezone.utc)
        assert cron_matches("0 9 * * *", dt)
        assert not cron_matches("0 10 * * *", dt)

    def test_step_match(self):
        from apps.gateway.scheduler import cron_matches
        from datetime import datetime, timezone

        dt = datetime(2025, 6, 15, 10, 15, tzinfo=timezone.utc)
        assert cron_matches("*/15 * * * *", dt)
        assert not cron_matches("*/7 * * * *", dt)  # 15 % 7 != 0


# ---------------------------------------------------------------------------
# 2. Context Window Manager Tests
# ---------------------------------------------------------------------------

class TestContextWindowManager:
    def test_estimate_tokens(self):
        from apps.gateway.context import estimate_tokens
        assert estimate_tokens("hello world") > 0
        assert estimate_tokens("a" * 100) == 25

    def test_prepare_messages_fits(self):
        from apps.gateway.context import ContextWindowManager

        mgr = ContextWindowManager(max_context_tokens=5000, keep_recent=4)
        history = [
            {"role": "user", "content": "Hi there"},
            {"role": "assistant", "content": "Hello! How can I help?"},
        ]
        result = mgr.prepare_messages("You are Jarvis.", history)
        assert len(result) == 2
        assert result[0]["role"] == "user"

    def test_prepare_messages_overflow_creates_summary(self):
        from apps.gateway.context import ContextWindowManager

        mgr = ContextWindowManager(max_context_tokens=500, keep_recent=2)
        # Create a long history that exceeds the budget
        history = []
        for i in range(20):
            history.append({"role": "user", "content": f"This is important message number {i} with a lot of detail."})
            history.append({"role": "assistant", "content": f"I remember that message {i} about the details."})

        result = mgr.prepare_messages("You are Jarvis.", history, tool_definitions_tokens=100)
        # Should have a summary + some recent messages, not all 40
        assert len(result) < 40

    def test_telemetry(self):
        from apps.gateway.context import ContextWindowManager

        mgr = ContextWindowManager()
        telem = mgr.get_telemetry()
        assert "max_context_tokens" in telem
        assert telem["has_summary"] is False


# ---------------------------------------------------------------------------
# 3. User Profile Tests
# ---------------------------------------------------------------------------

class TestUserProfile:
    def test_default_profile(self):
        from apps.gateway.profile import UserProfile

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profile.json"
            profile = UserProfile(path)
            assert profile.get("timezone") == "Asia/Kolkata"
            assert profile.get("currency") == "INR"

    def test_set_and_get(self):
        from apps.gateway.profile import UserProfile

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profile.json"
            profile = UserProfile(path)
            profile.set("preferred_name", "Tony")
            assert profile.get("preferred_name") == "Tony"

    def test_persistence(self):
        from apps.gateway.profile import UserProfile

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profile.json"
            p1 = UserProfile(path)
            p1.set("preferred_name", "Stark")

            p2 = UserProfile(path)
            assert p2.get("preferred_name") == "Stark"

    def test_custom_preferences(self):
        from apps.gateway.profile import UserProfile

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profile.json"
            profile = UserProfile(path)
            profile.set_preference("favorite_color", "blue")
            assert profile.get_preference("favorite_color") == "blue"

    def test_context_string(self):
        from apps.gateway.profile import UserProfile

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profile.json"
            profile = UserProfile(path)
            profile.set("preferred_name", "Tony")
            ctx = profile.to_context_string()
            assert "Tony" in ctx
            assert "Asia/Kolkata" in ctx


# ---------------------------------------------------------------------------
# 4. Notification Manager Tests
# ---------------------------------------------------------------------------

class TestNotificationManager:
    def test_send_notification_bus(self):
        import asyncio
        from apps.gateway.notify import NotificationManager

        received = []

        class FakeBus:
            def publish(self, event):
                received.append(event)

        mgr = NotificationManager(bus=FakeBus())
        notif = asyncio.run(mgr.send("Test Title", "Test message", urgency="normal"))
        assert notif.title == "Test Title"
        assert notif.message == "Test message"
        assert len(mgr.get_history()) == 1

    def test_notification_history(self):
        import asyncio
        from apps.gateway.notify import NotificationManager

        mgr = NotificationManager()

        async def _send_two():
            await mgr.send("A", "msg1")
            await mgr.send("B", "msg2")

        asyncio.run(_send_two())
        history = mgr.get_history()
        assert len(history) == 2
        # Most recent first
        assert history[0]["title"] == "B"


# ---------------------------------------------------------------------------
# 5. Scheduler Tool Handlers Tests
# ---------------------------------------------------------------------------

class TestSchedulerTools:
    def test_parse_time_in_minutes(self):
        from apps.gateway.tools.scheduler import _parse_time_expression

        result = _parse_time_expression("in 5 minutes")
        assert result is not None
        assert result > time.time()
        assert result < time.time() + 400  # ~5 min + tolerance

    def test_parse_time_at_hour(self):
        from apps.gateway.tools.scheduler import _parse_time_expression

        result = _parse_time_expression("at 3pm")
        assert result is not None
        assert result > time.time() - 86400  # within a day

    def test_parse_invalid(self):
        from apps.gateway.tools.scheduler import _parse_time_expression

        result = _parse_time_expression("next blue moon")
        assert result is None
