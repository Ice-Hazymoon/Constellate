from __future__ import annotations

import asyncio

import pytest

from star_server.limiter import JobLimiter, JobQueueAbortedError, JobQueueFullError


@pytest.mark.asyncio
async def test_limiter_enforces_concurrency_and_stats() -> None:
    limiter = JobLimiter(1, 4)
    gate = asyncio.Event()
    events: list[str] = []

    async def first_job() -> None:
        events.append("first-start")
        await gate.wait()
        events.append("first-end")

    async def second_job() -> None:
        events.append("second-start")
        events.append("second-end")

    first_task = asyncio.create_task(limiter.run(first_job))
    await asyncio.sleep(0)
    second_task = asyncio.create_task(limiter.run(second_job))
    await asyncio.sleep(0)

    assert limiter.stats() == {"activeJobs": 1, "queuedJobs": 1}

    gate.set()
    await asyncio.gather(first_task, second_task)

    assert events == ["first-start", "first-end", "second-start", "second-end"]
    assert limiter.stats() == {"activeJobs": 0, "queuedJobs": 0}


@pytest.mark.asyncio
async def test_limiter_rejects_when_queue_is_full() -> None:
    limiter = JobLimiter(1, 1)
    gate = asyncio.Event()

    first_task = asyncio.create_task(limiter.run(lambda: gate.wait()))
    await asyncio.sleep(0)
    second_task = asyncio.create_task(limiter.run(lambda: asyncio.sleep(0)))
    await asyncio.sleep(0)

    with pytest.raises(JobQueueFullError):
        await limiter.run(lambda: asyncio.sleep(0))

    gate.set()
    await asyncio.gather(first_task, second_task)


@pytest.mark.asyncio
async def test_limiter_removes_aborted_jobs_from_queue() -> None:
    limiter = JobLimiter(1, 2)
    gate = asyncio.Event()
    first_task = asyncio.create_task(limiter.run(lambda: gate.wait()))
    await asyncio.sleep(0)

    abort_event = asyncio.Event()
    queued_task = asyncio.create_task(
        limiter.run(lambda: asyncio.sleep(0), abort_event=abort_event)
    )
    await asyncio.sleep(0)
    assert limiter.stats() == {"activeJobs": 1, "queuedJobs": 1}

    abort_event.set()
    with pytest.raises(JobQueueAbortedError):
        await queued_task

    assert limiter.stats() == {"activeJobs": 1, "queuedJobs": 0}
    gate.set()
    await first_task
