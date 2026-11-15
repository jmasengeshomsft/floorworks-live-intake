"""Application-owned background work with bounded draining."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

log = logging.getLogger("gateway.tasks")
T = TypeVar("T")


class TaskSupervisor:
    def __init__(self):
        self.pending: set[asyncio.Task] = set()
        self.closing = False

    def spawn(self, work: Coroutine[Any, Any, T], *, name: str = "gateway") -> asyncio.Task[T]:
        if self.closing:
            work.close()
            raise RuntimeError("application is draining")
        task = asyncio.create_task(work, name=name)
        self.pending.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task) -> None:
        self.pending.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("background task %s failed: %s", task.get_name(), type(error).__name__)

    async def close(self, timeout: float = 10) -> None:
        self.closing = True
        tasks = list(self.pending)
        if not tasks:
            return
        try:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                log.warning("cancelling %d background tasks after drain deadline", len(pending))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
