from __future__ import annotations

import asyncio
import threading
from typing import Any


class EventBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()

    def subscribe(self) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue]:
        item = (asyncio.get_running_loop(), asyncio.Queue(maxsize=100))
        with self._lock:
            self._subscribers.add(item)
        return item

    def unsubscribe(self, item: tuple[asyncio.AbstractEventLoop, asyncio.Queue]) -> None:
        with self._lock:
            self._subscribers.discard(item)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            def deliver(target=queue, payload=event) -> None:
                if target.full():
                    try:
                        target.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                target.put_nowait(payload)

            if not loop.is_closed():
                loop.call_soon_threadsafe(deliver)
