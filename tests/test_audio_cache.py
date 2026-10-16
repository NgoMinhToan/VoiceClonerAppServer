import asyncio
import io
import time
import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.core.database import init_db
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.services.cache_service import cache_service

client = TestClient(app, headers={"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"})


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def test_audio_cache_hit_and_miss_flow(monkeypatch):
    from VoiceClonerAppServer.services import model_dispatcher

    synth_call_count = 0

    async def mock_synth(**kwargs):
        nonlocal synth_call_count
        synth_call_count += 1
        # Simulate worker processing time
        await asyncio.sleep(0.02)
        wav_payload = b"RIFF" + (b"\x00" * 40) + b"fakeWAVaudio48kDataStreamStandard1234567890" * 2
        return wav_payload, 0.02, "worker-mock-1"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)

    # 1. Register test user
    email = f"cache_tester_{int(time.time() * 1000)}@example.com"
    reg = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "password123", "full_name": "Cache Tester"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    unique_phrase = f"Cụm từ thử nghiệm audio cache {time.time()}"

    # 2. First call -> Cache MISS
    res1 = client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"text": unique_phrase, "voice": "minh_duc"}
    )
    assert res1.status_code == 200
    assert res1.headers.get("x-cache-status") == "MISS" or res1.headers.get("X-Cache-Status") == "MISS"
    assert synth_call_count == 1

    # Check cache file exists in disk
    cache_key = cache_service.get_cache_key("minh_duc", unique_phrase, smart_pause=True)
    cached_path = cache_service.get_cached_file(cache_key)
    assert cached_path is not None
    assert cached_path.exists()

    # Check user quota deducted for first call
    me1 = client.get("/v1/auth/me", headers=headers).json()
    chars_used_first = me1["daily_tts_chars_used"]
    assert chars_used_first == len(unique_phrase)

    # 3. Second call with IDENTICAL phrase and voice -> Cache HIT
    t_start = time.perf_counter()
    res2 = client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"text": unique_phrase, "voice": "minh_duc"}
    )
    elapsed_ms = (time.perf_counter() - t_start) * 1000.0

    assert res2.status_code == 200
    assert res2.headers.get("x-cache-status") == "HIT" or res2.headers.get("X-Cache-Status") == "HIT"
    # Crucial: worker synthesize should NOT have been called again!
    assert synth_call_count == 1
    # Instant response < 100ms
    assert elapsed_ms < 100.0

    # User quota should NOT have been deducted a second time!
    me2 = client.get("/v1/auth/me", headers=headers).json()
    assert me2["daily_tts_chars_used"] == chars_used_first


def test_preview_audio_cache_flow(monkeypatch):
    from VoiceClonerAppServer.services import model_dispatcher

    preview_call_count = 0

    async def mock_synth(**kwargs):
        nonlocal preview_call_count
        preview_call_count += 1
        return b"RIFF" + (b"\x00" * 40) + b"previewWAVdata48kStandardAudioClip" * 2, 0.01, "worker-mock-preview"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)

    # Register test user for preview auth
    email = f"prev_tester_{int(time.time() * 1000)}@example.com"
    reg = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "password123", "full_name": "Preview Tester"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    test_text = f"Bản nghe thử riêng biệt {time.time()}"

    # First preview call -> MISS
    res1 = client.post("/v1/voices/preview", headers=headers, json={"voice_id": "pham_tuyen", "text": test_text})
    assert res1.status_code == 200
    assert res1.headers.get("x-cache-status") == "MISS" or res1.headers.get("X-Cache-Status") == "MISS"
    assert preview_call_count == 1

    # Second preview call -> HIT
    res2 = client.post("/v1/voices/preview", headers=headers, json={"voice_id": "pham_tuyen", "text": test_text})
    assert res2.status_code == 200
    assert res2.headers.get("x-cache-status") == "HIT" or res2.headers.get("X-Cache-Status") == "HIT"
    assert preview_call_count == 1
