import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from VoiceClonerAppServer.core.database import AsyncSessionLocal, init_db
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.models.db_models import AnonymousDailyUsage, AnonymousUser

client = TestClient(app, headers={"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"})


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def _management_headers():
    return {"X-Management-Key": settings_manager.get_settings().security.management_key}


def _create_anonymous_user() -> str:
    install_id = str(uuid.uuid4())
    res = client.post("/v1/auth/anonymous", json={"install_id": install_id})
    assert res.status_code == 200
    return res.json()["user"]["id"]


def test_list_patch_and_guard_clone_slots():
    install_id = str(uuid.uuid4())
    anon_id = client.post("/v1/auth/anonymous", json={"install_id": install_id}).json()["user"]["id"]
    headers = {"X-Management-Key": settings_manager.get_settings().security.management_key}
    listed = client.get("/v1/management/anonymous-users", headers=headers)
    assert listed.status_code == 200
    assert any(u["id"] == anon_id for u in listed.json())
    ok = client.patch(f"/v1/management/anonymous-users/{anon_id}", headers=headers, json={"full_name": "Khách 2"})
    assert ok.status_code == 200 and ok.json()["full_name"] == "Khách 2"
    bad = client.patch(f"/v1/management/anonymous-users/{anon_id}", headers=headers, json={"max_cloned_slots": 3})
    assert bad.status_code == 400


def test_list_reports_usage_bonus_and_delete_cascades():
    anon_id = _create_anonymous_user()
    headers = _management_headers()

    async def _seed():
        async with AsyncSessionLocal() as session:
            user = await session.get(AnonymousUser, anon_id)
            user.bonus_tts_chars = 250
            session.add(
                AnonymousDailyUsage(
                    user_id=anon_id,
                    usage_date=datetime.now(timezone.utc).date(),
                    tts_characters_used=123,
                )
            )
            await session.commit()

    asyncio.run(_seed())

    listed = client.get("/v1/management/anonymous-users", headers=headers)
    assert listed.status_code == 200
    entry = next(u for u in listed.json() if u["id"] == anon_id)
    assert entry["tts_chars_today"] == 123
    assert entry["bonus_tts_chars"] == 250
    assert entry["max_cloned_slots"] == 0
    assert entry["auth_provider"] == "anonymous"

    patched = client.patch(
        f"/v1/management/anonymous-users/{anon_id}",
        headers=headers,
        json={"daily_tts_char_limit": 20000, "max_queue_waiters": 0},
    )
    assert patched.status_code == 200
    assert patched.json()["daily_tts_char_limit"] == 20000
    assert patched.json()["max_queue_waiters"] == 0
    assert patched.json()["max_cloned_slots"] == 0

    deleted = client.delete(f"/v1/management/anonymous-users/{anon_id}", headers=headers)
    assert deleted.status_code == 200

    listed_after = client.get("/v1/management/anonymous-users", headers=headers)
    assert all(u["id"] != anon_id for u in listed_after.json())

    async def _usage_count() -> int:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(func.count())
                .select_from(AnonymousDailyUsage)
                .where(AnonymousDailyUsage.user_id == anon_id)
            )
            return res.scalar() or 0

    assert asyncio.run(_usage_count()) == 0


def test_patch_missing_user_returns_404():
    headers = _management_headers()
    res = client.patch(
        f"/v1/management/anonymous-users/{uuid.uuid4()}",
        headers=headers,
        json={"full_name": "Ghost"},
    )
    assert res.status_code == 404
