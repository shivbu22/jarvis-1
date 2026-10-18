"""Scheduler tool handlers for J.A.R.V.I.S.

Exposes the TaskScheduler engine as callable tools:

* ``cron_task_schedule`` — schedule recurring or one-shot tasks.
* ``reminder_set`` — set a timed reminder with a message.
* ``scheduled_tasks_list`` — list all active scheduled tasks.
* ``scheduled_task_cancel`` — cancel a scheduled task by ID.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

logger = logging.getLogger("jarvis.tools.scheduler")


def parse_time_expression(expr: str) -> float | None:
    """Parse natural language time expressions into epoch timestamps.

    Supports:
      - "in 5 minutes", "in 2 hours", "in 30 seconds"
      - "at 3pm", "at 15:00", "at 3:30 PM"
      - "tomorrow at 9am"
    """
    return _parse_time_expression(expr)


def _parse_time_expression(expr: str) -> float | None:
    """Parse natural language time expressions into epoch timestamps.

    Supports:
      - "in 5 minutes", "in 2 hours", "in 30 seconds"
      - "at 3pm", "at 15:00", "at 3:30 PM"
      - "tomorrow at 9am"
    """
    expr = expr.strip().lower()
    now = time.time()

    # "in X minutes/hours/seconds"
    m = re.match(r'in\s+(\d+(?:\.\d+)?)\s*(second|minute|hour|day|week)s?', expr)
    if m:
        amount = float(m.group(1))
        unit = m.group(2)
        multipliers = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
        return now + amount * multipliers.get(unit, 60)

    # "at HH:MM" or "at H PM/AM"
    m = re.match(r'(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', expr)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target.timestamp() <= now:
            target += timedelta(days=1)  # if time already passed today, schedule for tomorrow
        return target.timestamp()

    # "tomorrow at ..."
    m = re.match(r'tomorrow\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', expr)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        target = (datetime.now() + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return target.timestamp()

    # Try as raw number (seconds from now)
    try:
        seconds = float(expr)
        if seconds > 0:
            return now + seconds
    except ValueError:
        pass

    return None


def _get_scheduler(ctx: ToolContext) -> Any:
    """Retrieve the scheduler from the service context."""
    service = getattr(ctx, "service", None) if ctx else None
    if service and hasattr(service, "scheduler"):
        return service.scheduler
    return None


def reminder_set(ctx: ToolContext, message: str, when: str, label: str = "") -> ToolOutput:
    """Set a reminder that fires at a specific time or after a delay.

    Args:
        message: The reminder text to deliver when it fires.
        when: Time expression like "in 5 minutes", "at 3pm", "tomorrow at 9am".
        label: Optional human-friendly label for the reminder.
    """
    scheduler = _get_scheduler(ctx)
    if not scheduler:
        return ToolOutput(text="Scheduler not available. Gateway may need restart.", ok=False)

    fire_at = _parse_time_expression(when)
    if fire_at is None:
        return ToolOutput(
            text=f"Could not parse time expression: '{when}'. "
                 "Try formats like 'in 5 minutes', 'at 3pm', 'tomorrow at 9am'.",
            ok=False,
        )

    task = scheduler.add_reminder(message=message, fire_at=fire_at, label=label)
    fire_dt = datetime.fromtimestamp(fire_at)
    delay = fire_at - time.time()

    return ToolOutput.from_ok({
        "task_id": task.task_id,
        "message": message,
        "fires_at": fire_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "fires_in": f"{delay / 60:.1f} minutes" if delay < 3600 else f"{delay / 3600:.1f} hours",
        "status": "scheduled",
    })


def cron_task_schedule(
    ctx: ToolContext, cron_expression: str, message: str = "",
    tool_name: str = "", tool_args: str = "",
    max_repeats: int = 0, label: str = "",
) -> ToolOutput:
    """Schedule a recurring task using a cron expression (minute hour dom month dow).

    Args:
        cron_expression: Standard 5-field cron expression, e.g. "0 9 * * 1-5" for weekdays at 9am.
        message: Message to deliver each time it fires.
        tool_name: Optional tool to execute each time it fires.
        tool_args: Optional JSON string of tool arguments.
        max_repeats: Maximum number of times to fire (0 = unlimited).
        label: Human-friendly label.
    """
    scheduler = _get_scheduler(ctx)
    if not scheduler:
        return ToolOutput(text="Scheduler not available.", ok=False)

    parts = cron_expression.strip().split()
    if len(parts) != 5:
        return ToolOutput(
            text=f"Invalid cron expression: '{cron_expression}'. Must be 5 fields: minute hour day-of-month month day-of-week.",
            ok=False,
        )

    parsed_args = {}
    if tool_args:
        try:
            import json
            parsed_args = json.loads(tool_args)
        except Exception:
            return ToolOutput(text=f"Invalid tool_args JSON: '{tool_args}'", ok=False)

    task = scheduler.add_cron(
        cron_expr=cron_expression,
        message=message,
        tool_name=tool_name or None,
        tool_args=parsed_args,
        max_repeats=max_repeats,
        label=label,
    )

    return ToolOutput.from_ok({
        "task_id": task.task_id,
        "cron_expression": cron_expression,
        "message": message,
        "tool": tool_name or "none",
        "max_repeats": max_repeats if max_repeats > 0 else "unlimited",
        "status": "active",
    })


def scheduled_tasks_list(ctx: ToolContext) -> ToolOutput:
    """List all active scheduled tasks (reminders, delayed tasks, cron jobs)."""
    scheduler = _get_scheduler(ctx)
    if not scheduler:
        return ToolOutput(text="Scheduler not available.", ok=False)

    tasks = scheduler.list_tasks()
    return ToolOutput.from_ok({
        "total": len(tasks),
        "tasks": tasks,
    })


def scheduled_task_cancel(ctx: ToolContext, task_id: str) -> ToolOutput:
    """Cancel a scheduled task by its ID."""
    scheduler = _get_scheduler(ctx)
    if not scheduler:
        return ToolOutput(text="Scheduler not available.", ok=False)

    if scheduler.cancel(task_id):
        return ToolOutput.from_ok({"cancelled": task_id, "status": "removed"})
    return ToolOutput(text=f"Task '{task_id}' not found or already completed.", ok=False)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="reminder_set",
            description="Set a timed reminder. Fires at a specific time or after a delay and delivers a message.",
            parameters=object_schema(
                properties={
                    "message": param("string", "The reminder message to deliver when it fires."),
                    "when": param("string", "Time expression: 'in 5 minutes', 'at 3pm', 'tomorrow at 9am', etc."),
                    "label": param("string", "Optional human-friendly label.", default=""),
                },
                required=["message", "when"],
            ),
            handler=reminder_set,
            category="scheduler",
        ),
        ToolSpec(
            name="cron_task_schedule",
            description="Schedule a recurring task using a cron expression. Can deliver messages or execute tools on schedule.",
            parameters=object_schema(
                properties={
                    "cron_expression": param("string", "5-field cron: minute hour day-of-month month day-of-week. E.g. '0 9 * * 1-5' for weekdays at 9am."),
                    "message": param("string", "Message to deliver each time.", default=""),
                    "tool_name": param("string", "Optional tool to execute on each trigger.", default=""),
                    "tool_args": param("string", "JSON string of tool arguments.", default=""),
                    "max_repeats": param("integer", "Max times to fire, 0=unlimited.", default=0),
                    "label": param("string", "Human-friendly label.", default=""),
                },
                required=["cron_expression"],
            ),
            handler=cron_task_schedule,
            category="scheduler",
        ),
        ToolSpec(
            name="scheduled_tasks_list",
            description="List all active scheduled tasks, reminders, and cron jobs.",
            parameters=object_schema(properties={}),
            handler=scheduled_tasks_list,
            category="scheduler",
        ),
        ToolSpec(
            name="scheduled_task_cancel",
            description="Cancel a scheduled task by its task ID.",
            parameters=object_schema(
                properties={
                    "task_id": param("string", "The task ID to cancel."),
                },
                required=["task_id"],
            ),
            handler=scheduled_task_cancel,
            category="scheduler",
        ),
    ]
