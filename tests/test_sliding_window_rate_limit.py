import asyncio
import time
import pytest
from VoiceClonerAppServer.core.redis_client import check_rate_limit, redis_client


@pytest.mark.asyncio
async def test_sliding_window_rate_limit_allows_under_limit():
    user_id = f"test_rate_user_{time.time()}"
    limit = 5

    # 5 requests should all succeed
    for i in range(limit):
        allowed = await check_rate_limit(user_id, max_requests=limit)
        assert allowed is True, f"Request {i+1} should be allowed"

    # 6th request must be rejected
    rejected = await check_rate_limit(user_id, max_requests=limit)
    assert rejected is False, "Request over limit should be rejected"


@pytest.mark.asyncio
async def test_sliding_window_rate_limit_prevents_edge_spike_burst(monkeypatch):
    """
    Simulate requests sent across minute boundary.
    In Fixed Window, 5 requests at second 55 and 5 requests at second 05 (10s apart)
    would all be allowed (total 10 in 10s!).
    In Sliding Window, the 60-second sliding window weights the previous minute's burst
    and rejects excess requests at the boundary.
    """
    user_id = f"test_edge_user_{time.time()}"
    limit = 5

    # 1. Simulate minute 1000 at second 55 (t = 60055.0)
    monkeypatch.setattr(time, "time", lambda: 60055.0)
    for _ in range(limit):
        assert await check_rate_limit(user_id, max_requests=limit) is True

    # 2. Simulate minute 1001 at second 05 (t = 60065.0, exactly 10s later)
    # Under fixed window this would reset to 0 and allow 5 more requests.
    # Under sliding window, prev_weight = 1 - 5/60 = 0.9167, estimated = 1 + 5 * 0.9167 = 5.58 > 5 -> REJECTED!
    monkeypatch.setattr(time, "time", lambda: 60065.0)
    assert await check_rate_limit(user_id, max_requests=limit) is False

    # 3. Simulate minute 1001 at second 56 (t = 60116.0, 61s later)
    # The previous burst at t=60055 is now completely outside the 60-second window!
    # prev_weight = 1 - 56/60 = 0.067, estimated = 1 + 5 * 0.067 = 1.33 <= 5 -> ALLOWED!
    monkeypatch.setattr(time, "time", lambda: 60116.0)
    assert await check_rate_limit(user_id, max_requests=limit) is True
