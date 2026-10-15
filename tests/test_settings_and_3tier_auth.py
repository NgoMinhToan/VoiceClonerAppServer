import asyncio
import os
import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.config.settings_manager import settings_manager
from VoiceClonerAppServer.core.database import init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def test_management_api_rejects_unauthorized():
    res = client.get("/v1/management/settings")
    assert res.status_code == 401


def test_management_api_accepts_valid_management_key():
    key = settings_manager.get_settings().security.management_key
    res = client.get("/v1/management/settings", headers={"X-Management-Key": key})
    assert res.status_code == 200
    data = res.json()
    assert "concurrency" in data
    assert "model_workers" in data


def test_speech_api_rejects_without_client_api_key():
    res = client.post("/v1/audio/speech", json={"text": "Test", "voice": "minh_duc"})
    assert res.status_code == 401
    assert "Client API key required" in res.json()["detail"]


def test_create_client_api_key_and_use_in_speech(monkeypatch):
    m_key = settings_manager.get_settings().security.management_key

    # 1. Create new client key
    res = client.post(
        "/v1/management/keys",
        headers={"X-Management-Key": m_key},
        json={"name": "Test Key", "daily_char_limit": 5000}
    )
    assert res.status_code == 201
    data = res.json()
    new_key = data["key"]
    assert new_key.startswith("vc_live_")

    # 2. Mock model dispatcher synthesis with async function
    from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
    async def mock_synth(**kwargs):
        return (b"RIFFmockwavdata", 0.05, "worker-mock")

    monkeypatch.setattr(model_dispatcher, "synthesize", mock_synth)

    # 3. Use client key in speech API
    speech_res = client.post(
        "/v1/audio/speech",
        headers={"X-API-Key": new_key},
        json={"text": "Xin chao buoi sang", "voice": "minh_duc"}
    )
    assert speech_res.status_code == 200
    assert speech_res.content == b"RIFFmockwavdata"


def test_hot_reload_settings_persists():
    m_key = settings_manager.get_settings().security.management_key
    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": m_key},
        json={"concurrency": {"max_concurrent_tts": 5, "max_queue_waiters": 12}}
    )
    assert res.status_code == 200
    assert settings_manager.get_settings().concurrency.max_concurrent_tts == 5
    assert settings_manager.get_settings().concurrency.max_queue_waiters == 12
