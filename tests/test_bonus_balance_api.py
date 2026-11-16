import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.core.database import init_db
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.main import app

client = TestClient(
    app, headers={"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}
)


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def test_me_and_balance_include_bonus():
    install_id = str(uuid.uuid4())
    token = client.post("/v1/auth/anonymous", json={"install_id": install_id}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/v1/auth/me", headers=headers)
    balance = client.get("/v1/tokens/balance", headers=headers)
    assert me.status_code == 200 and me.json()["bonus_tts_chars"] == 0
    assert balance.status_code == 200
    expected_limit = settings_manager.get_settings().default_anonymous_limits.daily_tts_char_limit
    assert balance.json() == {
        "bonus_tts_chars": 0, "daily_tts_chars_used": 0,
        "daily_tts_chars_limit": expected_limit,
        "daily_tts_chars_remaining": expected_limit,
    }
