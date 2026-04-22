from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class JobQueueFullError(Exception):
    pass


class JobQueueAbortedError(Exception):
    pass


class JobLimiter:
    def __init__(self, concurrency: int, max_queued: int):
        self._sem = asyncio.Semaphore(concurrency)
        self._max_queued = max_queued
        self._queued = 0
        self._active = 0
        self._lock = asyncio.Lock()

    async def run(
        self,
        coro_factory: Callable[[], Awaitable[T]],
        *,
        abort_event: asyncio.Event | None = None,
    ) -> T:
        if abort_event is not None and abort_event.is_set():
            raise JobQueueAbortedError()

        async with self._lock:
            if self._queued >= self._max_queued and self._sem.locked():
                raise JobQueueFullError()
            self._queued += 1

        try:
            if abort_event is None:
                await self._sem.acquire()
            else:
                acquire_task = asyncio.create_task(self._sem.acquire())
                abort_task = asyncio.create_task(abort_event.wait())
                done, pending = await asyncio.wait(
                    {acquire_task, abort_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if abort_task in done and acquire_task not in done:
                    raise JobQueueAbortedError()
        finally:
            async with self._lock:
                self._queued -= 1

        async with self._lock:
            self._active += 1
        try:
            return await coro_factory()
        finally:
            self._sem.release()
            async with self._lock:
                self._active -= 1

    def stats(self) -> dict[str, int]:
        return {
            "activeJobs": self._active,
            "queuedJobs": self._queued,
        }
