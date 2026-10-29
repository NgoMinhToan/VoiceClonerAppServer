import asyncio
import pytest
from fastapi import HTTPException
from VoiceClonerAppServer.services.concurrency_limiter import UserConcurrencyLimiter


@pytest.mark.asyncio
async def test_concurrency_limiter_allows_under_limit():
    limiter = UserConcurrencyLimiter()
    entered = False
    async with limiter.acquire("user_1", max_concurrent=1, max_waiters=1):
        entered = True
    assert entered is True
    assert limiter.get_active_count("user_1") == 0


@pytest.mark.asyncio
async def test_concurrency_limiter_queues_and_rejects_overflow():
    limiter = UserConcurrencyLimiter()
    events = []

    async def task_1():
        async with limiter.acquire("user_2", max_concurrent=1, max_waiters=1):
            events.append("t1_start")
            await asyncio.sleep(0.1)
            events.append("t1_end")

    async def task_2():
        await asyncio.sleep(0.02)
        async with limiter.acquire("user_2", max_concurrent=1, max_waiters=1):
            events.append("t2_start")

    async def task_3():
        await asyncio.sleep(0.04)
        with pytest.raises(HTTPException) as exc:
            async with limiter.acquire("user_2", max_concurrent=1, max_waiters=1):
                events.append("t3_should_not_run")
        assert exc.value.status_code == 429
        assert "đầy" in exc.value.detail.lower()

    await asyncio.gather(task_1(), task_2(), task_3())
    assert events == ["t1_start", "t1_end", "t2_start"]


@pytest.mark.asyncio
async def test_concurrency_limiter_releases_on_error():
    limiter = UserConcurrencyLimiter()
    with pytest.raises(ValueError):
        async with limiter.acquire("user_3", max_concurrent=1, max_waiters=0):
            raise ValueError("Intentional synthesis failure")

    assert limiter.get_active_count("user_3") == 0
    # Next call should succeed immediately
    entered = False
    async with limiter.acquire("user_3", max_concurrent=1, max_waiters=0):
        entered = True
    assert entered is True
