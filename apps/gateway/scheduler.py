"""Persistent task scheduler for J.A.R.V.I.S.

Supports three kinds of scheduled work:

* **Reminders** — fire once at a specific time and deliver a message.
* **Delayed tasks** — fire once after a duration and execute a tool.
* **Recurring cron** — fire on a cron schedule and execute a tool or deliver a message.

All tasks are persisted to SQLite so they survive gateway restarts.  The
scheduler runs as an asyncio background task, waking every 15 seconds to
check for due items.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger("jarvis.scheduler")

# ---------------------------------------------------------------------------
# Lightweight cron parser (minute hour day-of-month month day-of-week)
# ---------------------------------------------------------------------------

def _match_cron_field(field_str: str, value: int, min_val: int, max_val: int) -> bool:
    """Check if ``value`` matches a single cron field expression."""
    if field_str == "*":
        return True
    for part in field_str.split(","):
        part = part.strip()
        # Step: */N
        if part.startswith("*/"):
            step = int(part[2:])
            if step > 0 and (value - min_val) % step == 0:
                return True
            continue
        # Range: A-B
        if "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
            continue
        # Exact
        if value == int(part):
            return True
    return False


def cron_matches(expr: str, dt: datetime) -> bool:
    """Return True if *dt* falls within the cron expression (5-field)."""
    parts = expr.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    return (
        _match_cron_field(minute, dt.minute, 0, 59)
        and _match_cron_field(hour, dt.hour, 0, 23)
        and _match_cron_field(dom, dt.day, 1, 31)
        and _match_cron_field(month, dt.month, 1, 12)
        and _match_cron_field(dow, dt.isoweekday() % 7, 0, 6)  # 0=Sun
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ScheduledTask:
    task_id: str
    kind: str  # "reminder" | "delayed" | "cron"
    created_at: float
    fire_at: float | None = None  # epoch for one-shot
    cron_expr: str | None = None  # for recurring
    message: str = ""  # what to say / notify
    tool_name: str | None = None  # optional tool to execute
    tool_args: dict[str, Any] = field(default_factory=dict)
    repeat_count: int = 0  # how many times a cron has fired
    max_repeats: int = 0  # 0 = unlimited
    enabled: bool = True
    last_fired: float | None = None
    label: str = ""  # human-friendly name

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.fire_at:
            d["fire_at_iso"] = datetime.fromtimestamp(self.fire_at, tz=timezone.utc).isoformat()
        return d

    def as_dict(self) -> dict[str, Any]:
        """Alias for to_dict()."""
        return self.to_dict()


# ---------------------------------------------------------------------------
# Scheduler engine
# ---------------------------------------------------------------------------

class TaskScheduler:
    """SQLite-backed scheduler daemon."""

    def __init__(
        self,
        db_path: Path | str,
        on_fire: Callable[[ScheduledTask], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._on_fire = on_fire  # callback when a task fires
        self._lock = threading.Lock()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._init_db()

    # -- persistence -------------------------------------------------------

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id    TEXT PRIMARY KEY,
                    kind       TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    fire_at    REAL,
                    cron_expr  TEXT,
                    message    TEXT DEFAULT '',
                    tool_name  TEXT,
                    tool_args  TEXT DEFAULT '{}',
                    repeat_count INTEGER DEFAULT 0,
                    max_repeats  INTEGER DEFAULT 0,
                    enabled    INTEGER DEFAULT 1,
                    last_fired REAL,
                    label      TEXT DEFAULT ''
                )
            """)
            conn.commit()

    def _save(self, task: ScheduledTask) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO scheduled_tasks
                   (task_id, kind, created_at, fire_at, cron_expr, message,
                    tool_name, tool_args, repeat_count, max_repeats, enabled, last_fired, label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.task_id, task.kind, task.created_at, task.fire_at,
                    task.cron_expr, task.message, task.tool_name,
                    json.dumps(task.tool_args), task.repeat_count,
                    task.max_repeats, int(task.enabled), task.last_fired,
                    task.label,
                ),
            )
            conn.commit()

    def _load_all(self) -> list[ScheduledTask]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM scheduled_tasks WHERE enabled = 1").fetchall()
        tasks = []
        for row in rows:
            tasks.append(ScheduledTask(
                task_id=row["task_id"],
                kind=row["kind"],
                created_at=row["created_at"],
                fire_at=row["fire_at"],
                cron_expr=row["cron_expr"],
                message=row["message"] or "",
                tool_name=row["tool_name"],
                tool_args=json.loads(row["tool_args"] or "{}"),
                repeat_count=row["repeat_count"],
                max_repeats=row["max_repeats"],
                enabled=bool(row["enabled"]),
                last_fired=row["last_fired"],
                label=row["label"] or "",
            ))
        return tasks

    def _delete(self, task_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM scheduled_tasks WHERE task_id = ?", (task_id,))
            conn.commit()
            return cur.rowcount > 0

    # -- public API --------------------------------------------------------

    def add_reminder(self, message: str, fire_at: float, label: str = "") -> ScheduledTask:
        """Schedule a one-shot reminder at a specific epoch time."""
        task = ScheduledTask(
            task_id=f"rem-{uuid.uuid4().hex[:8]}",
            kind="reminder",
            created_at=time.time(),
            fire_at=fire_at,
            message=message,
            label=label or message[:50],
        )
        self._save(task)
        logger.info("Reminder scheduled: %s at %s", task.task_id, datetime.fromtimestamp(fire_at))
        return task

    def add_delayed(
        self, message: str, delay_seconds: float,
        tool_name: str | None = None, tool_args: dict[str, Any] | None = None,
        label: str = "",
    ) -> ScheduledTask:
        """Schedule a one-shot task after a delay."""
        task = ScheduledTask(
            task_id=f"del-{uuid.uuid4().hex[:8]}",
            kind="delayed",
            created_at=time.time(),
            fire_at=time.time() + delay_seconds,
            message=message,
            tool_name=tool_name,
            tool_args=tool_args or {},
            label=label or message[:50],
        )
        self._save(task)
        logger.info("Delayed task scheduled: %s in %.0fs", task.task_id, delay_seconds)
        return task

    def add_cron(
        self, cron_expr: str, message: str = "",
        tool_name: str | None = None, tool_args: dict[str, Any] | None = None,
        max_repeats: int = 0, label: str = "",
    ) -> ScheduledTask:
        """Schedule a recurring cron task."""
        task = ScheduledTask(
            task_id=f"cron-{uuid.uuid4().hex[:8]}",
            kind="cron",
            created_at=time.time(),
            cron_expr=cron_expr,
            message=message,
            tool_name=tool_name,
            tool_args=tool_args or {},
            max_repeats=max_repeats,
            label=label or f"cron({cron_expr})",
        )
        self._save(task)
        logger.info("Cron task scheduled: %s [%s]", task.task_id, cron_expr)
        return task

    def cancel(self, task_id: str) -> bool:
        """Cancel and remove a scheduled task."""
        deleted = self._delete(task_id)
        if deleted:
            logger.info("Task cancelled: %s", task_id)
        return deleted

    def list_tasks(self) -> list[dict[str, Any]]:
        """Return all active scheduled tasks as dicts."""
        return [t.to_dict() for t in self._load_all()]

    # -- daemon loop -------------------------------------------------------

    async def start(self) -> None:
        """Start the background scheduler loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Task scheduler daemon started")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Task scheduler daemon stopped")

    async def _loop(self) -> None:
        """Check for due tasks every 15 seconds."""
        while self._running:
            try:
                await asyncio.sleep(15)
                now = time.time()
                now_dt = datetime.now(tz=timezone.utc)

                for task in self._load_all():
                    fired = False

                    if task.kind in ("reminder", "delayed"):
                        if task.fire_at and now >= task.fire_at:
                            fired = True

                    elif task.kind == "cron" and task.cron_expr:
                        if cron_matches(task.cron_expr, now_dt):
                            # Don't fire more than once per minute
                            if task.last_fired is None or (now - task.last_fired) > 55:
                                fired = True

                    if fired:
                        await self._fire(task)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Scheduler loop error: %s", exc)

    async def _fire(self, task: ScheduledTask) -> None:
        """Execute a due task."""
        logger.info("Firing task: %s (%s)", task.task_id, task.label)

        if self._on_fire:
            try:
                await self._on_fire(task)
            except Exception as exc:
                logger.error("Task fire callback error for %s: %s", task.task_id, exc)

        # Update state
        task.last_fired = time.time()
        task.repeat_count += 1

        if task.kind in ("reminder", "delayed"):
            # One-shot: remove after firing
            self._delete(task.task_id)
        elif task.kind == "cron":
            if task.max_repeats > 0 and task.repeat_count >= task.max_repeats:
                # Hit repeat limit: disable
                task.enabled = False
                logger.info("Cron task %s reached max repeats (%d), disabling", task.task_id, task.max_repeats)
            self._save(task)

    # -- telemetry ---------------------------------------------------------

    def get_telemetry(self) -> dict[str, Any]:
        tasks = self._load_all()
        return {
            "running": self._running,
            "total_tasks": len(tasks),
            "reminders": sum(1 for t in tasks if t.kind == "reminder"),
            "delayed": sum(1 for t in tasks if t.kind == "delayed"),
            "cron": sum(1 for t in tasks if t.kind == "cron"),
        }
