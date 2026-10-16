import httpx
import pytest
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.services.voice_service import VoiceService


@pytest.mark.asyncio
async def test_dynamic_voice_discovery_aggregates_multi_workers():
    service = VoiceService()

    # Initial state uses fallback
    initial_count = len(service.list_builtin_voices())
    assert initial_count >= 23

    # Mock responses for 2 independent workers
    worker1_voices = {
        "total_count": 2,
        "voices": [
            {"id": "custom_alpha", "name": "Alpha Voice", "gender": "male", "description": "Custom Male"},
            {"id": "shared_voice", "name": "Shared Voice", "gender": "female", "description": "Shared"},
        ]
    }
    worker2_voices = {
        "total_count": 2,
        "voices": [
            {"id": "custom_beta", "name": "Beta Voice", "gender": "female", "description": "Custom Female"},
            {"id": "shared_voice", "name": "Shared Voice", "gender": "female", "description": "Shared"},
        ]
    }

    async def mock_handler(request: httpx.Request):
        url_str = str(request.url)
        if "worker-1" in url_str:
            return httpx.Response(200, json=worker1_voices)
        elif "worker-2" in url_str:
            return httpx.Response(200, json=worker2_voices)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=transport)

    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({
        "model_workers": [
            {"id": "w1", "url": "http://worker-1:8001", "api_key": "k1", "weight": 1, "is_active": True},
            {"id": "w2", "url": "http://worker-2:8002", "api_key": "k2", "weight": 1, "is_active": True},
        ]
    })

    try:
        discovered_count = await service.discover_all_workers(http_client=mock_client)
        # Should aggregate custom_alpha, custom_beta, shared_voice (deduplicated) = 3 voices
        assert discovered_count == 3
        voices = service.list_builtin_voices()
        ids = {v.id for v in voices}
        assert ids == {"custom_alpha", "custom_beta", "shared_voice"}

        # Lookup by id and by name
        assert service.get_voice_by_id("custom_alpha") is not None
        assert service.get_voice_by_id("Alpha Voice") is not None
        assert service.get_voice_by_id("Beta Voice") is not None
    finally:
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})
        await mock_client.aclose()
