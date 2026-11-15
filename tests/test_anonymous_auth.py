import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from VoiceClonerAppServer.api import auth as auth_api
from VoiceClonerAppServer.core.database import AsyncSessionLocal, init_db
from VoiceClonerAppServer.core.redis_client import redis_client
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.models.db_models import AnonymousUser
from VoiceClonerAppServer.services import anonymous_service

client = TestClient(
    app, headers={"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}
)

DUMMY_WAV = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


class _StubSettingsManager:
    """Expose a settings snapshot without touching the shared singleton."""

    def __init__(self, settings):
        self._settings = settings

    def get_settings(self):
        return self._settings


def _settings_with_security(**overrides):
    base = settings_manager.get_settings()
    return base.model_copy(
        update={"security": base.security.model_copy(update=overrides)}
    )


def _patch_service_security(monkeypatch, **overrides):
    stub = _StubSettingsManager(_settings_with_security(**overrides))
    monkeypatch.setattr(anonymous_service, "settings_manager", stub)
    # The endpoint resolves the client IP with its own settings reference.
    monkeypatch.setattr(auth_api, "settings_manager", stub)


def _reset_signup_bucket(client_ip: str = "testclient") -> None:
    asyncio.run(redis_client.delete(anonymous_service.signup_rate_key(client_ip)))


def _login(install_id: str, **kwargs):
    return client.post("/v1/auth/anonymous", json={"install_id": install_id}, **kwargs)


def test_anonymous_login_is_idempotent():
    install_id = str(uuid.uuid4())
    first = _login(install_id)
    second = _login(install_id)
    assert first.status_code == second.status_code == 200
    assert first.json()["user"]["id"] == second.json()["user"]["id"]
    assert first.json()["user"]["max_cloned_slots"] == 0
    assert first.json()["user"]["auth_provider"] == "anonymous"
    assert first.json()["user"]["bonus_tts_chars"] == 0


def test_anonymous_tokens_carry_anonymous_account_type():
    from VoiceClonerAppServer.core.security import decode_token

    payload = _login(str(uuid.uuid4())).json()
    assert decode_token(payload["access_token"])["acct"] == "anonymous"
    assert decode_token(payload["refresh_token"])["acct"] == "anonymous"


def test_anonymous_creates_new_account_after_desync():
    install_id = str(uuid.uuid4())
    old_id = _login(install_id).json()["user"]["id"]

    # Simulate a new server DB: the row is gone.
    async def _delete():
        async with AsyncSessionLocal() as s:
            await s.execute(delete(AnonymousUser).where(AnonymousUser.id == old_id))
            await s.commit()

    asyncio.run(_delete())
    new_id = _login(install_id).json()["user"]["id"]
    assert new_id != old_id


def test_anonymous_profile_reports_zero_clone_slots():
    access_token = _login(str(uuid.uuid4())).json()["access_token"]
    me = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert me.status_code == 200
    assert me.json()["max_cloned_slots"] == 0
    assert me.json()["bonus_tts_chars"] == 0


def test_invalid_install_id_rejected():
    assert _login("not-a-uuid").status_code == 422


def test_anonymous_cannot_clone():
    access_token = _login(str(uuid.uuid4())).json()["access_token"]
    res = client.post(
        "/v1/voices/clone",
        headers={"Authorization": f"Bearer {access_token}"},
        files={"audio_file": ("sample.wav", DUMMY_WAV, "audio/wav")},
        data={"name": "Giọng Khách", "gender": "male", "region": "bac"},
    )
    assert res.status_code == 403
    assert res.json()["detail"] == (
        "Tài khoản khách không hỗ trợ clone giọng. Vui lòng đăng nhập."
    )


def test_rate_limit_only_counts_new_accounts(monkeypatch):
    _patch_service_security(monkeypatch, anonymous_signup_per_ip_per_day=1)
    _reset_signup_bucket()

    first_install = str(uuid.uuid4())
    second_install = str(uuid.uuid4())

    assert _login(first_install).status_code == 200
    assert _login(second_install).status_code == 429
    # Repeating an existing install is idempotent and is not counted.
    assert _login(first_install).status_code == 200


def test_trust_forwarded_for_uses_header_ip(monkeypatch):
    _patch_service_security(
        monkeypatch,
        anonymous_signup_per_ip_per_day=1,
        trust_forwarded_for=True,
    )
    ip_one = "203.0.113.10"
    ip_two = "203.0.113.20"
    _reset_signup_bucket(ip_one)
    _reset_signup_bucket(ip_two)

    first_install = str(uuid.uuid4())
    second_install = str(uuid.uuid4())
    third_install = str(uuid.uuid4())

    assert _login(first_install, headers={"X-Forwarded-For": ip_one}).status_code == 200
    assert _login(second_install, headers={"X-Forwarded-For": ip_two}).status_code == 200
    # The first IP has already used its single account for the day.
    assert _login(third_install, headers={"X-Forwarded-For": ip_one}).status_code == 429


def test_forwarded_for_ignored_when_flag_disabled(monkeypatch):
    _patch_service_security(monkeypatch, anonymous_signup_per_ip_per_day=1)
    _reset_signup_bucket()

    ip_one = "203.0.113.30"
    ip_two = "203.0.113.40"

    # With the flag off the header is ignored, so both requests share the
    # `testclient` bucket and the second one is rejected.
    assert _login(str(uuid.uuid4()), headers={"X-Forwarded-For": ip_one}).status_code == 200
    assert _login(str(uuid.uuid4()), headers={"X-Forwarded-For": ip_two}).status_code == 429
