import asyncio
import os
import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.core.settings_manager import settings_manager
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


def test_speech_api_rejects_without_user_bearer_token():
    valid_key = "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
    res = client.post(
        "/v1/audio/speech",
        headers={"X-API-Key": valid_key},
        json={"text": "Test", "voice": "minh_duc"}
    )
    assert res.status_code == 401
    assert "Yêu cầu xác thực" in res.json()["detail"]


def test_create_client_api_key_and_use_in_speech(monkeypatch):
    import time
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

    # 2. Register user using new client key
    email = f"tier_user_{int(time.time() * 1000)}@example.com"
    reg = client.post(
        "/v1/auth/register",
        headers={"X-API-Key": new_key},
        json={"email": email, "password": "password123", "full_name": "Tier User"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]

    # 3. Mock model dispatcher synthesis with async function
    from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
    async def mock_synth(**kwargs):
        return (b"RIFFmockwavdata", 0.05, "worker-mock")

    monkeypatch.setattr(model_dispatcher, "synthesize", mock_synth)

    # 4. Use client key AND user token in speech API
    speech_res = client.post(
        "/v1/audio/speech",
        headers={
            "X-API-Key": new_key,
            "Authorization": f"Bearer {token}",
        },
        json={"text": "Xin chao buoi sang", "voice": "minh_duc"}
    )
    assert speech_res.status_code == 200
    assert speech_res.content == b"RIFFmockwavdata"

    # 5. Clean up created test key
    client.delete(f"/v1/management/keys/{data['id']}", headers={"X-Management-Key": m_key})


def test_hot_reload_settings_persists():
    m_key = settings_manager.get_settings().security.management_key
    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": m_key},
        json={"concurrency": {"max_concurrent_tts": 5, "max_queue_waiters": 12, "max_preview_char_limit": 300, "max_speech_char_limit": 2500}}
    )
    assert res.status_code == 200
    assert settings_manager.get_settings().concurrency.max_concurrent_tts == 5
    assert settings_manager.get_settings().concurrency.max_queue_waiters == 12
    assert settings_manager.get_settings().concurrency.max_preview_char_limit == 300
    assert settings_manager.get_settings().concurrency.max_speech_char_limit == 2500

    # Restore concurrency defaults
    client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": m_key},
        json={"concurrency": {"max_concurrent_tts": 2, "max_queue_waiters": 8, "max_preview_char_limit": 250, "max_speech_char_limit": 2000}}
    )


def test_default_user_limits_settings_and_hot_reload():
    m_key = settings_manager.get_settings().security.management_key
    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": m_key},
        json={
            "default_user_limits": {
                "max_cloned_slots": 5,
                "daily_tts_char_limit": 60000,
                "rate_limit_requests_per_minute": 90,
                "max_speech_char_limit": 3000,
                "max_preview_char_limit": 300,
                "max_concurrent_tts": 2,
                "max_queue_waiters": 3,
            }
        }
    )
    assert res.status_code == 200
    cfg = settings_manager.get_settings().default_user_limits
    assert cfg.max_cloned_slots == 5
    assert cfg.daily_tts_char_limit == 60000
    assert cfg.rate_limit_requests_per_minute == 90
    assert cfg.max_speech_char_limit == 3000
    assert cfg.max_preview_char_limit == 300
    assert cfg.max_concurrent_tts == 2
    assert cfg.max_queue_waiters == 3

    # Restore defaults
    client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": m_key},
        json={
            "default_user_limits": {
                "max_cloned_slots": 3,
                "daily_tts_char_limit": 50000,
                "rate_limit_requests_per_minute": 60,
                "max_speech_char_limit": 2000,
                "max_preview_char_limit": 250,
                "max_concurrent_tts": 1,
                "max_queue_waiters": 1,
            }
        }
    )


def test_speech_rejects_invalid_client_api_key_even_if_authenticated(monkeypatch):
    import time
    from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher

    async def mock_synth(**kwargs):
        return (b"RIFFmockwavdata", 0.05, "worker-mock")

    monkeypatch.setattr(model_dispatcher, "synthesize", mock_synth)

    valid_key = "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

    # Register user to get Bearer token using valid client key
    email = f"authed_client_{int(time.time() * 1000)}@example.com"
    reg = client.post(
        "/v1/auth/register",
        headers={"X-API-Key": valid_key},
        json={"email": email, "password": "password123", "full_name": "Auth User"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]

    # Call with valid Bearer token BUT invalid/modified X-API-Key (e.g. key + "_1")
    res = client.post(
        "/v1/audio/speech",
        headers={
            "Authorization": f"Bearer {token}",
            "X-API-Key": "vc_live_invalid_key_that_does_not_exist_in_settings",
        },
        json={"text": "Test speech", "voice": "minh_duc"}
    )
    assert res.status_code == 401
    assert "Invalid or deactivated client API key" in res.json()["detail"]


def test_speech_rejects_when_client_api_key_deleted_even_if_authenticated(monkeypatch):
    import time
    from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher

    async def mock_synth(**kwargs):
        return (b"RIFFmockwavdata", 0.05, "worker-mock")

    monkeypatch.setattr(model_dispatcher, "synthesize", mock_synth)

    valid_key = "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

    # Register user with valid client key
    email = f"del_key_user_{int(time.time() * 1000)}@example.com"
    reg = client.post(
        "/v1/auth/register",
        headers={"X-API-Key": valid_key},
        json={"email": email, "password": "password123", "full_name": "Del Key User"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]

    # 1. User is authenticated, but DELETES/CLEARS X-API-Key (empty string)
    res_empty = client.post(
        "/v1/audio/speech",
        headers={
            "Authorization": f"Bearer {token}",
            "X-API-Key": "",
        },
        json={"text": "Test speech", "voice": "minh_duc"}
    )
    assert res_empty.status_code == 401
    assert "Client API key required" in res_empty.json()["detail"]

    # 2. User is authenticated, but completely OMITS X-API-Key header
    res_omitted = client.post(
        "/v1/audio/speech",
        headers={
            "Authorization": f"Bearer {token}",
        },
        json={"text": "Test speech", "voice": "minh_duc"}
    )
    assert res_omitted.status_code == 401
    assert "Client API key required" in res_omitted.json()["detail"]


def test_v1_endpoints_reject_when_client_key_missing_or_invalid():
    valid_key = "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

    # 1. Calling public endpoint with invalid X-API-Key -> 401
    res_invalid = client.get(
        "/v1/voices",
        headers={"X-API-Key": "vc_live_invalid_key_12345"}
    )
    assert res_invalid.status_code == 401
    assert "Invalid or deactivated client API key" in res_invalid.json()["detail"]

    # 2. Calling endpoint with missing X-API-Key -> 401
    res_missing = client.get("/v1/voices")
    assert res_missing.status_code == 401
    assert "Client API key required" in res_missing.json()["detail"]

    # 3. Calling with valid X-API-Key -> 200
    res_valid = client.get(
        "/v1/voices",
        headers={"X-API-Key": valid_key}
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["count"] >= 23


def test_settings_hot_reloads_on_disk_modification(tmp_path, monkeypatch):
    import json
    from VoiceClonerAppServer.core.settings_manager import SettingsManager

    cfg_file = tmp_path / "settings.json"
    initial_data = {
        "server": {"host": "0.0.0.0", "port": 8000},
        "security": {"management_key": "vc_admin_test", "require_client_api_key": True},
        "client_api_keys": [
            {
                "id": "key_app",
                "name": "App",
                "key": "vc_live_original_key",
                "is_active": True,
                "daily_char_limit": 50000,
            }
        ],
        "model_workers": [],
        "concurrency": {},
        "audio_cache": {}
    }
    cfg_file.write_text(json.dumps(initial_data), encoding="utf-8")

    sm = SettingsManager(settings_path=cfg_file)
    assert sm.find_client_key("vc_live_original_key") is not None
    assert sm.find_client_key("vc_live_original_key_1") is None

    # Simulate user editing the file on disk (adding '_1')
    initial_data["client_api_keys"][0]["key"] = "vc_live_original_key_1"
    # Sleep slightly to ensure mtime changes
    import time
    time.sleep(0.05)
    cfg_file.write_text(json.dumps(initial_data), encoding="utf-8")

    # Without restarting, sm must detect the disk modification and find the new key
    assert sm.find_client_key("vc_live_original_key_1") is not None
    assert sm.find_client_key("vc_live_original_key") is None


def test_exempt_endpoints_accessible_without_client_key():
    # 1. /health probe is open to container healthchecks
    res_health = client.get("/health")
    assert res_health.status_code == 200

    # 2. /dashboard is open to web browser
    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200

    # 3. /v1/management/* requires X-Management-Key, NOT X-API-Key
    m_key = settings_manager.get_settings().security.management_key
    res_mgmt = client.get(
        "/v1/management/settings",
        headers={"X-Management-Key": m_key}
    )
    assert res_mgmt.status_code == 200

