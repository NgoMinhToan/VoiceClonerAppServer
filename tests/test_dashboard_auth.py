import asyncio

from fastapi.testclient import TestClient

from VoiceClonerAppServer.core import security
from VoiceClonerAppServer.core.redis_client import redis_client
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.main import app

KEY = "test_management_key"
client = TestClient(app)


def _reset_login_rate_limit():
    asyncio.run(redis_client.delete("dashboard_login:testclient"))


def test_session_token_roundtrip_and_rotation():
    token = security.create_dashboard_session_token(KEY)
    assert security.verify_dashboard_session_token(token, KEY) is True
    assert security.verify_dashboard_session_token(token, "different_key") is False
    assert security.verify_dashboard_session_token("garbage", KEY) is False


def test_management_api_fails_closed_when_key_unset(monkeypatch):
    monkeypatch.setattr(settings_manager, "_env_management_key", "")
    monkeypatch.setattr(settings_manager, "_settings", settings_manager._settings.model_copy(deep=True))
    settings_manager._settings.security.management_key = ""
    res = client.get("/v1/management/settings")
    assert res.status_code == 503


def test_header_key_still_authenticates():
    res = client.get("/v1/management/settings", headers={"X-Management-Key": KEY})
    assert res.status_code == 200
