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
        assert "failed_requests" in data
        assert "success_requests" in data
        assert "analysis" in data
        assert "token_usage" in data["analysis"]
        assert "model_usage" in data["analysis"]
        assert "api_key_composition" in data["analysis"]
        assert "latency_diagnostics" in data["analysis"]
        assert isinstance(data["cache_metrics"]["cache_hit_rate"], (int, float))
        assert isinstance(data["hourly_trends"], list)
        if len(data["recent_logs"]) > 0:
            first_log = data["recent_logs"][0]
            assert "key_name" in first_log
            assert "worker" in first_log
            assert "chars" in first_log
            assert "status" in first_log
            assert "is_cache_hit" in first_log

        # Verify dedicated analysis endpoint
        res_analysis = await ac.get(
            "/v1/management/metrics/analysis?hours=24&range_type=today",
            headers={"X-Management-Key": "vc_admin_secret_key_123"},
        )
        assert res_analysis.status_code == 200
        analysis_data = res_analysis.json()
        assert "token_usage" in analysis_data
        assert "model_usage" in analysis_data

