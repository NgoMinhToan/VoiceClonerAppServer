import asyncio
import io
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.core.database import AsyncSessionLocal
from VoiceClonerAppServer.models.db_models import User
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher

client = TestClient(app)
API_KEY_HEADER = {"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}


def test_user_custom_speech_char_limit():
    email = f"user_sp_lim_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post(
        "/v1/auth/register",
        headers=API_KEY_HEADER,
        json={"email": email, "password": "password123", "full_name": "Speech Limit User"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

    # Set user's custom limit to 500 characters in DB
    async def set_limit():
        async with AsyncSessionLocal() as session:
            u = await session.get(User, user_id)
            u.max_speech_char_limit = 500
            await session.commit()

    asyncio.run(set_limit())

    # 501 characters should fail with 400
    long_text = "B" * 501
    res = client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"voice": "minh_duc", "text": long_text}
    )
    assert res.status_code == 400
    assert "vượt quá giới hạn tối đa" in res.json()["detail"]


def test_user_custom_preview_char_limit():
    email = f"user_prev_lim_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post(
        "/v1/auth/register",
        headers=API_KEY_HEADER,
        json={"email": email, "password": "password123", "full_name": "Preview Limit User"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

    # Set user's custom preview limit to 100 characters in DB
    async def set_limit():
        async with AsyncSessionLocal() as session:
            u = await session.get(User, user_id)
            u.max_preview_char_limit = 100
            await session.commit()

    asyncio.run(set_limit())

    # 101 characters should fail with 400
    long_text = "C" * 101
    res = client.post(
        "/v1/voices/preview",
        headers=headers,
        json={"voice_id": "minh_duc", "text": long_text}
    )
    assert res.status_code == 400
    assert "vượt quá giới hạn tối đa" in res.json()["detail"]


def test_user_custom_daily_quota(monkeypatch):
    async def mock_synth(**kwargs):
        return b"RIFF" + (b"\x00" * 40) + b"mockWavBytes48k" * 2, 0.01, "worker-mock"

    monkeypatch.setattr(model_dispatcher, "synthesize", mock_synth)

    email = f"user_quota_lim_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post(
        "/v1/auth/register",
        headers=API_KEY_HEADER,
        json={"email": email, "password": "password123", "full_name": "Quota Limit User"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

    # Set custom daily quota of 300 characters
    async def set_quota():
        async with AsyncSessionLocal() as session:
            u = await session.get(User, user_id)
            u.daily_tts_char_limit = 300
            await session.commit()

    asyncio.run(set_quota())

    # Send 250 characters (Cache MISS)
    phrase_1 = f"UNIQUE_{uuid.uuid4().hex[:6]}_" + ("X" * (250 - 15))
    res1 = client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"voice": "minh_duc", "text": phrase_1}
    )
    assert res1.status_code == 200

    # Next call of 100 characters exceeds remaining 50 chars -> 403 Forbidden
    phrase_2 = f"UNIQUE_{uuid.uuid4().hex[:6]}_" + ("Y" * (100 - 15))
    res2 = client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"voice": "minh_duc", "text": phrase_2}
    )
    assert res2.status_code == 403
    assert "Hạn mức ký tự còn lại hôm nay không đủ" in res2.json()["detail"]


def test_user_speech_concurrency_queue_overflow(monkeypatch):
    import threading

    async def slow_synth(**kwargs):
        await asyncio.sleep(0.3)
        return b"RIFF" + (b"\x00" * 40) + b"slowWavBytes" * 2, 0.3, "slow-worker"

    monkeypatch.setattr(model_dispatcher, "synthesize", slow_synth)

    email = f"user_conc_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post(
        "/v1/auth/register",
        headers=API_KEY_HEADER,
        json={"email": email, "password": "password123", "full_name": "Concurrency User"}
    )
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

    # Set user limits to 1 concurrent and 1 queue waiter
    async def set_concurrency():
        async with AsyncSessionLocal() as session:
            u = await session.get(User, user_id)
            u.max_concurrent_tts = 1
            u.max_queue_waiters = 1
            await session.commit()

    asyncio.run(set_concurrency())

    results = []

    def make_request(idx):
        text = f"CONC_TEST_{uuid.uuid4().hex[:8]}_{idx}"
        r = client.post(
            "/v1/audio/speech",
            headers=headers,
            json={"voice": "minh_duc", "text": text}
        )
        results.append((idx, r.status_code, r.text))

    t1 = threading.Thread(target=make_request, args=(1,))
    t2 = threading.Thread(target=make_request, args=(2,))
    t3 = threading.Thread(target=make_request, args=(3,))

    t1.start()
    time.sleep(0.03)  # t1 enters active slot
    t2.start()
    time.sleep(0.03)  # t2 enters waiting queue (size 1)
    t3.start()        # t3 overflows queue (capacity 1) -> 429

    t1.join()
    t2.join()
    t3.join()

    status_codes = [r[1] for r in results]
    assert 429 in status_codes
    assert 200 in status_codes

