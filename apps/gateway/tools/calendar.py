"""Calendar tools backed by a local iCalendar file.

Deliberately file-based rather than API-based: an `.ics` in the Jarvis data
folder needs no OAuth, no signup and no network, and it imports into Google
Calendar, Outlook or Apple Calendar in one drag. That makes it a real calendar
today and a migration path later, instead of a feature blocked on credentials.

Times are parsed leniently -- "2026-09-18 15:00", "18 September 2026 3pm" -- because
a spoken assistant receives whatever the master said, not ISO-8601.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

DEFAULT_DURATION_MINUTES = 60


def _calendar_path(ctx: ToolContext) -> Path:
    path = Path(ctx.settings.data_dir) / "calendar.ics"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _parse_when(value: str, *, default: datetime | None = None) -> datetime:
    """Parse the many shapes a spoken date can take.

    Raises:
        ValueError: when nothing recognizable is found, so the model can ask again
            rather than the event landing at a silently wrong time.
    """
    raw = (value or "").strip()
    if not raw:
        if default is not None:
            return default
        raise ValueError("no date or time was given")

    lowered = raw.lower()
    now = datetime.now()
    if lowered in {"now", "right now"}:
        return now.replace(second=0, microsecond=0)
    if lowered in {"today", "tonight"}:
        return now.replace(hour=20 if lowered == "tonight" else now.hour, minute=0, second=0, microsecond=0)
    if lowered == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    # "in 2 hours", "in 30 minutes"
    match = re.fullmatch(r"in\s+(\d+)\s*(minute|min|hour|hr|day)s?", lowered)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("min"):
            return (now + timedelta(minutes=amount)).replace(second=0, microsecond=0)
        if unit.startswith(("hour", "hr")):
            return (now + timedelta(hours=amount)).replace(second=0, microsecond=0)
        return (now + timedelta(days=amount)).replace(hour=9, minute=0, second=0, microsecond=0)

    cleaned = raw.replace(",", " ").replace(" at ", " ").strip()
    formats = (
        "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d",
        "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%d %B %Y %H:%M", "%d %B %Y", "%d %b %Y %H:%M", "%d %b %Y",
        "%B %d %Y %H:%M", "%B %d %Y", "%b %d %Y %H:%M",
        "%H:%M", "%I%p", "%I %p", "%I:%M%p", "%I:%M %p",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:  # a bare time meant today
            return now.replace(
                hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
            )
        return parsed
    raise ValueError(
        f"I could not understand the date '{value}'. Give it as '2026-09-18 15:00'."
    )


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _unfold(text: str) -> list[str]:
    """Undo iCalendar line folding so long descriptions survive a round trip."""
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _read_events(ctx: ToolContext) -> list[dict[str, Any]]:
    path = _calendar_path(ctx)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in _unfold(text):
        if line.upper().startswith("BEGIN:VEVENT"):
            current = {}
        elif line.upper().startswith("END:VEVENT"):
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            # RFC 5545 property names are case-insensitive, and files imported
            # from elsewhere use a mix, so compare on the upper-cased key.
            key = key.upper()
            if key.startswith("DTSTART"):
                current["start"] = value
            elif key.startswith("DTEND"):
                current["end"] = value
            elif key == "SUMMARY":
                current["summary"] = value
            elif key == "LOCATION":
                current["location"] = value
            elif key == "DESCRIPTION":
                current["description"] = value
            elif key == "UID":
                current["uid"] = value
    for event in events:
        raw_start = str(event.get("start", ""))
        try:
            event["start_dt"] = datetime.strptime(raw_start, "%Y%m%dT%H%M%S")
        except ValueError:
            event["start_dt"] = None
    events.sort(key=lambda e: (e["start_dt"] is None, e.get("start_dt") or datetime.max))
    return events


def _write_event(ctx: ToolContext, fields: dict[str, str]) -> None:
    path = _calendar_path(ctx)
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if not existing.strip():
        existing = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Jarvis//EN\nCALSCALE:GREGORIAN\n"
    if not existing.endswith("\n"):
        existing += "\n"

    # Property names are upper-cased because RFC 5545 names are conventionally
    # upper-case and every real calendar client expects it. Writing "dtstart:"
    # produced a file our own reader could not parse, which is how this was found.
    lines = ["BEGIN:VEVENT", f"UID:{fields['uid']}", f"DTSTAMP:{fields['dtstamp']}"]
    lines += [
        f"{key.upper()}:{value}"
        for key, value in fields.items()
        if key.lower() not in {"uid", "dtstamp"}
    ]
    lines.append("END:VEVENT")
    existing += "\n".join(lines) + "\n"

    if not existing.rstrip().endswith("END:VCALENDAR"):
        existing = existing.rstrip() + "\nEND:VCALENDAR\n"
    path.write_text(existing, encoding="utf-8")


def calendar_add(
    ctx: ToolContext,
    title: str,
    start: str,
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
    location: str = "",
    notes: str = "",
) -> ToolOutput:
    try:
        starts = _parse_when(start)
    except ValueError as exc:
        return ToolOutput(text=str(exc), ok=False)

    duration = max(5, min(int(duration_minutes or DEFAULT_DURATION_MINUTES), 24 * 60))
    ends = starts + timedelta(minutes=duration)

    try:
        _write_event(
            ctx,
            {
                "uid": f"{uuid.uuid4().hex}@jarvis",
                "dtstamp": _fmt(datetime.now()),
                "dtstart": _fmt(starts),
                "dtend": _fmt(ends),
                "summary": title.replace("\n", " "),
                "location": location.replace("\n", " "),
                "description": notes.replace("\n", " "),
            },
        )
    except OSError as exc:
        return ToolOutput(text=f"I could not write to the calendar: {exc}", ok=False)

    when = starts.strftime("%A %d %B at %H:%M")
    return ToolOutput(
        text=f"Added '{title}' on {when}, for {duration} minutes.",
        data={
            "title": title,
            "start": starts.isoformat(),
            "end": ends.isoformat(),
            "location": location,
            "calendar_file": str(_calendar_path(ctx)),
        },
    )


def calendar_list(ctx: ToolContext, days: int = 7, include_past: bool = False) -> ToolOutput:
    days = max(1, min(int(days or 7), 365))
    events = _read_events(ctx)
    if not events:
        return ToolOutput(
            text="The calendar is empty.",
            data={"events": [], "calendar_file": str(_calendar_path(ctx))},
        )

    now = datetime.now()
    horizon = now + timedelta(days=days)
    upcoming = [
        event
        for event in events
        if event.get("start_dt") is not None
        and (include_past or event["start_dt"] >= now - timedelta(minutes=1))
        and event["start_dt"] <= horizon
    ]
    if not upcoming:
        return ToolOutput(
            text=f"Nothing in the calendar for the next {days} day(s).",
            data={"events": []},
        )

    lines = [f"{len(upcoming)} event(s) in the next {days} day(s):"]
    for event in upcoming:
        start_dt = event["start_dt"]
        lines.append(
            f"- {start_dt.strftime('%a %d %b %H:%M')}: {event.get('summary', '(untitled)')}"
            + (f" at {event['location']}" if event.get("location") else "")
        )
    return ToolOutput(
        text="\n".join(lines),
        data={
            "events": [
                {
                    "summary": event.get("summary"),
                    "start": event["start_dt"].isoformat() if event.get("start_dt") else None,
                    "location": event.get("location", ""),
                }
                for event in upcoming
            ],
            "calendar_file": str(_calendar_path(ctx)),
            "generated_at": time.time(),
        },
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="calendar_add",
            description=(
                "Add an event to the local calendar. Needs permission. Write the start "
                "as 'today', 'tomorrow', 'in 2 hours', or '2026-09-18 15:00'."
            ),
            parameters=object_schema(
                {
                    "title": param("title", "string", "Short event title."),
                    "start": param("start", "string", "When it starts."),
                    "duration_minutes": param(
                        "duration_minutes", "integer", "Length in minutes.",
                        default=DEFAULT_DURATION_MINUTES, minimum=5, maximum=1440,
                    ),
                    "location": param("location", "string", "Optional place.", default=""),
                    "notes": param("notes", "string", "Optional notes.", default=""),
                },
                ["title", "start"],
            ),
            handler=calendar_add,
            executing=True,
            category="calendar",
        ),
        ToolSpec(
            name="calendar_list",
            description="List upcoming calendar events.",
            parameters=object_schema(
                {
                    "days": param(
                        "days", "integer", "How far ahead to look.",
                        default=7, minimum=1, maximum=365,
                    ),
                    "include_past": param(
                        "include_past", "boolean", "Include events that already happened.",
                        default=False,
                    ),
                },
                [],
            ),
            handler=calendar_list,
            executing=False,
            category="calendar",
        ),
    ]
