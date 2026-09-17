"""The confirmation broker: turns a tier-``confirm`` decision into a yes/no.

A confirmed tool call is *held*, not executed. The gateway asks out loud, and the
call proceeds only when the master answers. Three properties matter more than the
mechanism:

* **Silence is a no.** The request expires after ``confirm_timeout_seconds`` and
  resolves to refused, so walking away from a prompt can never mean assent.
* **A spoken answer is enough.** :func:`classify_reply` is a local lexicon, not a
  model round trip -- a "no" has to land instantly and must not depend on the
  brain being reachable.
* **Everything is recorded.** Refusals, timeouts and approvals all reach the
  audit log, because what Jarvis declined to do is part of its record.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from .events import EventBus

ReplyKind = Literal["yes", "no", "unclear"]

AFFIRMATIVE = (
    "yes", "yep", "yeah", "yup", "ya", "sure", "ok", "okay", "k",
    "go ahead", "go for it", "do it", "please do", "proceed", "continue",
    "affirmative", "correct", "confirm", "confirmed", "approved", "approve",
    "yes please", "please do it", "do that", "that is correct", "send it",
    "make it so", "carry on", "aye", "fine",
)

NEGATIVE = (
    "no", "nope", "nah", "naw", "negative", "don't", "do not", "dont",
    "stop", "cancel", "abort", "halt", "never mind", "nevermind", "forget it",
    "leave it", "no thanks", "no thank you", "not now", "hold on", "wait",
    "deny", "denied", "reject", "rejected", "skip it", "skip",
)

# Checked before the affirmative lexicon: "don't do it" contains "do it".
_NEGATION_CUES = (
    "don't", "dont", "do not", "doesn't", "does not", "not now", "no thank",
    "never mind", "nevermind", "cancel", "abort", "stop", "reject", "deny",
)


def _normalise(text: str) -> str:
    lowered = (text or "").strip().lower()
    lowered = re.sub(r"[^\w\s']", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def classify_reply(text: str) -> ReplyKind:
    """Interpret a spoken answer as yes, no, or unclear.

    Negatives are checked first and phrase matches beat single words, so
    "don't do it" and "no thanks" can never be read as assent.
    """
    normalised = _normalise(text)
    if not normalised:
        return "unclear"

    if normalised in NEGATIVE:
        return "no"
    if normalised in AFFIRMATIVE:
        return "yes"

    for cue in _NEGATION_CUES:
        if re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", normalised):
            return "no"
    for phrase in NEGATIVE:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalised):
            return "no"
    for phrase in AFFIRMATIVE:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalised):
            return "yes"
    return "unclear"


@dataclass
class PendingConfirmation:
    """One held tool call awaiting a decision."""

    id: str
    tool: str
    args: dict[str, Any]
    summary: str
    created_at: float
    expires_at: float
    approved: bool | None = None
    source: str | None = None  # ui | voice | timeout | shutdown
    decided_at: float | None = None
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args,
            "summary": self.summary,
            "seconds_left": round(self.seconds_left, 1),
            "approved": self.approved,
            "source": self.source,
        }


class ConfirmationBroker:
    def __init__(
        self,
        timeout_seconds: float = 30.0,
        *,
        bus: EventBus | None = None,
        audit: Any | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.bus = bus
        self.audit = audit
        self._pending: dict[str, PendingConfirmation] = {}
        self._lock = asyncio.Lock()

    # -- broker side ------------------------------------------------------

    async def request(self, tool: str, args: dict[str, Any], summary: str) -> bool:
        """Hold a tool call until it is approved, refused, or times out."""
        now = time.time()
        pending = PendingConfirmation(
            id=uuid.uuid4().hex[:12],
            tool=tool,
            args=args,
            summary=summary,
            created_at=now,
            expires_at=now + self.timeout_seconds,
        )
        async with self._lock:
            self._pending[pending.id] = pending

        if self.bus is not None:
            self.bus.publish({"type": "confirm_request", **pending.as_dict()})

        try:
            await asyncio.wait_for(pending._event.wait(), timeout=self.timeout_seconds)
        except TimeoutError:
            pending.approved = False
            pending.source = "timeout"
            pending.decided_at = time.time()
            if self.bus is not None:
                self.bus.publish(
                    {
                        "type": "confirm_result",
                        "id": pending.id,
                        "tool": tool,
                        "approved": False,
                        "source": "timeout",
                        "reason": f"no answer within {self.timeout_seconds:g}s, treated as a refusal",
                    }
                )
        finally:
            async with self._lock:
                self._pending.pop(pending.id, None)

        approved = bool(pending.approved)
        if self.audit is not None:
            self.audit.record(
                "confirmation",
                tool=tool,
                approval_id=pending.id,
                approved=approved,
                source=pending.source,
                args=args,
            )
        return approved

    def resolve(self, confirmation_id: str, approved: bool, source: str = "ui") -> bool:
        """Answer a held call. Returns False when the id is unknown or expired."""
        pending = self._pending.get(confirmation_id)
        if pending is None or pending.decided_at is not None:
            return False
        pending.approved = bool(approved)
        pending.source = source
        pending.decided_at = time.time()
        pending._event.set()
        if self.bus is not None:
            self.bus.publish(
                {
                    "type": "confirm_result",
                    "id": pending.id,
                    "tool": pending.tool,
                    "approved": bool(approved),
                    "source": source,
                }
            )
        return True

    def resolve_by_voice(self, text: str) -> PendingConfirmation | None:
        """Apply a spoken answer to the oldest held call, if it is decisive."""
        pending = self.oldest()
        if pending is None:
            return None
        verdict = classify_reply(text)
        if verdict == "unclear":
            return None
        self.resolve(pending.id, verdict == "yes", source="voice")
        return pending

    # -- introspection ----------------------------------------------------

    def oldest(self) -> PendingConfirmation | None:
        if not self._pending:
            return None
        return min(self._pending.values(), key=lambda p: p.created_at)

    def get(self, confirmation_id: str) -> PendingConfirmation | None:
        return self._pending.get(confirmation_id)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            p.as_dict()
            for p in sorted(self._pending.values(), key=lambda p: p.created_at)
        ]

    def cancel_all(self, reason: str = "shutdown") -> int:
        """Refuse every held call. Used on shutdown so nothing is left hanging."""
        cancelled = 0
        for pending in list(self._pending.values()):
            if pending.decided_at is None:
                pending.approved = False
                pending.source = reason
                pending.decided_at = time.time()
                pending._event.set()
                cancelled += 1
        return cancelled


def describe_call(tool: str, args: dict[str, Any]) -> str:
    """A short spoken summary of what is being asked for.

    Written for the ear: no JSON, no field names, and it leads with the verb so
    the master can answer without waiting for the whole sentence.

    Truncation has a safety rule, not just a length one: for the tools where the
    DANGER lives in the tail of the text (shell commands, mail bodies), the tail
    must never be silently cut -- a master cannot approve what was never spoken.
    Those are disclosed in full and marked; everything else truncates as before.
    """
    def first(*names: str) -> Any:
        for name in names:
            if name in args and args[name] not in (None, ""):
                return args[name]
        return None

    label = tool.removeprefix("mcp__").replace("__", " ")
    verb = {
        "shell_run": "run the command",
        "app_launch": "open",
        "fs_write": "write the file",
        "fs_delete": "delete",
        "fs_mkdir": "create the folder",
        "mail_send": "send an email",
        "mail_list_unread": "read your unread mail",
        "calendar_add": "add a calendar event",
        "clipboard_write": "put text on the clipboard",
        "browser_open": "open the browser at",
        "browser_click": "click",
        "browser_type": "type into the page",
    }.get(tool, f"use {label} on")

    # Full disclosure: the payload must be heard in its entirety for these.
    if tool == "shell_run":
        command = str(first("command") or "")
        cwd = str(first("cwd") or "")
        return f"run the command {command}" + (f" in {cwd}" if cwd else "")
    if tool == "mail_send":
        to = str(first("to") or "")
        subject = str(first("subject") or "(no subject)")
        body = str(first("body") or "")
        summary = f"send an email to {to}, subject {subject}"
        if body:
            summary += f", saying: {body}"
        return summary
    if tool == "app_launch":
        # The argument must be spoken ("open powershell" alone hides the payload)
        # but may be truncated: anything pattern-dangerous here is refused outright
        # by the deny-list long before a summary is ever spoken.
        app = str(first("app") or "")
        argument = str(first("argument") or "")
        if len(argument) > 120:
            argument = argument[:117] + "..."
        return f"open {app}" + (f" with {argument}" if argument else "")

    target = first("command", "path", "url", "to", "title", "selector", "name", "app", "text")
    if target is None:
        return f"{verb} {label}".strip()
    rendered = str(target)
    if len(rendered) > 120:
        rendered = rendered[:117] + "..."
    return f"{verb} {rendered}".strip()
