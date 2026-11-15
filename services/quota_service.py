"""Daily quota and bonus-character accounting for the speech and preview endpoints."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.accounts import (
    ACCOUNT_TYPE_ANONYMOUS,
    Account,
    account_type_of,
)
from VoiceClonerAppServer.core.redis_client import (
    check_and_increment_daily_quota,
    get_daily_usage,
)
from VoiceClonerAppServer.models.db_models import (
    AnonymousDailyUsage,
    AnonymousUser,
    BonusCharLedger,
    DailyUsage,
    User,
    utc_now,
)

logger = logging.getLogger("VoiceClonerAppServer.QuotaService")

REASON_TTS_SPEND = "tts_spend"
REASON_TTS_REFUND = "tts_refund"


@dataclass
class QuotaConsumption:
    bonus_used: int = 0


def _account_model(account_type: str):
    return AnonymousUser if account_type == ACCOUNT_TYPE_ANONYMOUS else User


def _usage_model(account_type: str):
    return AnonymousDailyUsage if account_type == ACCOUNT_TYPE_ANONYMOUS else DailyUsage


def _exceeded_detail(remaining: int, balance: int, char_count: int) -> str:
    return (
        f"Hạn mức ký tự còn lại hôm nay không đủ ({remaining:,} ký tự còn lại, "
        f"{balance:,} ký tự thưởng, yêu cầu {char_count:,} ký tự). "
        "Hạn mức sẽ được làm mới vào 00:00. "
        "Xem một quảng cáo ngắn để nhận thêm ký tự thưởng."
    )


async def _bonus_balance(db: AsyncSession, model, account_id: str) -> int:
    result = await db.execute(select(model.bonus_tts_chars).where(model.id == account_id))
    return result.scalar_one_or_none() or 0


async def consume_speech_quota(
    db: AsyncSession, account: Account, char_count: int, daily_limit: int
) -> QuotaConsumption:
    """Charge `char_count` against the daily quota, spilling into bonus characters.

    The bonus part of the charge is debited with a conditional ``UPDATE`` so two
    concurrent requests can never take the balance below zero. When the daily
    Redis increment fails afterwards, the pending debit is rolled back and a 403
    is raised.
    """
    account_id = account.id
    account_type = account_type_of(account)
    model = _account_model(account_type)

    used = await get_daily_usage(account_id)
    remaining = max(0, daily_limit - used)
    needed_bonus = max(0, char_count - remaining)
    daily_charge = min(char_count, remaining)

    if needed_bonus > 0:
        debit = (
            update(model)
            .where(model.id == account_id, model.bonus_tts_chars >= needed_bonus)
            .values(bonus_tts_chars=model.bonus_tts_chars - needed_bonus)
            .execution_options(synchronize_session=False)
        )
        result = await db.execute(debit)
        if result.rowcount != 1:
            balance = await _bonus_balance(db, model, account_id)
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=_exceeded_detail(remaining, balance, char_count),
            )

    incremented = await check_and_increment_daily_quota(
        account_id, daily_charge, limit=daily_limit
    )
    if not incremented:
        # The bonus debit has not been committed, so rolling back is the refund.
        await db.rollback()
        balance = await _bonus_balance(db, model, account_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_exceeded_detail(remaining, balance, char_count),
        )

    if needed_bonus > 0:
        db.add(
            BonusCharLedger(
                account_id=account_id,
                account_type=account_type,
                delta=-needed_bonus,
                reason=REASON_TTS_SPEND,
            )
        )
        await db.commit()
        return QuotaConsumption(bonus_used=needed_bonus)

    return QuotaConsumption(bonus_used=0)


async def refund_bonus_chars(
    db: AsyncSession,
    account: Account,
    amount: int,
    reference: str | None = None,
) -> None:
    """Give `amount` bonus characters back and record the refund in the ledger."""
    if amount <= 0:
        return

    account_id = account.id
    account_type = account_type_of(account)
    model = _account_model(account_type)

    try:
        await db.execute(
            update(model)
            .where(model.id == account_id)
            .values(bonus_tts_chars=model.bonus_tts_chars + amount)
            .execution_options(synchronize_session=False)
        )
        db.add(
            BonusCharLedger(
                account_id=account_id,
                account_type=account_type,
                delta=amount,
                reason=REASON_TTS_REFUND,
                reference=reference,
            )
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error(
            "Could not refund %s bonus characters for %s:%s: %s",
            amount,
            account_type,
            account_id,
            exc,
        )


async def persist_daily_usage(
    db: AsyncSession, account: Account, char_count: int
) -> None:
    """Persist today's character usage for the account's own usage table."""
    account_id = account.id
    model = _usage_model(account_type_of(account))

    try:
        today = datetime.now(timezone.utc).date()
        usage_res = await db.execute(
            select(model).where(model.user_id == account_id, model.usage_date == today)
        )
        record = usage_res.scalar_one_or_none()
        if record:
            record.tts_characters_used += char_count
            record.updated_at = utc_now()
        else:
            db.add(
                model(
                    user_id=account_id,
                    usage_date=today,
                    tts_characters_used=char_count,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning("Could not persist daily usage for %s: %s", account_id, exc)
