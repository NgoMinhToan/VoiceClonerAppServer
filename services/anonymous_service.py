import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.redis_client import (
    get_current_business_date,
    redis_client,
)
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.models.db_models import AnonymousUser

logger = logging.getLogger("VoiceClonerAppServer.AnonymousService")

SIGNUP_RATE_LIMIT_TTL_SECONDS = 86400


def signup_rate_key(client_ip: str, business_date: str | None = None) -> str:
    """Redis key holding the number of anonymous accounts created by an IP today."""
    return f"anon_signup:{client_ip}:{business_date or get_current_business_date()}"


async def get_or_create_anonymous_user(
    db: AsyncSession, install_id: str, client_ip: str
) -> tuple[AnonymousUser, bool]:
    """Resolve an anonymous account by `install_id`, creating it on first sight.

    Returns `(account, created)`. Existing installs are always accepted without
    counting against the per-IP daily signup rate limit. New installs are
    counted first and rejected with HTTP 429 once the business-day limit is hit.
    """
    result = await db.execute(
        select(AnonymousUser).where(AnonymousUser.install_id == install_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing, False

    settings = settings_manager.get_settings()
    signup_limit = settings.security.anonymous_signup_per_ip_per_day
    key = signup_rate_key(client_ip)
    signup_count = await redis_client.incrby(key, 1)
    if signup_count == 1:
        await redis_client.expire(key, SIGNUP_RATE_LIMIT_TTL_SECONDS)
    if signup_count > signup_limit:
        logger.warning(
            "Anonymous signup rate limit hit for IP %s (%s/%s today)",
            client_ip,
            signup_count,
            signup_limit,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Đã đạt giới hạn tạo tài khoản khách cho IP này hôm nay. Vui lòng thử lại sau.",
        )

    limits = settings.default_anonymous_limits
    user = AnonymousUser(
        id=str(uuid.uuid4()),
        install_id=install_id,
        email=f"anon+{install_id}@anonymous.local",
        password_hash=None,
        auth_provider="anonymous",
        full_name="Khách",
        max_cloned_slots=0,
        daily_tts_char_limit=limits.daily_tts_char_limit,
        rate_limit_requests_per_minute=limits.rate_limit_requests_per_minute,
        max_speech_char_limit=limits.max_speech_char_limit,
        max_preview_char_limit=limits.max_preview_char_limit,
        max_concurrent_tts=limits.max_concurrent_tts,
        max_queue_waiters=limits.max_queue_waiters,
        bonus_tts_chars=0,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Created anonymous account %s for install %s", user.id, install_id)
    return user, True
