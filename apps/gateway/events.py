"""In-process pub/sub so gateway internals can push events to connected clients.

The confirmation broker, the agent loop and the wake-word detector all publish
here; every websocket subscriber gets its own bounded queue. Delivery is
best-effort by design -- a slow or stalled client must never block a turn, so a
full queue drops its oldest event rather than applying backpressure.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

MAX_QUEUE = 128


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        """Fan out an event. Safe to call from sync code and from threads."""
        payload = {"ts": time.time(), **event}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the oldest event so the newest state always wins.
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def publish_threadsafe(
        self, event: dict[str, Any], loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        """Publish from a worker thread (e.g. the wake-word detector)."""
        loop = loop or _active_loop()
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self.publish, event)


def _active_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
