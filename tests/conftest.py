"""Shared test fixtures for the VoiceCloner gateway test suite."""

import asyncio

import pytest

from VoiceClonerAppServer.core.redis_client import redis_client
from VoiceClonerAppServer.services.anonymous_service import signup_rate_key


@pytest.fixture(autouse=True)
def reset_anonymous_signup_bucket():
    """Keep the per-IP guest signup counter from leaking across tests/runs.

    The production limit is intentionally low (10/day), and the test client
    always reports the same IP, so without a reset a full suite run would
    exhaust the bucket and fail unrelated anonymous tests on later runs.
    """
    asyncio.run(redis_client.delete(signup_rate_key("testclient")))
    yield
    asyncio.run(redis_client.delete(signup_rate_key("testclient")))
