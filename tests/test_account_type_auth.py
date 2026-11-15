import uuid
from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from VoiceClonerAppServer.api.auth import refresh_token
from VoiceClonerAppServer.core import security as security_module
from VoiceClonerAppServer.core.accounts import Account, account_type_of, get_account_by_id
from VoiceClonerAppServer.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)
from VoiceClonerAppServer.models.db_models import AnonymousUser, Base, User
from VoiceClonerAppServer.models.schemas import RefreshRequest

JWT_SECRET_KEY = security_module.JWT_SECRET_KEY
JWT_ALGORITHM = security_module.JWT_ALGORITHM


def _make_user() -> User:
    user_id = str(uuid.uuid4())
    return User(id=user_id, email=f"{user_id}@example.com", full_name="Normal User")


def _make_anonymous_user() -> AnonymousUser:
    account_id = str(uuid.uuid4())
    return AnonymousUser(
        id=account_id,
        install_id=str(uuid.uuid4()),
        email=f"anon+{account_id}@anonymous.local",
        full_name="Khách",
    )


def _legacy_token(account_id: str, token_type: str) -> str:
    """Build a pre-`acct` token exactly like the old implementation did."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": account_id,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def _not_blacklisted(token: str) -> bool:
    return False


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _disable_token_blacklist(monkeypatch):
    monkeypatch.setattr("VoiceClonerAppServer.core.redis_client.is_token_blacklisted", _not_blacklisted)


def test_tokens_carry_account_type():
    token = create_access_token("acc-1", account_type="anonymous")
    assert decode_token(token)["acct"] == "anonymous"
    assert decode_token(create_access_token("acc-2"))["acct"] == "user"


def test_refresh_tokens_carry_account_type():
    token = create_refresh_token("acc-1", account_type="anonymous")
    payload = decode_token(token)
    assert payload["type"] == "refresh"
    assert payload["acct"] == "anonymous"
    assert decode_token(create_refresh_token("acc-2"))["acct"] == "user"


def test_account_union_covers_both_models():
    assert set(get_args(Account)) == {User, AnonymousUser}


def test_account_type_of_distinguishes_models():
    assert account_type_of(_make_anonymous_user()) == "anonymous"
    assert account_type_of(_make_user()) == "user"


@pytest.mark.asyncio
async def test_get_account_by_id_queries_the_requested_table(db_session):
    user = _make_user()
    anonymous = _make_anonymous_user()
    db_session.add_all([user, anonymous])
    await db_session.commit()

    assert isinstance(await get_account_by_id(db_session, user.id, "user"), User)
    assert isinstance(await get_account_by_id(db_session, anonymous.id, "anonymous"), AnonymousUser)
    # IDs live in disjoint tables, so a type/id mismatch resolves to nothing.
    assert await get_account_by_id(db_session, anonymous.id, "user") is None
    assert await get_account_by_id(db_session, user.id, "anonymous") is None


@pytest.mark.asyncio
async def test_get_current_user_resolves_anonymous_token(db_session):
    anonymous = _make_anonymous_user()
    db_session.add(anonymous)
    await db_session.commit()

    token = create_access_token(anonymous.id, account_type="anonymous")
    account = await get_current_user(token=token, db=db_session)

    assert isinstance(account, AnonymousUser)
    assert account.id == anonymous.id


@pytest.mark.asyncio
async def test_legacy_token_without_acct_resolves_as_user(db_session):
    user = _make_user()
    db_session.add(user)
    await db_session.commit()

    account = await get_current_user(token=_legacy_token(user.id, "access"), db=db_session)

    assert isinstance(account, User)
    assert account.id == user.id


@pytest.mark.asyncio
async def test_refresh_preserves_anonymous_account_type(db_session):
    anonymous = _make_anonymous_user()
    db_session.add(anonymous)
    await db_session.commit()

    refresh = create_refresh_token(anonymous.id, account_type="anonymous")
    response = await refresh_token(RefreshRequest(refresh_token=refresh), db=db_session)

    assert decode_token(response.access_token)["acct"] == "anonymous"


@pytest.mark.asyncio
async def test_refresh_legacy_token_defaults_to_user(db_session):
    user = _make_user()
    db_session.add(user)
    await db_session.commit()

    response = await refresh_token(
        RefreshRequest(refresh_token=_legacy_token(user.id, "refresh")), db=db_session
    )

    assert decode_token(response.access_token)["acct"] == "user"
