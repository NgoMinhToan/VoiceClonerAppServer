import asyncio
import io
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.core.database import init_db
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.services import model_dispatcher

client = TestClient(app, headers={"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"})


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def test_register_invalid_email_and_short_password():
    # 1. Invalid email
    res1 = client.post(
        "/v1/auth/register",
        json={"email": "not_an_email", "password": "password123", "full_name": "Test"}
    )
    assert res1.status_code == 422

    # 2. Short password (< 6 chars)
    res2 = client.post(
        "/v1/auth/register",
        json={"email": "valid@example.com", "password": "123", "full_name": "Test"}
    )
    assert res2.status_code == 422


def test_login_wrong_credentials():
    email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    client.post(
        "/v1/auth/register",
        json={"email": email, "password": "correct_pass_123", "full_name": "Test User"}
    )

    # Wrong password
    res_wrong_pass = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "wrong_password"}
    )
    assert res_wrong_pass.status_code == 401

    # Non-existent email
    res_wrong_email = client.post(
        "/v1/auth/login",
        json={"email": "non_existent@example.com", "password": "correct_pass_123"}
    )
    assert res_wrong_email.status_code == 401


def test_tampered_jwt_token_rejected():
    res = client.get(
        "/v1/auth/me",
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature"}
    )
    assert res.status_code == 401


def test_blacklisted_token_rejected_after_logout():
    email = f"logout_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "password123", "full_name": "Logout Tester"}
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Before logout -> works
    me1 = client.get("/v1/auth/me", headers=headers)
    assert me1.status_code == 200

    # Logout
    logout = client.post("/v1/auth/logout", headers=headers)
    assert logout.status_code == 200

    # After logout -> rejected
    me2 = client.get("/v1/auth/me", headers=headers)
    assert me2.status_code == 401
    assert "vô hiệu hóa" in me2.json()["detail"]


def test_idor_protection_between_users(monkeypatch):
    # Mock model_dispatcher.extract_embedding
    async def mock_extract(audio_bytes, filename="sample.wav"):
        buf = io.BytesIO()
        import numpy as np
        np.savez(buf, speaker_emb=np.ones(192, dtype=np.float32), codes=np.zeros((1, 256), dtype=np.int64))
        return buf.getvalue()

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "extract_embedding", mock_extract)

    # 1. Register User A
    userA_res = client.post(
        "/v1/auth/register",
        json={"email": f"usera_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "User A"}
    )
    tokenA = userA_res.json()["access_token"]

    # 2. Register User B
    userB_res = client.post(
        "/v1/auth/register",
        json={"email": f"userb_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "User B"}
    )
    tokenB = userB_res.json()["access_token"]

    # 3. User A clones a voice
    dummy_wav = b"RIFF" + (b"\x00" * 40) + b"voiceSampleData" * 4
    files = {"audio_file": ("sampleA.wav", dummy_wav, "audio/wav")}
    clone_res = client.post(
        "/v1/voices/clone",
        headers={"Authorization": f"Bearer {tokenA}"},
        files=files,
        data={"name": "Giọng User A"}
    )
    assert clone_res.status_code == 200
    voice_id_A = clone_res.json()["voice"]["id"]

    # 4. User B tries to access User A's cloned voice -> ALL MUST RETURN 404 (IDOR Defense)
    headersB = {"Authorization": f"Bearer {tokenB}"}

    # GET detail
    res_get = client.get(f"/v1/voices/{voice_id_A}", headers=headersB)
    assert res_get.status_code == 404

    # GET audio
    res_audio = client.get(f"/v1/voices/{voice_id_A}/audio", headers=headersB)
    assert res_audio.status_code == 404

    # PUT update
    res_put = client.put(f"/v1/voices/{voice_id_A}", headers=headersB, json={"name": "Hacked Name"})
    assert res_put.status_code == 404

    # DELETE
    res_del = client.delete(f"/v1/voices/{voice_id_A}", headers=headersB)
    assert res_del.status_code == 404

    # POST speech with User A's cloned voice
    res_speech = client.post(
        "/v1/audio/speech",
        headers=headersB,
        json={"text": "User B cố tình dùng giọng User A", "voice": voice_id_A}
    )
    assert res_speech.status_code == 404


def test_modify_or_delete_builtin_voice_rejected():
    user_res = client.post(
        "/v1/auth/register",
        json={"email": f"admin_probe_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Admin Probe"}
    )
    token = user_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Attempting to edit built-in voice minh_duc
    res_put = client.put("/v1/voices/minh_duc", headers=headers, json={"name": "Hacked Builtin"})
    assert res_put.status_code == 400
    assert "Không thể chỉnh sửa" in res_put.json()["detail"]

    # Attempting to delete built-in voice minh_duc
    res_del = client.delete("/v1/voices/minh_duc", headers=headers)
    assert res_del.status_code == 400
    assert "Không thể xóa" in res_del.json()["detail"]


def test_cloned_slots_quota_exhaustion_and_release(monkeypatch):
    async def mock_extract(audio_bytes, filename="sample.wav"):
        buf = io.BytesIO()
        import numpy as np
        np.savez(buf, speaker_emb=np.ones(192, dtype=np.float32), codes=np.zeros((1, 256), dtype=np.int64))
        return buf.getvalue()

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "extract_embedding", mock_extract)

    user_res = client.post(
        "/v1/auth/register",
        json={"email": f"slots_tester_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Slots Tester"}
    )
    token = user_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    dummy_wav = b"RIFF" + (b"\x00" * 40) + b"voiceData" * 4

    created_ids = []
    # Clone 3 voices (reaching default limit 3/3)
    for i in range(3):
        res = client.post(
            "/v1/voices/clone",
            headers=headers,
            files={"audio_file": (f"sample{i}.wav", dummy_wav, "audio/wav")},
            data={"name": f"Clone {i}"}
        )
        assert res.status_code == 200
        created_ids.append(res.json()["voice"]["id"])

    # 4th clone attempt MUST be rejected with 403 Forbidden
    res_4 = client.post(
        "/v1/voices/clone",
        headers=headers,
        files={"audio_file": ("sample4.wav", dummy_wav, "audio/wav")},
        data={"name": "Clone 4 (Over quota)"}
    )
    assert res_4.status_code == 403
    assert "hết 3/3 slot" in res_4.json()["detail"]

    # Delete 1 voice -> frees up slot
    del_res = client.delete(f"/v1/voices/{created_ids[0]}", headers=headers)
    assert del_res.status_code == 200

    # 4th clone attempt should now SUCCEED
    res_retry = client.post(
        "/v1/voices/clone",
        headers=headers,
        files={"audio_file": ("sample4.wav", dummy_wav, "audio/wav")},
        data={"name": "Clone 4 (Retried)"}
    )
    assert res_retry.status_code == 200


def test_quota_rollback_on_worker_synthesis_failure(monkeypatch):
    # Simulate worker synthesis failure
    async def mock_failing_synth(**kwargs):
        raise RuntimeError("Worker node crashed or timed out")

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_failing_synth)

    user_res = client.post(
        "/v1/auth/register",
        json={"email": f"rollback_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Rollback Tester"}
    )
    token = user_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Initial usage
    me_before = client.get("/v1/auth/me", headers=headers).json()
    used_before = me_before["daily_tts_chars_used"]

    # Call speech synthesis with failing worker
    test_phrase = "Đây là đoạn văn bản kiểm tra rollback hạn mức ký tự khi gặp lỗi."
    speech_res = client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"text": test_phrase, "voice": "minh_duc"}
    )
    assert speech_res.status_code == 503

    # Check that quota was REFUNDED (rolled back)
    me_after = client.get("/v1/auth/me", headers=headers).json()
    assert me_after["daily_tts_chars_used"] == used_before


def test_clone_voice_empty_file_rejected():
    user_res = client.post(
        "/v1/auth/register",
        json={"email": f"empty_upload_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Empty Upload"}
    )
    token = user_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post(
        "/v1/voices/clone",
        headers=headers,
        files={"audio_file": ("empty.wav", b"", "audio/wav")},
        data={"name": "Empty Clip"}
    )
    assert res.status_code == 400
    assert "trống" in res.json()["detail"].lower()


def test_management_unauthorized_access():
    # Without header
    res1 = client.get("/v1/management/settings")
    assert res1.status_code == 401

    # With wrong key
    res2 = client.get("/v1/management/settings", headers={"X-Management-Key": "wrong_key_123"})
    assert res2.status_code == 401

    # Delete non-existent user
    m_key = settings_manager.get_settings().security.management_key
    res3 = client.delete("/v1/management/users/non_existent_user_id_xyz", headers={"X-Management-Key": m_key})
    assert res3.status_code == 404


def test_preview_exceeds_max_char_limit():
    user_res = client.post(
        "/v1/auth/register",
        json={"email": f"limit_probe_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Limit Probe"}
    )
    token = user_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Default max_preview_char_limit is 250 chars. Text with 251 chars should be rejected with 400.
    long_preview_text = "A" * 251
    res = client.post(
        "/v1/voices/preview",
        headers=headers,
        json={"voice_id": "minh_duc", "text": long_preview_text}
    )
    assert res.status_code == 400
    assert "vượt quá giới hạn tối đa" in res.json()["detail"]


def test_preview_insufficient_remaining_quota(monkeypatch):
    from VoiceClonerAppServer.services import model_dispatcher

    async def mock_synth(**kwargs):
        return b"RIFF" + (b"\x00" * 40) + b"audioClipWavData48k" * 2, 0.01, "worker-mock"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)

    # Temporarily set default daily_tts_char_limit to 1000 chars via management settings
    m_key = settings_manager.get_settings().security.management_key
    client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": m_key},
        json={"default_user_limits": {"daily_tts_char_limit": 1000}}
    )

    try:
        user_res = client.post(
            "/v1/auth/register",
            json={"email": f"quota_probe_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Quota Probe"}
        )
        token = user_res.json()["access_token"]
        user_id = user_res.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}"}

        # Pre-fill user usage with unique text to guarantee Cache MISS (e.g. 950 chars out of 1000 limit)
        fill_prefix = f"FILL_{uuid.uuid4().hex}_{time.time()}_"
        speech_text = fill_prefix + ("Z" * (950 - len(fill_prefix)))
        fill_len = len(speech_text)

        speech_fill = client.post(
            "/v1/audio/speech",
            headers=headers,
            json={"voice": "minh_duc", "text": speech_text}
        )
        assert speech_fill.status_code == 200
        assert speech_fill.headers.get("x-cache-status") == "MISS" or speech_fill.headers.get("X-Cache-Status") == "MISS"

        # Verify remaining chars is 1000 - fill_len = 50
        me = client.get("/v1/auth/me", headers=headers).json()
        assert me["daily_tts_chars_used"] == fill_len
        remaining = 1000 - fill_len

        # Preview request with (remaining + 15) chars -> Insufficient! (MUST reject with 403)
        preview_prefix = f"PREV_{uuid.uuid4().hex}_"
        phrase2 = preview_prefix + ("B" * (remaining + 15 - len(preview_prefix)))
        res2 = client.post(
            "/v1/voices/preview",
            headers=headers,
            json={"voice_id": "minh_duc", "text": phrase2}
        )
        assert res2.status_code == 403
        assert "không đủ" in res2.json()["detail"]
        assert f"{remaining} ký tự còn lại" in res2.json()["detail"]
    finally:
        # Restore default daily limit
        client.put(
            "/v1/management/settings",
            headers={"X-Management-Key": m_key},
            json={"default_user_limits": {"daily_tts_char_limit": 50000}}
        )


def test_preview_quota_rollback_on_failure(monkeypatch):
    from VoiceClonerAppServer.services import model_dispatcher

    async def mock_failing_synth(**kwargs):
        raise RuntimeError("Worker node crashed during preview")

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_failing_synth)

    user_res = client.post(
        "/v1/auth/register",
        json={"email": f"prev_rollback_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Preview Rollback"}
    )
    token = user_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me_before = client.get("/v1/auth/me", headers=headers).json()
    used_before = me_before["daily_tts_chars_used"]

    unique_text = f"Kiểm tra rollback preview {time.time()}"
    res = client.post(
        "/v1/voices/preview",
        headers=headers,
        json={"voice_id": "minh_duc", "text": unique_text}
    )
    assert res.status_code == 503

    me_after = client.get("/v1/auth/me", headers=headers).json()
    assert me_after["daily_tts_chars_used"] == used_before


def test_speech_exceeds_max_char_limit():
    user_res = client.post(
        "/v1/auth/register",
        json={"email": f"speech_limit_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Speech Limit Probe"}
    )
    token = user_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Default max_speech_char_limit is 2000 chars. Text with 2001 chars should be rejected with 400.
    long_speech_text = "A" * 2001
    res = client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"voice": "minh_duc", "text": long_speech_text}
    )
    assert res.status_code == 400
    assert "vượt quá giới hạn tối đa" in res.json()["detail"]


def test_speech_insufficient_remaining_quota(monkeypatch):
    from VoiceClonerAppServer.services import model_dispatcher

    async def mock_synth(**kwargs):
        return b"RIFF" + (b"\x00" * 40) + b"speechDataStreamWav" * 2, 0.01, "worker-mock"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)

    m_key = settings_manager.get_settings().security.management_key
    client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": m_key},
        json={"default_user_limits": {"daily_tts_char_limit": 1000}}
    )

    try:
        user_res = client.post(
            "/v1/auth/register",
            json={"email": f"speech_quota_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "Speech Quota Probe"}
        )
        token = user_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Pre-fill user usage with 960 chars (1000 - 960 = 40 remaining)
        fill_prefix = f"FILL_SP_{uuid.uuid4().hex}_{time.time()}_"
        speech_text = fill_prefix + ("K" * (960 - len(fill_prefix)))
        fill_len = len(speech_text)

        speech_fill = client.post(
            "/v1/audio/speech",
            headers=headers,
            json={"voice": "minh_duc", "text": speech_text}
        )
        assert speech_fill.status_code == 200
        remaining = 1000 - fill_len

        # Now send speech request with (remaining + 10) chars -> Insufficient! (MUST reject with 403)
        req_prefix = f"REQ_SP_{uuid.uuid4().hex}_"
        excess_text = req_prefix + ("M" * (remaining + 10 - len(req_prefix)))
        res = client.post(
            "/v1/audio/speech",
            headers=headers,
            json={"voice": "minh_duc", "text": excess_text}
        )
        assert res.status_code == 403
        assert "không đủ" in res.json()["detail"]
        assert f"{remaining} ký tự còn lại" in res.json()["detail"]
    finally:
        client.put(
            "/v1/management/settings",
            headers={"X-Management-Key": m_key},
            json={"default_user_limits": {"daily_tts_char_limit": 50000}}
        )

