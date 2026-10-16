import asyncio
import uuid
import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.core.database import AsyncSessionLocal, init_db
from VoiceClonerAppServer.models.db_models import User

client = TestClient(app, headers={"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"})


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def test_private_network_access_pna_options_header():
    """Verify server responds with Access-Control-Allow-Private-Network on preflight."""
    headers = {
        "Origin": "https://ngominhtoan.github.io",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Private-Network": "true",
    }
    res = client.options("/health", headers=headers)
    assert res.headers.get("access-control-allow-private-network") == "true" or res.headers.get("Access-Control-Allow-Private-Network") == "true"


def test_private_network_access_get_header():
    """Verify server appends Access-Control-Allow-Private-Network on GET responses."""
    headers = {
        "Origin": "https://ngominhtoan.github.io",
    }
    res = client.get("/health", headers=headers)
    assert res.headers.get("access-control-allow-private-network") == "true" or res.headers.get("Access-Control-Allow-Private-Network") == "true"


def test_user_management_api_flow():
    m_key = settings_manager.get_settings().security.management_key
    unique_id = f"usr_{uuid.uuid4().hex[:8]}"
    unique_email = f"tester_{uuid.uuid4().hex[:8]}@example.com"

    # 1. Seed test user directly into DB
    async def create_user():
        async with AsyncSessionLocal() as session:
            test_user = User(
                id=unique_id,
                email=unique_email,
                full_name="Nguyễn Văn Test",
                auth_provider="local",
                max_cloned_slots=3,
            )
            session.add(test_user)
            await session.commit()

    asyncio.run(create_user())

    # 2. Query users list via Management API
    res = client.get("/v1/management/users", headers={"X-Management-Key": m_key})
    assert res.status_code == 200
    users = res.json()
    assert any(u["id"] == unique_id for u in users)

    # 3. Update user max_cloned_slots
    patch_res = client.patch(
        f"/v1/management/users/{unique_id}",
        headers={"X-Management-Key": m_key},
        json={"max_cloned_slots": 5}
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["max_cloned_slots"] == 5

    # 4. Delete user
    del_res = client.delete(
        f"/v1/management/users/{unique_id}",
        headers={"X-Management-Key": m_key}
    )
    assert del_res.status_code == 200


def test_management_users_returns_accurate_slots_and_tts_chars(monkeypatch):
    import time
    from VoiceClonerAppServer.models.db_models import Voice, utc_now
    from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher

    async def mock_synth(**kwargs):
        return (b"RIFFmockwavdata", 0.05, "worker-mock")

    monkeypatch.setattr(model_dispatcher, "synthesize", mock_synth)

    m_key = settings_manager.get_settings().security.management_key
    email = f"slot_user_{int(time.time() * 1000)}@example.com"
    reg = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "password123", "full_name": "Slot Tester"}
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]

    # Add 1 cloned voice for this user
    async def add_voice():
        async with AsyncSessionLocal() as session:
            v = Voice(
                id=f"voice_{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                name="Custom Voice 1",
                sample_text="Xin chào",
                is_builtin=False,
                status="ready",
                created_at=utc_now(),
            )
            session.add(v)
            await session.commit()

    asyncio.run(add_voice())

    # Synthesize speech (cache miss phrase) to consume TTS characters
    phrase = f"Custom phrase {time.time()}"
    synth_res = client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": phrase, "voice": "minh_duc"}
    )
    assert synth_res.status_code == 200

    # Query management users list
    res = client.get("/v1/management/users", headers={"X-Management-Key": m_key})
    assert res.status_code == 200
    users = res.json()
    target = next((u for u in users if u["id"] == user_id), None)
    assert target is not None

    # Check cloned_voices_count and tts_chars_today
    assert "cloned_voices_count" in target
    assert target["cloned_voices_count"] == 1
    assert target["max_cloned_slots"] == 3
    assert target["tts_chars_today"] == len(phrase)

