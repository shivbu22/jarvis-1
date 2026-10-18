"""Asking the master a question, mid-turn.

The confirmation broker answers "shall I?" with yes or no. This answers the
other thing an assistant genuinely needs to know: *what did you mean?* When a
request is ambiguous -- "send it to him", "book the usual" -- guessing is worse
than asking, so a turn can be suspended while Jarvis puts a question to the
master and waits for words rather than a button.

Three properties matter here, and they are the same ones the confirmation gate
has, for the same reasons:

* **A question is spoken, not just displayed.** The master may be across the
  room; the question goes out through the voice as well as the UI.
* **No answer is not consent.** A timeout resolves to "no answer", and the tool
  reports that to the model as a fact, so it explains what it will do instead
  rather than assuming.
* **Everything is recorded.** The question, the answer and the timeout all reach
  the audit log.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .events import EventBus

logger = logging.getLogger(__name__)

#: Generous compared with the permission timeout. The master may be away from the
#: keyboard, and the cost of asking is a pause rather than a wrong action.
DEFAULT_QUESTION_TIMEOUT = 120.0


@dataclass
class PendingQuestion:
    """One question awaiting words from the master."""

    id: str
    question: str
    options: list[str]
    created_at: float
    expires_at: float
    answer: str | None = None
    source: str | None = None  # ui | voice | timeout | shutdown
    decided_at: float | None = None
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "options": list(self.options),
            "seconds_left": round(self.seconds_left, 1),
            "answer": self.answer,
            "source": self.source,
        }


class InteractionBroker:
    """Holds a turn open while a question is answered."""

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_QUESTION_TIMEOUT,
        *,
        bus: EventBus | None = None,
        audit: Any | None = None,
        speak: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.bus = bus
        self.audit = audit
        self._speak = speak
        self._pending: dict[str, PendingQuestion] = {}
        self._lock = asyncio.Lock()

    # -- broker side ------------------------------------------------------

    async def ask(
        self, question: str, options: list[str] | None = None, *, timeout: float | None = None
    ) -> str:
        """Put a question to the master and wait for an answer.

        Returns the answer, or an empty string when nothing was said in time --
        which the caller must treat as "no answer", never as agreement.
        """
        question = (question or "").strip()
        if not question:
            return ""
        window = self.timeout_seconds if timeout is None else max(1.0, float(timeout))
        now = time.time()
        pending = PendingQuestion(
            id=uuid.uuid4().hex[:12],
            question=question,
            options=[o for o in (options or []) if o][:4],
            created_at=now,
            expires_at=now + window,
        )
        async with self._lock:
            self._pending[pending.id] = pending

        if self.bus is not None:
            self.bus.publish({"type": "question", **pending.as_dict()})
        # Spoken as well as shown: the master is usually not looking at the screen.
        if self._speak is not None:
            try:
                await self._speak(question)
            except Exception:
                logger.exception("speaking the question failed")

        try:
            await asyncio.wait_for(pending._event.wait(), timeout=window)
        except TimeoutError:
            pending.source = "timeout"
            pending.decided_at = time.time()
            if self.bus is not None:
                self.bus.publish(
                    {
                        "type": "question_result",
                        "id": pending.id,
                        "answered": False,
                        "source": "timeout",
                        "reason": f"no answer within {window:g}s",
                    }
                )
            logger.info("question %s timed out", pending.id)
        finally:
            async with self._lock:
                self._pending.pop(pending.id, None)

        answer = (pending.answer or "").strip()
        if self.audit is not None:
            self.audit.record(
                "question",
                question_id=pending.id,
                question=question,
                answer=answer,
                source=pending.source,
                answered=bool(answer),
            )
        return answer

    def resolve(self, question_id: str, text: str, source: str = "ui") -> bool:
        """Answer a held question. False when the id is unknown or already answered."""
        pending = self._pending.get(question_id)
        if pending is None or pending.decided_at is not None:
            return False
        pending.answer = (text or "").strip()
        pending.source = source
        pending.decided_at = time.time()
        pending._event.set()
        if self.bus is not None:
            self.bus.publish(
                {
                    "type": "question_result",
                    "id": pending.id,
                    "answered": bool(pending.answer),
                    "answer": pending.answer,
                    "source": source,
                }
            )
        return True

    def resolve_by_voice(self, text: str, source: str = "voice") -> PendingQuestion | None:
        """Route something the master said to the oldest open question."""
        pending = self.oldest()
        if pending is None:
            return None
        if not (text or "").strip():
            return None
        self.resolve(pending.id, text, source=source)
        return pending

    # -- introspection ----------------------------------------------------

    def oldest(self) -> PendingQuestion | None:
        if not self._pending:
            return None
        return min(self._pending.values(), key=lambda p: p.created_at)

    def pending(self) -> bool:
        return bool(self._pending)

    def snapshot(self) -> list[dict[str, Any]]:
        return [p.as_dict() for p in sorted(self._pending.values(), key=lambda p: p.created_at)]

    def cancel_all(self, reason: str = "shutdown") -> int:
        """Release every held question, unanswered. Used on shutdown."""
        cancelled = 0
        for pending in list(self._pending.values()):
            if pending.decided_at is None:
                pending.source = reason
                pending.decided_at = time.time()
                pending._event.set()
                cancelled += 1
        return cancelled


__all__ = ["DEFAULT_QUESTION_TIMEOUT", "InteractionBroker", "PendingQuestion"]
