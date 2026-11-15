import asyncio
import base64
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from VoiceClonerAppServer.api import ads as ads_api
from VoiceClonerAppServer.core.database import AsyncSessionLocal, init_db
from VoiceClonerAppServer.core.security import create_access_token
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.models.db_models import (
    AdRewardClaim,
    AnonymousUser,
    Base,
    BonusCharLedger,
)
from VoiceClonerAppServer.services import ad_rewards_service, admob_ssv

API_KEY_HEADER = {"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}

client = TestClient(app, headers=API_KEY_HEADER)


@pytest.fixture(autouse=True)
def _setup_db():
    asyncio.run(init_db())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_key, public_pem


def _signed_query(
    private_key, params: dict[str, str], key_id: str = "1"
) -> tuple[str, str, str]:
    raw = "&".join(f"{k}={v}" for k, v in params.items())
    signature = private_key.sign(raw.encode(), ec.ECDSA(hashes.SHA256()))
    return raw, base64.b64encode(signature).decode(), key_id


def _signed_url(private_key, params: dict[str, str], key_id: str = "1") -> str:
    raw, signature, key_id = _signed_query(private_key, params, key_id=key_id)
    tail = urlencode({"signature": signature, "key_id": key_id})
    return f"{raw}&{tail}"


class _FakeVerifierResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _install_fake_verifier(monkeypatch, state):
    """Replace the verifier HTTP client; `state` holds the payload and call log."""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            state["calls"].append(url)
            return _FakeVerifierResponse(state["payload"])

    monkeypatch.setattr(admob_ssv, "httpx", SimpleNamespace(AsyncClient=_FakeClient))
    return state


def _unique_verifier_url() -> str:
    return f"https://verifier.test/{uuid.uuid4().hex}.json"


def _anon(bonus_tts_chars: int = 0) -> AnonymousUser:
    account_id = str(uuid.uuid4())
    return AnonymousUser(
        id=account_id,
        install_id=str(uuid.uuid4()),
        email=f"anon+{account_id}@anonymous.local",
        full_name="Khách",
        max_cloned_slots=0,
        bonus_tts_chars=bonus_tts_chars,
    )


@asynccontextmanager
async def _session(account):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(account)
        await db.commit()
        yield db, account
    await engine.dispose()


class _StubSettingsManager:
    def __init__(self, ads):
        self._settings = SimpleNamespace(ad_rewards=ads)

    def get_settings(self):
        return self._settings


def _ads_settings(**overrides):
    return settings_manager.get_settings().ad_rewards.model_copy(
        update={"enabled": True, **overrides}
    )


def _patch_ads(monkeypatch, **overrides):
    stub = _StubSettingsManager(_ads_settings(**overrides))
    monkeypatch.setattr(ad_rewards_service, "settings_manager", stub)
    monkeypatch.setattr(ads_api, "settings_manager", stub)
    return stub


async def _create_anonymous_account() -> str:
    account_id = str(uuid.uuid4())
    install_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(
            AnonymousUser(
                id=account_id,
                install_id=install_id,
                email=f"anon+{install_id}@anonymous.local",
                full_name="Khách",
                max_cloned_slots=0,
                bonus_tts_chars=0,
            )
        )
        await db.commit()
    return account_id


async def _claims(db):
    return (await db.execute(select(AdRewardClaim))).scalars().all()


async def _ledger(db):
    return (await db.execute(select(BonusCharLedger))).scalars().all()


async def _credit(db, transaction_id: str, account_id: str, ad_unit: str, account_type="anonymous"):
    return await ad_rewards_service.credit_ad_reward(
        db, transaction_id, account_id, account_type, ad_unit
    )


# ---------------------------------------------------------------------------
# Signature and signed-content helpers
# ---------------------------------------------------------------------------
def test_valid_signature_verifies():
    private_key, public_pem = _keypair()
    raw, sig, key_id = _signed_query(
        private_key,
        {"user_id": "u1", "transaction_id": "t1", "ad_unit": "unit-1"},
    )
    assert admob_ssv.verify_signature(raw, sig, key_id, {"1": public_pem}) is True


def test_tampered_query_rejected():
    private_key, public_pem = _keypair()
    raw, sig, key_id = _signed_query(private_key, {"user_id": "u1", "transaction_id": "t1"})
    assert admob_ssv.verify_signature(raw.replace("u1", "u2"), sig, key_id, {"1": public_pem}) is False


def test_unknown_key_id_rejected():
    private_key, public_pem = _keypair()
    raw, sig, _key_id = _signed_query(private_key, {"user_id": "u1"})
    assert admob_ssv.verify_signature(raw, sig, "2", {"1": public_pem}) is False


def test_build_signed_content_drops_signature_and_key_id_in_order():
    raw = (
        "ad_unit=unit-1&user_id=u1&signature=abc&transaction_id=t1"
        "&key_id=1&custom_data=xyz"
    )
    assert admob_ssv.build_signed_content(raw) == (
        "ad_unit=unit-1&user_id=u1&transaction_id=t1&custom_data=xyz"
    )


# ---------------------------------------------------------------------------
# Crediting service
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_first_credit_adds_chars_per_ad(monkeypatch):
    _patch_ads(monkeypatch, chars_per_ad=250)
    async with _session(_anon()) as (db, account):
        granted = await _credit(db, "tx-1", account.id, "unit-1")

        assert granted == 250
        await db.refresh(account)
        assert account.bonus_tts_chars == 250

        claims = await _claims(db)
        assert len(claims) == 1
        assert claims[0].transaction_id == "tx-1"
        assert claims[0].chars_granted == 250
        assert claims[0].account_type == "anonymous"

        ledger = await _ledger(db)
        assert len(ledger) == 1
        assert ledger[0].delta == 250
        assert ledger[0].reason == "ad_reward"
        assert ledger[0].reference == "tx-1"


@pytest.mark.asyncio
async def test_replay_returns_zero_and_leaves_balance_unchanged(monkeypatch):
    _patch_ads(monkeypatch, chars_per_ad=250)
    async with _session(_anon()) as (db, account):
        assert await _credit(db, "tx-1", account.id, "unit-1") == 250
        assert await _credit(db, "tx-1", account.id, "unit-1") == 0

        await db.refresh(account)
        assert account.bonus_tts_chars == 250
        assert len(await _claims(db)) == 1
        assert len(await _ledger(db)) == 1


@pytest.mark.asyncio
async def test_daily_cap_records_uncredited_claim(monkeypatch):
    _patch_ads(monkeypatch, chars_per_ad=250, max_ads_per_user_per_day=1)
    async with _session(_anon()) as (db, account):
        assert await _credit(db, "tx-1", account.id, "unit-1") == 250
        assert await _credit(db, "tx-2", account.id, "unit-1") == 0

        await db.refresh(account)
        assert account.bonus_tts_chars == 250
        claims = {c.transaction_id: c.chars_granted for c in await _claims(db)}
        assert claims == {"tx-1": 250, "tx-2": 0}


@pytest.mark.asyncio
async def test_unknown_account_raises_404_without_claim(monkeypatch):
    _patch_ads(monkeypatch)
    async with _session(_anon()) as (db, _account):
        with pytest.raises(HTTPException) as exc_info:
            await _credit(db, "tx-1", "missing-account", "unit-1", account_type="user")

        assert exc_info.value.status_code == 404
        assert await _claims(db) == []


@pytest.mark.asyncio
async def test_disallowed_ad_unit_raises_403_without_claim(monkeypatch):
    _patch_ads(monkeypatch, allowed_ad_unit_ids=["unit-1"])
    async with _session(_anon()) as (db, account):
        with pytest.raises(HTTPException) as exc_info:
            await _credit(db, "tx-1", account.id, "evil-unit")

        assert exc_info.value.status_code == 403
        await db.refresh(account)
        assert account.bonus_tts_chars == 0
        assert await _claims(db) == []


@pytest.mark.asyncio
async def test_disabled_rewards_raise_403(monkeypatch):
    _patch_ads(monkeypatch, enabled=False)
    async with _session(_anon()) as (db, account):
        with pytest.raises(HTTPException) as exc_info:
            await _credit(db, "tx-1", account.id, "unit-1")

        assert exc_info.value.status_code == 403
        assert await _claims(db) == []


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------
def test_ssv_callback_credits_once_across_replay(monkeypatch):
    private_key, public_pem = _keypair()

    async def fake_fetch(url, force_refresh=False):
        return {"1": public_pem}

    monkeypatch.setattr(admob_ssv, "fetch_verifier_keys", fake_fetch)
    _patch_ads(monkeypatch, chars_per_ad=1000, max_ads_per_user_per_day=10)

    account_id = asyncio.run(_create_anonymous_account())
    token = create_access_token(account_id, account_type="anonymous")

    params = {
        "ad_network": "5450213213286189855",
        "ad_unit": "unit-1",
        "custom_data": "dictionary-revision",
        "reward_amount": "999",
        "reward_item": "bonus_chars",
        "timestamp": "1700000000000",
        "transaction_id": f"tx-{uuid.uuid4().hex}",
        "user_id": account_id,
    }
    url = _signed_url(private_key, params)

    first = client.get(f"/ads/ssv?{url}")
    replay = client.get(f"/ads/ssv?{url}")

    assert first.status_code == 200
    assert first.text == "OK"
    assert replay.status_code == 200
    assert replay.text == "OK"

    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["bonus_tts_chars"] == 1000


def test_ssv_callback_rejects_tampered_query(monkeypatch):
    private_key, public_pem = _keypair()

    async def fake_fetch(url, force_refresh=False):
        return {"1": public_pem}

    monkeypatch.setattr(admob_ssv, "fetch_verifier_keys", fake_fetch)
    _patch_ads(monkeypatch)

    account_id = asyncio.run(_create_anonymous_account())
    params = {
        "ad_unit": "unit-1",
        "transaction_id": f"tx-{uuid.uuid4().hex}",
        "user_id": account_id,
    }
    url = _signed_url(private_key, params)

    tampered = url.replace(f"user_id={account_id}", "user_id=somebody-else")
    res = client.get(f"/ads/ssv?{tampered}")

    assert res.status_code == 403


def test_ssv_callback_rejects_unknown_account(monkeypatch):
    private_key, public_pem = _keypair()

    async def fake_fetch(url, force_refresh=False):
        return {"1": public_pem}

    monkeypatch.setattr(admob_ssv, "fetch_verifier_keys", fake_fetch)
    _patch_ads(monkeypatch)

    params = {
        "ad_unit": "unit-1",
        "transaction_id": f"tx-{uuid.uuid4().hex}",
        "user_id": str(uuid.uuid4()),
    }
    res = client.get(f"/ads/ssv?{_signed_url(private_key, params)}")

    assert res.status_code == 404


def test_ssv_callback_rejected_when_disabled(monkeypatch):
    _patch_ads(monkeypatch, enabled=False)

    params = {
        "ad_unit": "unit-1",
        "transaction_id": f"tx-{uuid.uuid4().hex}",
        "user_id": str(uuid.uuid4()),
        "signature": "irrelevant",
        "key_id": "1",
    }
    res = client.get(f"/ads/ssv?{urlencode(params)}")

    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Bounded verifier-key refresh policy
# ---------------------------------------------------------------------------
def test_unknown_key_id_with_fresh_cache_fails_closed_without_fetch(monkeypatch):
    private_key, public_pem = _keypair()
    verifier_url = _unique_verifier_url()
    _patch_ads(monkeypatch, ssv_verifier_url=verifier_url)
    # A fresh cache only knows key "1"; the callback is signed with key "2".
    admob_ssv._verifier_key_cache[verifier_url] = (time.monotonic(), {"1": public_pem})
    state = _install_fake_verifier(monkeypatch, {"calls": [], "payload": {"keys": []}})

    account_id = asyncio.run(_create_anonymous_account())
    params = {
        "ad_unit": "unit-1",
        "transaction_id": f"tx-{uuid.uuid4().hex}",
        "user_id": account_id,
    }
    res = client.get(f"/ads/ssv?{_signed_url(private_key, params, key_id='2')}")

    assert res.status_code == 403
    assert state["calls"] == []


@pytest.mark.asyncio
async def test_forced_refresh_is_rate_limited(monkeypatch):
    _private_key, public_pem = _keypair()
    verifier_url = _unique_verifier_url()
    state = _install_fake_verifier(
        monkeypatch,
        {"calls": [], "payload": {"keys": [{"keyId": 1, "pem": public_pem}]}},
    )

    first = await admob_ssv.fetch_verifier_keys(verifier_url, force_refresh=True)
    second = await admob_ssv.fetch_verifier_keys(verifier_url, force_refresh=True)

    assert first == {"1": public_pem}
    assert second == {"1": public_pem}
    assert len(state["calls"]) == 1


@pytest.mark.asyncio
async def test_rotated_key_verifies_after_forced_refresh_cooldown(monkeypatch):
    _old_private, old_pem = _keypair()
    new_private, new_pem = _keypair()
    verifier_url = _unique_verifier_url()
    state = _install_fake_verifier(
        monkeypatch,
        {"calls": [], "payload": {"keys": [{"keyId": 1, "pem": old_pem}]}},
    )

    initial = await admob_ssv.fetch_verifier_keys(verifier_url)
    raw, signature, _key_id = _signed_query(
        new_private, {"user_id": "u1", "transaction_id": "t1"}
    )
    assert admob_ssv.verify_signature(raw, signature, "2", initial) is False

    # Inside the cooldown the forced refresh is throttled to the cached set.
    assert await admob_ssv.fetch_verifier_keys(verifier_url, force_refresh=True) == {
        "1": old_pem
    }
    assert len(state["calls"]) == 1

    # After the cooldown Google's rotated key set is fetched and verifies.
    state["payload"] = {
        "keys": [{"keyId": 1, "pem": old_pem}, {"keyId": 2, "pem": new_pem}]
    }
    admob_ssv._last_fetch_attempt[verifier_url] = (
        time.monotonic() - admob_ssv.FORCED_REFRESH_COOLDOWN_SECONDS - 1
    )
    rotated = await admob_ssv.fetch_verifier_keys(verifier_url, force_refresh=True)

    assert rotated["2"] == new_pem
    assert admob_ssv.verify_signature(raw, signature, "2", rotated) is True
    assert len(state["calls"]) == 2


def test_rotated_key_verifies_after_stale_cache_reload(monkeypatch):
    new_private, new_pem = _keypair()
    verifier_url = _unique_verifier_url()
    _patch_ads(monkeypatch, ssv_verifier_url=verifier_url, chars_per_ad=1000)
    stale_at = time.monotonic() - admob_ssv.VERIFIER_KEY_CACHE_TTL_SECONDS - 1
    admob_ssv._verifier_key_cache[verifier_url] = (stale_at, {"1": "stale-pem"})
    state = _install_fake_verifier(
        monkeypatch,
        {"calls": [], "payload": {"keys": [{"keyId": 2, "pem": new_pem}]}},
    )

    account_id = asyncio.run(_create_anonymous_account())
    params = {
        "ad_unit": "unit-1",
        "transaction_id": f"tx-{uuid.uuid4().hex}",
        "user_id": account_id,
    }
    res = client.get(f"/ads/ssv?{_signed_url(new_private, params, key_id='2')}")

    assert res.status_code == 200
    assert res.text == "OK"
    assert len(state["calls"]) == 1


def test_ads_config_reports_remaining_ads(monkeypatch):
    _patch_ads(
        monkeypatch,
        chars_per_ad=500,
        max_ads_per_user_per_day=3,
        rewarded_ad_unit_id="unit-rewarded",
    )
    account_id = asyncio.run(_create_anonymous_account())
    token = create_access_token(account_id, account_type="anonymous")

    res = client.get("/v1/ads/config", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200
    assert res.json() == {
        "enabled": True,
        "chars_per_ad": 500,
        "max_ads_per_day": 3,
        "ads_watched_today": 0,
        "ads_remaining_today": 3,
        "rewarded_ad_unit_id": "unit-rewarded",
    }
