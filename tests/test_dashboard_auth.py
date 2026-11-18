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


def _login():
    assert client.post("/v1/dashboard/session", json={"key": KEY}).status_code == 200


def test_settings_response_redacts_management_key():
    res = client.get("/v1/management/settings", headers={"X-Management-Key": KEY})
    data = res.json()
    assert data["security"]["management_key"] == ""
    assert data["management_key_configured"] is True
    assert data["management_key_source"] == "environment"
    assert data["client_api_keys"] is not None


def test_cookie_cannot_rotate_management_key():
    _login()
    res = client.put(
        "/v1/management/settings",
        headers={"X-Dashboard-Request": "1"},
        json={"security": {"management_key": "new_key_from_cookie"}},
    )
    assert res.status_code == 403


def test_header_rotation_invalidates_sessions(monkeypatch):
    _login()
    # Simulate a config-managed key without touching the real config file.
    monkeypatch.setattr(settings_manager, "_settings", settings_manager._settings.model_copy(deep=True))
    monkeypatch.setattr(settings_manager, "_env_management_key", "")
    settings_manager._settings.security.management_key = KEY
    monkeypatch.setattr(settings_manager, "_save_to_disk", lambda: None)

    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": KEY},
        json={"security": {"management_key": "rotated_key_123"}},
    )
    assert res.status_code == 200
    assert res.json()["security"]["management_key"] == ""
    assert client.get("/v1/dashboard/session").status_code == 401
    assert client.get("/v1/management/settings", headers={"X-Management-Key": "rotated_key_123"}).status_code == 200


def test_env_managed_key_rotation_is_rejected():
    _login()
    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": KEY},
        json={"security": {"management_key": "ignored_key_123"}},
    )
    assert res.status_code == 409


def test_cookie_authenticates_management_get():
    _login()
    assert client.get("/v1/management/settings").status_code == 200


def test_cookie_mutation_requires_csrf_header():
    _login()
    res = client.put("/v1/management/settings", json={"server": {"port": 8000}})
    assert res.status_code == 403


def test_env_key_equal_value_is_not_written_to_disk():
    import json

    def _on_disk_key():
        with open(settings_manager._path, "r", encoding="utf-8") as f:
            return json.load(f)["security"]["management_key"]

    before = _on_disk_key()
    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": KEY},
        json={"security": {"management_key": KEY}},
    )
    assert res.status_code == 200
    assert _on_disk_key() == before


def test_non_string_management_key_returns_422():
    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": KEY},
        json={"security": {"management_key": 123}},
    )
    assert res.status_code == 422


def test_null_management_key_returns_422():
    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": KEY},
        json={"security": {"management_key": None}},
    )
    assert res.status_code == 422


def test_whitespace_padded_key_is_treated_as_unchanged(monkeypatch):
    _login()
    monkeypatch.setattr(settings_manager, "_settings", settings_manager._settings.model_copy(deep=True))
    monkeypatch.setattr(settings_manager, "_env_management_key", "")
    settings_manager._settings.security.management_key = KEY
    monkeypatch.setattr(settings_manager, "_save_to_disk", lambda: None)

    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": KEY},
        json={"security": {"management_key": f" {KEY} "}},
    )
    assert res.status_code == 200
    assert settings_manager._settings.security.management_key == KEY
