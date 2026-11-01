import asyncio
import io
import time
import uuid
import httpx
import numpy as np
import pytest

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.core.database import AsyncSessionLocal, init_db
from VoiceClonerAppServer.core.redis_client import get_daily_usage
from VoiceClonerAppServer.models.db_models import User
from VoiceClonerAppServer.services import model_dispatcher
from VoiceClonerAppServer.services.cache_service import cache_service
from VoiceClonerAppServer.services.inflight_manager import inflight_manager

API_KEY_HEADER = {"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}


@pytest.fixture(autouse=True)
def setup_test_env():
    asyncio.run(init_db())
    inflight_manager._tasks.clear()


@pytest.mark.asyncio
async def test_inflight_deduplication_single_worker_dispatch(monkeypatch):
    """
    Test 1: Two concurrent requests with the same text and voice.
    Verify upstream worker synthesize is invoked exactly ONCE, both callers receive 200,
    the initiator receives X-Cache-Status: MISS, and the waiter receives X-Cache-Status: HIT.
    """
    synth_call_count = 0

    async def mock_synth(**kwargs):
        nonlocal synth_call_count
        synth_call_count += 1
        # Artificial delay so waiter arrives while creator job is in-flight
        await asyncio.sleep(0.15)
        wav_payload = b"RIFF" + (b"\x00" * 40) + b"fakeWAVaudio48kDataStreamStandard1234567890" * 2
        return wav_payload, 0.15, "worker-mock-inflight"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        email = f"dedup_tester_{uuid.uuid4().hex[:8]}@example.com"
        reg = await ac.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": email, "password": "password123", "full_name": "Dedup Tester"},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        user_id = reg.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

        # Warm up rate limit check so Redis timeout (if offline) does not race coroutines
        from VoiceClonerAppServer.core.redis_client import check_rate_limit
        await check_rate_limit(user_id)

        unique_phrase = f"Cụm từ thử nghiệm in-flight singleflight {time.time()}"
        payload = {"text": unique_phrase, "voice": "minh_duc"}

        async def call_initiator():
            return await ac.post("/v1/audio/speech", headers=headers, json=payload)

        async def call_waiter():
            await asyncio.sleep(0.04)  # Ensure initiator has created in-flight task
            return await ac.post("/v1/audio/speech", headers=headers, json=payload)

        res_initiator, res_waiter = await asyncio.gather(call_initiator(), call_waiter())

        assert res_initiator.status_code == 200
        assert res_waiter.status_code == 200

        # Synthesize must be called exactly once
        assert synth_call_count == 1

        status_initiator = res_initiator.headers.get("x-cache-status")
        status_waiter = res_waiter.headers.get("x-cache-status")

        assert status_initiator == "MISS"
        assert status_waiter == "HIT"

        # Check that cache key was removed from in-flight manager
        cache_key = cache_service.get_cache_key("minh_duc", unique_phrase, smart_pause=True)
        assert inflight_manager.get(cache_key) is None

        # Check standard cache file exists on disk
        assert cache_service.get_cached_file(cache_key) is not None


@pytest.mark.asyncio
async def test_unbilled_cache_claim_on_retry_deducts_quota():
    """
    Test 2: Directly save unbilled_{cache_key}.wav. When user requests it,
    quota is deducted, file is promoted to {cache_key}.wav, and status is HIT-CLAIMED.
    On a second request, status is HIT without deducting quota again.
    """
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        email = f"unbilled_claim_{uuid.uuid4().hex[:8]}@example.com"
        reg = await ac.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": email, "password": "password123", "full_name": "Claim Tester"},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        user_id = reg.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

        unique_phrase = f"Unbilled cache claim test phrase {time.time()}"
        cache_key = cache_service.get_cache_key("minh_duc", unique_phrase, smart_pause=True)
        dummy_wav = b"RIFF" + (b"\x00" * 40) + b"validAudioDataPayload48kHz" * 2

        # Initial state: save directly as unbilled cache
        cache_service.save_unbilled_cache(cache_key, dummy_wav)
        assert cache_service.get_unbilled_cached_file(cache_key) is not None
        assert cache_service.get_cached_file(cache_key) is None

        used_before = await get_daily_usage(user_id)
        assert used_before == 0

        # First request -> HIT-CLAIMED (must deduct quota and promote file)
        res1 = await ac.post("/v1/audio/speech", headers=headers, json={"text": unique_phrase, "voice": "minh_duc"})
        assert res1.status_code == 200
        assert res1.headers.get("x-cache-status") == "HIT-CLAIMED"

        # Quota must be deducted for the phrase length
        used_after_claim = await get_daily_usage(user_id)
        assert used_after_claim == len(unique_phrase)

        # File must be promoted: unbilled is gone, standard cache exists
        assert cache_service.get_unbilled_cached_file(cache_key) is None
        assert cache_service.get_cached_file(cache_key) is not None

        # Second request -> HIT (must NOT deduct quota again)
        res2 = await ac.post("/v1/audio/speech", headers=headers, json={"text": unique_phrase, "voice": "minh_duc"})
        assert res2.status_code == 200
        assert res2.headers.get("x-cache-status") == "HIT"

        used_after_hit = await get_daily_usage(user_id)
        assert used_after_hit == used_after_claim


@pytest.mark.asyncio
async def test_unbilled_cache_insufficient_quota_leaves_file_intact():
    """
    Test 3: User with insufficient remaining quota hits unbilled cache.
    Expect HTTP 403; unbilled cache remains unpromoted.
    """
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        email = f"quota_fail_{uuid.uuid4().hex[:8]}@example.com"
        reg = await ac.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": email, "password": "password123", "full_name": "Quota Fail Tester"},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        user_id = reg.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

        unique_phrase = f"Insufficient quota unbilled test {time.time()}"
        cache_key = cache_service.get_cache_key("minh_duc", unique_phrase, smart_pause=True)
        dummy_wav = b"RIFF" + (b"\x00" * 40) + b"validAudioDataPayload48kHz" * 2

        cache_service.save_unbilled_cache(cache_key, dummy_wav)
        assert cache_service.get_unbilled_cached_file(cache_key) is not None
        assert cache_service.get_cached_file(cache_key) is None

        # Restrict user's daily quota to less than the phrase length
        async with AsyncSessionLocal() as session:
            u = await session.get(User, user_id)
            u.daily_tts_char_limit = 5
            await session.commit()

        # Request speech: 5 < len(unique_phrase) -> 403
        res = await ac.post("/v1/audio/speech", headers=headers, json={"text": unique_phrase, "voice": "minh_duc"})
        assert res.status_code == 403

        # Crucial: unbilled cache must remain INTACT and UNPROMOTED
        assert cache_service.get_unbilled_cached_file(cache_key) is not None
        assert cache_service.get_cached_file(cache_key) is None

        # User's quota must NOT have been deducted
        used = await get_daily_usage(user_id)
        assert used == 0


@pytest.mark.asyncio
async def test_cloned_voice_idor_rejected_before_cache_or_inflight(monkeypatch):
    """
    Test 4: User B requests User A's private cloned voice ID.
    Verify HTTP 404 is returned immediately without cache lookup, in-flight task creation, or quota deduction.
    """
    async def mock_extract(audio_bytes, filename="sample.wav"):
        buf = io.BytesIO()
        np.savez(buf, speaker_emb=np.ones(192, dtype=np.float32), codes=np.zeros((1, 256), dtype=np.int64))
        return buf.getvalue()

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "extract_embedding", mock_extract)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        # Register User A
        regA = await ac.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": f"usera_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "User A"},
        )
        tokenA = regA.json()["access_token"]
        headersA = {"Authorization": f"Bearer {tokenA}", **API_KEY_HEADER}

        # User A clones a voice
        dummy_wav = b"RIFF" + (b"\x00" * 40) + b"voiceSampleData" * 4
        files = {"audio_file": ("sampleA.wav", dummy_wav, "audio/wav")}
        clone_res = await ac.post(
            "/v1/voices/clone",
            headers=headersA,
            files=files,
            data={"name": "Giọng User A"},
        )
        assert clone_res.status_code == 200
        voice_id_A = clone_res.json()["voice"]["id"]

        # Register User B
        regB = await ac.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": f"userb_{uuid.uuid4().hex[:8]}@example.com", "password": "password123", "full_name": "User B"},
        )
        tokenB = regB.json()["access_token"]
        user_id_B = regB.json()["user"]["id"]
        headersB = {"Authorization": f"Bearer {tokenB}", **API_KEY_HEADER}

        unique_phrase = f"User B trying to access User A voice {time.time()}"
        cache_key = cache_service.get_cache_key(voice_id_A, unique_phrase, smart_pause=True)

        # Prepopulate an unbilled or standard cache to ensure IDOR blocks before cache is touched
        cache_service.save_unbilled_cache(cache_key, dummy_wav)

        # User B requests speech with voice_id_A -> MUST be 404
        res = await ac.post(
            "/v1/audio/speech",
            headers=headersB,
            json={"text": unique_phrase, "voice": voice_id_A},
        )
        assert res.status_code == 404

        # Quota for User B must NOT be deducted
        used_B = await get_daily_usage(user_id_B)
        assert used_B == 0

        # No in-flight task created
        assert inflight_manager.get(cache_key) is None

        # Unbilled cache remains intact (not claimed or promoted by unauthorized user)
        assert cache_service.get_unbilled_cached_file(cache_key) is not None
        assert cache_service.get_cached_file(cache_key) is None


@pytest.mark.asyncio
async def test_worker_failure_rolls_back_quota_and_clears_inflight(monkeypatch):
    """
    Test 5: Upstream worker raises an exception.
    Verify initiator quota is refunded, in-flight registry is cleared, and no cache file exists.
    """
    async def mock_failing_synth(**kwargs):
        raise RuntimeError("Worker node exploded")

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_failing_synth)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        email = f"fail_tester_{uuid.uuid4().hex[:8]}@example.com"
        reg = await ac.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": email, "password": "password123", "full_name": "Fail Tester"},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        user_id = reg.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

        unique_phrase = f"Worker failure test phrase {time.time()}"
        cache_key = cache_service.get_cache_key("minh_duc", unique_phrase, smart_pause=True)

        res = await ac.post(
            "/v1/audio/speech",
            headers=headers,
            json={"text": unique_phrase, "voice": "minh_duc"},
        )
        assert res.status_code == 503

        # Quota must be refunded (0 used)
        used = await get_daily_usage(user_id)
        assert used == 0

        # In-flight registry must be cleared
        assert inflight_manager.get(cache_key) is None

        # Neither standard nor unbilled cache should exist
        assert cache_service.get_cached_file(cache_key) is None
        assert cache_service.get_unbilled_cached_file(cache_key) is None


@pytest.mark.asyncio
async def test_client_timeout_disconnect_worker_persists_unbilled_and_retry_claims(monkeypatch):
    """
    Test 6: Client experiences a timeout / disconnects while worker synthesis is underway.
    Verify:
    1. Upstream worker continues running in background via asyncio.shield + anyio.CancelScope.
    2. Disconnected client's quota is rolled back.
    3. Audio is saved to storage/audio_cache as unbilled_{cache_key}.wav.
    4. When user retries, unbilled cache is claimed, quota is deducted, file is promoted to {cache_key}.wav,
       and audio is served with HIT-CLAIMED.
    5. A subsequent retry receives the audio as standard cache HIT without deducting quota.
    6. Crucially: Upstream worker synthesize was called EXACTLY ONCE across the entire lifecycle.
    """
    synth_call_count = 0
    worker_finished = asyncio.Event()

    async def mock_slow_synth(**kwargs):
        nonlocal synth_call_count
        synth_call_count += 1
        # Simulate worker taking longer than client timeout
        await asyncio.sleep(0.3)
        worker_finished.set()
        wav_payload = b"RIFF" + (b"\x00" * 40) + b"longSynthesizedAudioDataPayload48kHz" * 2
        return wav_payload, 0.3, "worker-primary-long"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_slow_synth)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        email = f"timeout_tester_{uuid.uuid4().hex[:8]}@example.com"
        reg = await ac.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": email, "password": "password123", "full_name": "Timeout Tester"},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        user_id = reg.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

        unique_phrase = f"Câu văn dài thử nghiệm client timeout và retry unbilled {time.time()}"
        cache_key = cache_service.get_cache_key("minh_duc", unique_phrase, smart_pause=True)

        # 1. Start initial synthesis request in background task
        req_task = asyncio.create_task(
            ac.post("/v1/audio/speech", headers=headers, json={"text": unique_phrase, "voice": "minh_duc"})
        )

        # Allow request to reach the gateway and register in inflight_manager
        await asyncio.sleep(0.08)
        assert inflight_manager.get(cache_key) is not None

        # 2. Simulate client timeout / disconnection
        req_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await req_task

        # Quota must be rolled back immediately for disconnected client
        used_after_disconnect = await get_daily_usage(user_id)
        assert used_after_disconnect == 0

        # 3. Wait for the background worker to complete
        await asyncio.wait_for(worker_finished.wait(), timeout=2.0)
        # Small grace period for unbilled cache file write
        await asyncio.sleep(0.05)

        # 4. Verify unbilled cache file exists and standard cache does not yet exist
        unbilled_file = cache_service.get_unbilled_cached_file(cache_key)
        assert unbilled_file is not None
        assert unbilled_file.exists()
        assert cache_service.get_cached_file(cache_key) is None

        # 5. User taps "Retry"
        res_retry = await ac.post(
            "/v1/audio/speech",
            headers=headers,
            json={"text": unique_phrase, "voice": "minh_duc"},
        )
        assert res_retry.status_code == 200
        assert res_retry.headers.get("x-cache-status") == "HIT-CLAIMED"

        # Quota must now be accurately deducted for the claimed audio
        used_after_claim = await get_daily_usage(user_id)
        assert used_after_claim == len(unique_phrase)

        # Unbilled file was promoted to standard cache
        assert cache_service.get_unbilled_cached_file(cache_key) is None
        assert cache_service.get_cached_file(cache_key) is not None

        # 6. Third request should be a standard cache HIT without deducting quota
        res_3rd = await ac.post(
            "/v1/audio/speech",
            headers=headers,
            json={"text": unique_phrase, "voice": "minh_duc"},
        )
        assert res_3rd.status_code == 200
        assert res_3rd.headers.get("x-cache-status") == "HIT"

        # Quota remains unchanged
        assert await get_daily_usage(user_id) == len(unique_phrase)

        # 7. CRUCIAL INVARIANT: Upstream worker was called EXACTLY ONCE!
        assert synth_call_count == 1


@pytest.mark.asyncio
async def test_client_timeout_header_triggers_unbilled_and_rollback(monkeypatch):
    """
    Test 7: Client passes X-Client-Timeout: 0.1s. Worker synthesis takes 0.25s.
    Even without explicit client socket cancellation, Gateway detects duration_sec >= client_timeout_sec,
    rolls back creator quota, saves unbilled cache, and returns 499.
    Subsequent retry claims unbilled cache with HIT-CLAIMED and deducts quota.
    """
    synth_call_count = 0

    async def mock_slow_synth(**kwargs):
        nonlocal synth_call_count
        synth_call_count += 1
        await asyncio.sleep(0.25)
        wav_payload = b"RIFF" + (b"\x00" * 40) + b"timedOutAudioDataPayload48kHz" * 2
        return wav_payload, 0.25, "worker-primary-timeout"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_slow_synth)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        email = f"header_timeout_tester_{uuid.uuid4().hex[:8]}@example.com"
        reg = await ac.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": email, "password": "password123", "full_name": "Header Timeout Tester"},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        user_id = reg.json()["user"]["id"]
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Client-Timeout": "0.1",  # Client only waits 0.1s
            **API_KEY_HEADER,
        }

        unique_phrase = f"Câu văn thử nghiệm X-Client-Timeout header {time.time()}"
        cache_key = cache_service.get_cache_key("minh_duc", unique_phrase, smart_pause=True)

        # 1. Request synthesis with 0.1s client timeout
        res = await ac.post(
            "/v1/audio/speech",
            headers=headers,
            json={"text": unique_phrase, "voice": "minh_duc"},
        )
        # Gateway returns 499 (Client Closed Request / Timed out)
        assert res.status_code == 499

        # Quota must be rolled back to 0
        used_after_timeout = await get_daily_usage(user_id)
        assert used_after_timeout == 0

        # Unbilled cache file must be saved
        unbilled_file = cache_service.get_unbilled_cached_file(cache_key)
        assert unbilled_file is not None
        assert unbilled_file.exists()
        assert cache_service.get_cached_file(cache_key) is None

        # 2. Client retries (with standard timeout)
        retry_headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}
        res_retry = await ac.post(
            "/v1/audio/speech",
            headers=retry_headers,
            json={"text": unique_phrase, "voice": "minh_duc"},
        )
        assert res_retry.status_code == 200
        assert res_retry.headers.get("x-cache-status") == "HIT-CLAIMED"

        # Quota is now deducted
        assert await get_daily_usage(user_id) == len(unique_phrase)

        # File is promoted to standard cache
        assert cache_service.get_unbilled_cached_file(cache_key) is None
        assert cache_service.get_cached_file(cache_key) is not None

        # 3. Third call is instant HIT
        res_3rd = await ac.post(
            "/v1/audio/speech",
            headers=retry_headers,
            json={"text": unique_phrase, "voice": "minh_duc"},
        )
        assert res_3rd.status_code == 200
        assert res_3rd.headers.get("x-cache-status") == "HIT"

        # Worker was called only ONCE
        assert synth_call_count == 1


