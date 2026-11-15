"""AdMob rewarded-ad claims and bonus-character crediting.

Rewards are decided server side: the callback's `reward_amount` is ignored and
`settings.ad_rewards.chars_per_ad` is used instead. Every callback inserts a
claim row keyed by `transaction_id`, which makes replays idempotent.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.accounts import ACCOUNT_TYPE_ANONYMOUS
from VoiceClonerAppServer.core.redis_client import BUSINESS_TIMEZONE
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.models.db_models import (
    AdRewardClaim,
    AnonymousUser,
    BonusCharLedger,
    User,
)

logger = logging.getLogger("VoiceClonerAppServer.AdRewardsService")

REASON_AD_REWARD = "ad_reward"


def _account_model(account_type: str):
    return AnonymousUser if account_type == ACCOUNT_TYPE_ANONYMOUS else User


def _business_day_start_utc() -> datetime:
    """Return the start of the current business day as naive UTC.

    `ad_reward_claims.created_at` stores naive UTC timestamps, and the daily
    cap resets with the same business-day boundary as the TTS quota.
    """
    try:
        tz = ZoneInfo(BUSINESS_TIMEZONE)
    except Exception:
        tz = timezone(timedelta(hours=7))
    local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def validate_reward_request(ad_unit: Optional[str]) -> None:
    """Reject reward requests when ads are disabled or the ad unit is not allowed."""
    ads = settings_manager.get_settings().ad_rewards
    if not ads.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ad rewards are disabled",
        )
    if ads.allowed_ad_unit_ids and ad_unit not in ads.allowed_ad_unit_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ad unit is not allowed",
        )


async def _count_credited_claims_today(
    db: AsyncSession, account_id: str, account_type: str
) -> int:
    result = await db.execute(
        select(func.count(AdRewardClaim.transaction_id)).where(
            AdRewardClaim.account_id == account_id,
            AdRewardClaim.account_type == account_type,
            AdRewardClaim.chars_granted > 0,
            AdRewardClaim.created_at >= _business_day_start_utc(),
        )
    )
    return int(result.scalar() or 0)


async def ads_watched_today(
    db: AsyncSession, account_id: str, account_type: str
) -> int:
    """Number of rewarded ads credited to the account in the current day."""
    return await _count_credited_claims_today(db, account_id, account_type)


async def credit_ad_reward(
    db: AsyncSession,
    transaction_id: str,
    account_id: str,
    account_type: str,
    ad_unit: Optional[str],
) -> int:
    """Credit one ad reward and return the characters granted.

    Returns `0` when the transaction was already claimed (replay), when the
    daily cap is reached (the claim is still recorded with `chars_granted=0`)
    or when rewards are disabled. Raises 404 for an unknown account and 403 for
    a disabled/misconfigured reward setup.
    """
    ads = settings_manager.get_settings().ad_rewards
    validate_reward_request(ad_unit)
    chars = ads.chars_per_ad

    claim = AdRewardClaim(
        transaction_id=transaction_id,
        account_id=account_id,
        account_type=account_type,
        chars_granted=0,
        ad_unit=ad_unit,
    )
    db.add(claim)
    try:
        await db.flush()
    except IntegrityError:
        # `transaction_id` is the primary key: another callback already claimed it.
        await db.rollback()
        logger.info("Ignoring replayed ad reward transaction %s", transaction_id)
        return 0

    model = _account_model(account_type)
    account = (
        await db.execute(
            select(model).where(model.id == account_id).with_for_update()
        )
    ).scalar_one_or_none()
    if account is None:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    credited_today = await _count_credited_claims_today(db, account_id, account_type)
    if credited_today >= ads.max_ads_per_user_per_day:
        # Keep the claim (chars_granted stays 0) so AdMob stops retrying.
        await db.commit()
        logger.info(
            "Ad reward cap reached for %s %s (%s/%s today)",
            account_type,
            account_id,
            credited_today,
            ads.max_ads_per_user_per_day,
        )
        return 0

    result = await db.execute(
        update(model)
        .where(model.id == account_id)
        .values(bonus_tts_chars=model.bonus_tts_chars + chars)
    )
    if result.rowcount == 0:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    claim.chars_granted = chars
    db.add(
        BonusCharLedger(
            account_id=account_id,
            account_type=account_type,
            delta=chars,
            reason=REASON_AD_REWARD,
            reference=transaction_id,
        )
    )
    await db.commit()
    logger.info(
        "Credited %s bonus characters to %s %s for transaction %s",
        chars,
        account_type,
        account_id,
        transaction_id,
    )
    return chars
