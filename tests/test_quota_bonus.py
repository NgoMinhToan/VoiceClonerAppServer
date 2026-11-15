from contextlib import asynccontextmanager
import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from VoiceClonerAppServer.core.database import AsyncSessionLocal, init_db
from VoiceClonerAppServer.core.redis_client import get_daily_usage
from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.models.db_models import (
    AnonymousDailyUsage,
    AnonymousUser,
    Base,
    BonusCharLedger,
    DailyUsage,
    User,
)
from VoiceClonerAppServer.services import quota_service
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher

API_KEY_HEADER = {"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}


async def _async(value):
    return value


async def _async_true():
    return True


async def _async_false():
    return False


def _anon(bonus_tts_chars: int = 1000) -> AnonymousUser:
    return AnonymousUser(
        id="a1",
        install_id="i1",
        email="anon+i1@anonymous.local",
        full_name="Khách",
        bonus_tts_chars=bonus_tts_chars,
    )


def _user(bonus_tts_chars: int = 1000) -> User:
    return User(
        id="u1",
        email="u1@example.com",
        full_name="Người dùng",
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


async def _ledger_rows(db):
    result = await db.execute(select(BonusCharLedger))
    return result.scalars().all()


@pytest.mark.asyncio
async def test_bonus_covers_overflow(monkeypatch):
    async with _session(_anon(1000)) as (db, account):
        monkeypatch.setattr(quota_service, "get_daily_usage", lambda _id: _async(100))
        monkeypatch.setattr(
            quota_service, "check_and_increment_daily_quota", lambda *_a, **_k: _async_true()
        )

        consumed = await quota_service.consume_speech_quota(db, account, 300, 200)

        assert consumed.bonus_used == 200
        await db.refresh(account)
        assert account.bonus_tts_chars == 800

        rows = await _ledger_rows(db)
        assert len(rows) == 1
        assert rows[0].account_id == "a1"
        assert rows[0].account_type == "anonymous"
        assert rows[0].delta == -200
        assert rows[0].reason == "tts_spend"


@pytest.mark.asyncio
async def test_no_overflow_uses_daily_quota_only(monkeypatch):
    calls: list[tuple] = []

    async def _increment(user_id, char_count, limit=None):
        calls.append((user_id, char_count, limit))
        return True

    async with _session(_anon(1000)) as (db, account):
        monkeypatch.setattr(quota_service, "get_daily_usage", lambda _id: _async(100))
        monkeypatch.setattr(quota_service, "check_and_increment_daily_quota", _increment)

        consumed = await quota_service.consume_speech_quota(db, account, 80, 200)

        assert consumed.bonus_used == 0
        assert calls == [("a1", 80, 200)]
        await db.refresh(account)
        assert account.bonus_tts_chars == 1000
        assert await _ledger_rows(db) == []


@pytest.mark.asyncio
async def test_remaining_equal_to_char_count_uses_daily_quota_only(monkeypatch):
    calls: list[tuple] = []

    async def _increment(user_id, char_count, limit=None):
        calls.append((user_id, char_count, limit))
        return True

    async with _session(_anon(1000)) as (db, account):
        monkeypatch.setattr(quota_service, "get_daily_usage", lambda _id: _async(100))
        monkeypatch.setattr(quota_service, "check_and_increment_daily_quota", _increment)

        consumed = await quota_service.consume_speech_quota(db, account, 100, 200)

        assert consumed.bonus_used == 0
        assert calls == [("a1", 100, 200)]
        await db.refresh(account)
        assert account.bonus_tts_chars == 1000


@pytest.mark.asyncio
async def test_insufficient_bonus_raises_403_and_keeps_balance(monkeypatch):
    async def _increment(*_a, **_k):
        raise AssertionError("daily quota must not be incremented when bonus is insufficient")

    async with _session(_anon(50)) as (db, account):
        monkeypatch.setattr(quota_service, "get_daily_usage", lambda _id: _async(100))
        monkeypatch.setattr(quota_service, "check_and_increment_daily_quota", _increment)

        with pytest.raises(HTTPException) as exc_info:
            await quota_service.consume_speech_quota(db, account, 300, 200)

        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        assert "100 ký tự còn lại" in detail
        assert "50 ký tự thưởng" in detail
        assert "300 ký tự" in detail

        await db.refresh(account)
        assert account.bonus_tts_chars == 50
        assert await _ledger_rows(db) == []


@pytest.mark.asyncio
async def test_increment_failure_refunds_bonus(monkeypatch):
    async with _session(_anon(1000)) as (db, account):
        monkeypatch.setattr(quota_service, "get_daily_usage", lambda _id: _async(100))
        monkeypatch.setattr(
            quota_service, "check_and_increment_daily_quota", lambda *_a, **_k: _async_false()
        )

        with pytest.raises(HTTPException) as exc_info:
            await quota_service.consume_speech_quota(db, account, 300, 200)

        assert exc_info.value.status_code == 403
        await db.refresh(account)
        assert account.bonus_tts_chars == 1000
        assert await _ledger_rows(db) == []


@pytest.mark.asyncio
async def test_refund_bonus_chars_restores_balance_and_writes_ledger():
    async with _session(_anon(800)) as (db, account):
        await quota_service.refund_bonus_chars(db, account, 200, reference="synthesis-failed")

        await db.refresh(account)
        assert account.bonus_tts_chars == 1000

        rows = await _ledger_rows(db)
        assert len(rows) == 1
        assert rows[0].account_id == "a1"
        assert rows[0].account_type == "anonymous"
        assert rows[0].delta == 200
        assert rows[0].reason == "tts_refund"
        assert rows[0].reference == "synthesis-failed"


@pytest.mark.asyncio
async def test_bonus_applies_to_user_accounts(monkeypatch):
    async with _session(_user(500)) as (db, account):
        monkeypatch.setattr(quota_service, "get_daily_usage", lambda _id: _async(100))
        monkeypatch.setattr(
            quota_service, "check_and_increment_daily_quota", lambda *_a, **_k: _async_true()
        )

        consumed = await quota_service.consume_speech_quota(db, account, 250, 200)

        assert consumed.bonus_used == 150
        await db.refresh(account)
        assert account.bonus_tts_chars == 350

        rows = await _ledger_rows(db)
        assert len(rows) == 1
        assert rows[0].account_type == "user"
        assert rows[0].delta == -150


@pytest.mark.asyncio
async def test_persist_daily_usage_writes_anonymous_table():
    async with _session(_anon(0)) as (db, account):
        await quota_service.persist_daily_usage(db, account, 120)
        await quota_service.persist_daily_usage(db, account, 30)

        anon_rows = (await db.execute(select(AnonymousDailyUsage))).scalars().all()
        assert len(anon_rows) == 1
        assert anon_rows[0].user_id == "a1"
        assert anon_rows[0].tts_characters_used == 150

        user_rows = (await db.execute(select(DailyUsage))).scalars().all()
        assert user_rows == []


@pytest.mark.asyncio
async def test_persist_daily_usage_writes_user_table():
    async with _session(_user(0)) as (db, account):
        await quota_service.persist_daily_usage(db, account, 42)

        user_rows = (await db.execute(select(DailyUsage))).scalars().all()
        assert len(user_rows) == 1
        assert user_rows[0].user_id == "u1"
        assert user_rows[0].tts_characters_used == 42

        anon_rows = (await db.execute(select(AnonymousDailyUsage))).scalars().all()
        assert anon_rows == []


@pytest.mark.asyncio
async def test_speech_failure_refunds_bonus_exactly_once(monkeypatch):
    await init_db()

    async def failing_synth(**_kwargs):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(model_dispatcher, "synthesize", failing_synth)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        email = f"bonus_refund_{uuid.uuid4().hex[:8]}@example.com"
        reg = await client.post(
            "/v1/auth/register",
            headers=API_KEY_HEADER,
            json={"email": email, "password": "password123", "full_name": "Bonus Refund"},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        user_id = reg.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}", **API_KEY_HEADER}

        async with AsyncSessionLocal() as session:
            user = await session.get(User, user_id)
            user.daily_tts_char_limit = 10
            user.bonus_tts_chars = 500
            await session.commit()

        text = f"REFUND_{uuid.uuid4().hex}_" + ("X" * 80)
        expected_bonus = len(text) - 10

        res = await client.post(
            "/v1/audio/speech", headers=headers, json={"voice": "minh_duc", "text": text}
        )
        assert res.status_code == 503

        async with AsyncSessionLocal() as session:
            user = await session.get(User, user_id)
            ledger = (
                await session.execute(
                    select(BonusCharLedger).where(BonusCharLedger.account_id == user_id)
                )
            ).scalars().all()

        assert user.bonus_tts_chars == 500
        assert await get_daily_usage(user_id) == 0
        assert sorted((row.delta, row.reason) for row in ledger) == [
            (-expected_bonus, "tts_spend"),
            (expected_bonus, "tts_refund"),
        ]
