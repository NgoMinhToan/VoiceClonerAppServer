import asyncio
import io
import uuid
import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.core.database import init_db

client = TestClient(app, headers={"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"})


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def test_auth_full_lifecycle():
    unique_suffix = uuid.uuid4().hex[:8]
    email = f"user_{unique_suffix}@example.com"
    password = "secret_password_123"
    full_name = "Nguyễn Văn Test"

    # 1. Register
    reg_res = client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert reg_res.status_code == 200
    data = reg_res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    access_token = data["access_token"]
    refresh_token = data["refresh_token"]

    # 2. Duplicate email rejected
    dup_res = client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert dup_res.status_code == 400

    # 3. Login
    login_res = client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 200
    assert "access_token" in login_res.json()

    # 4. Get Profile (/me)
    me_res = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_res.status_code == 200
    profile = me_res.json()
    assert profile["email"] == email
    assert profile["full_name"] == full_name
    assert profile["max_cloned_slots"] == 3
    assert profile["cloned_voices_count"] == 0

    # 5. Refresh token
    ref_res = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert ref_res.status_code == 200
    assert "access_token" in ref_res.json()

    # 6. Logout
    logout_res = client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"refresh_token": refresh_token},
    )
    assert logout_res.status_code == 200


def test_voices_list_and_clone_flow(monkeypatch):
    from VoiceClonerAppServer.services import model_dispatcher

    # Mock model_dispatcher.extract_embedding and synthesize
    async def mock_extract(audio_bytes, filename="sample.wav"):
        buf = io.BytesIO()
        import numpy as np
        np.savez(buf, speaker_emb=np.ones(192, dtype=np.float32), codes=np.zeros((1, 256), dtype=np.int64))
        return buf.getvalue()

    async def mock_synth(**kwargs):
        return b"RIFFfakeWAVaudio48k", 0.05, "mock-worker"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "extract_embedding", mock_extract)
    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)

    unique_suffix = uuid.uuid4().hex[:8]
    email = f"cloner_{unique_suffix}@example.com"
    password = "secret_password_123"

    reg_res = client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Test Cloner"},
    )
    access_token = reg_res.json()["access_token"]

    # 1. List voices as guest
    guest_res = client.get("/v1/voices")
    assert guest_res.status_code == 200
    guest_data = guest_res.json()
    assert guest_data["count"] >= 23
    assert guest_data["cloned_count"] == 0

    # 2. Clone a voice
    dummy_wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    files = {"audio_file": ("my_sample.wav", dummy_wav, "audio/wav")}
    data = {
        "name": "Giọng Thử Nghiệm",
        "gender": "male",
        "region": "bac",
        "category": "Thuyết minh",
        "denoise": "true",
    }
    clone_res = client.post(
        "/v1/voices/clone",
        headers={"Authorization": f"Bearer {access_token}"},
        files=files,
        data=data,
    )
    assert clone_res.status_code == 200
    cloned_voice = clone_res.json()["voice"]
    cloned_id = cloned_voice["id"]
    assert cloned_voice["name"] == "Giọng Thử Nghiệm"

    # 3. List voices as authenticated user (should contain the cloned voice)
    user_voices_res = client.get(
        "/v1/voices",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert user_voices_res.status_code == 200
    user_voices_data = user_voices_res.json()
    assert user_voices_data["cloned_count"] == 1
    assert any(v["id"] == cloned_id for v in user_voices_data["data"])

    # 4. Synthesize speech using the cloned voice
    synth_res = client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"text": "Xin chào từ giọng clone", "voice": cloned_id},
    )
    assert synth_res.status_code == 200
    assert synth_res.content == b"RIFFfakeWAVaudio48k"

    # 5. Delete cloned voice
    del_res = client.delete(
        f"/v1/voices/{cloned_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert del_res.status_code == 200


def test_system_status_android_contract():
    res = client.get("/v1/system/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "hardware" in data
    assert "benchmarks" in data
    assert data["sample_rate"] == 48000
