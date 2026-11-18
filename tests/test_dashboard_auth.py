import asyncio

import pytest
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


@pytest.fixture(autouse=True)
def _clear_cookies():
    client.cookies.clear()
    _reset_login_rate_limit()
    yield
    client.cookies.clear()
    _reset_login_rate_limit()


def test_login_with_wrong_key_returns_401_without_cookie():
    res = client.post("/v1/dashboard/session", json={"key": "wrong"})
    assert res.status_code == 401
    assert "set-cookie" not in {k.lower() for k in res.headers}


def test_login_sets_strict_httponly_cookie_and_status_works():
    res = client.post("/v1/dashboard/session", json={"key": KEY})
    assert res.status_code == 200
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/v1/dashboard/session").status_code == 200


def test_logout_clears_session():
    client.post("/v1/dashboard/session", json={"key": KEY})
    assert client.delete("/v1/dashboard/session").status_code == 204
    assert client.get("/v1/dashboard/session").status_code == 401


def test_login_rate_limit_returns_429():
    for _ in range(10):
        assert client.post("/v1/dashboard/session", json={"key": "wrong"}).status_code == 401
    res = client.post("/v1/dashboard/session", json={"key": "wrong"})
    assert res.status_code == 429
    assert res.headers["retry-after"] == "300"


def test_login_fails_closed_when_key_unset(monkeypatch):
    monkeypatch.setattr(settings_manager, "_env_management_key", "")
    monkeypatch.setattr(settings_manager, "_settings", settings_manager._settings.model_copy(deep=True))
    settings_manager._settings.security.management_key = ""
    res = client.post("/v1/dashboard/session", json={"key": "anything"})
    assert res.status_code == 503


def test_login_with_non_ascii_key_returns_401_not_500():
    res = client.post("/v1/dashboard/session", json={"key": "khóa-mật-khẩu"})
    assert res.status_code == 401


def test_session_endpoints_always_send_no_store():
    wrong = client.post("/v1/dashboard/session", json={"key": "wrong"})
    assert wrong.status_code == 401
    assert wrong.headers["cache-control"] == "no-store"

    login = client.post("/v1/dashboard/session", json={"key": KEY})
    assert login.status_code == 200
    assert login.headers["cache-control"] == "no-store"

    logout = client.delete("/v1/dashboard/session")
    assert logout.status_code == 204
    assert logout.headers["cache-control"] == "no-store"

    status = client.get("/v1/dashboard/session")
    assert status.status_code == 401
    assert status.headers["cache-control"] == "no-store"
