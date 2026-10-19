import pytest
from httpx import AsyncClient, ASGITransport
from VoiceClonerAppServer.main import app

@pytest.mark.asyncio
async def test_metrics_summary_contains_keeper_extended_fields():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(
            "/v1/management/metrics/summary?hours=24",
            headers={"X-Management-Key": "vc_admin_secret_key_123"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "cache_metrics" in data
        assert "hourly_trends" in data
        assert "rpm" in data
        assert "cpm" in data
        assert "recent_logs" in data
        assert isinstance(data["cache_metrics"]["cache_hit_rate"], (int, float))
        assert isinstance(data["hourly_trends"], list)
