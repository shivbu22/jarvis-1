"""Append-only audit trail.

Every tool decision lands here -- refusals and timeouts as well as executions --
because "what did it refuse?" is as important as "what did it do?". The file is
JSONL so it can be tailed, grepped or replayed without a schema migration, and
it is gitignored because a transcript is personal data.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_TAIL = 500


class AuditLog:
    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()
        # Set when a write fails (a full disk, a locked file). The entry is still
        # returned to the caller; /health reports this so a broken trail is visible
        # rather than silently absent.
        self.write_error: str | None = None
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "event": event,
        }
        for key, value in fields.items():
            entry[key] = _jsonable(value)
        if self.enabled:
            # Recording must never be able to end a turn. If the write fails the
            # decision still stands and still gets a spoken answer; the failure is
            # logged, flagged on ``write_error``, and surfaced through /health.
            line = json.dumps(entry, ensure_ascii=False)
            try:
                with self._lock, self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                self.write_error = None
            except OSError as exc:
                self.write_error = f"{type(exc).__name__}: {exc}"
                logger.warning("audit entry could not be written: %s", exc)
        return entry

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the last `limit` entries, newest last. Never raises."""
        limit = max(1, min(limit, MAX_TAIL))
        if not self.path.exists():
            return []
        try:
            with self._lock:
                lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
            entries: list[dict[str, Any]] = []
            for line in lines[-limit:]:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return entries
        except OSError:
            return []


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
