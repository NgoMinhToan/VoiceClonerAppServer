"""Token balance endpoint: daily quota usage plus rewarded-ad bonus characters."""

from fastapi import APIRouter, Depends

from VoiceClonerAppServer.core.accounts import (
    ACCOUNT_TYPE_ANONYMOUS,
    Account,
    account_type_of,
)
from VoiceClonerAppServer.core.redis_client import get_daily_usage
from VoiceClonerAppServer.core.security import get_current_user
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.models.schemas import TokensBalanceResponse

router = APIRouter(prefix="/v1/tokens", tags=["Tokens"])


@router.get("/balance", response_model=TokensBalanceResponse)
async def get_balance(account: Account = Depends(get_current_user)):
    """Report remaining daily characters and the bonus balance for the account."""
    settings = settings_manager.get_settings()
    is_anonymous = account_type_of(account) == ACCOUNT_TYPE_ANONYMOUS
    default_limits = (
        settings.default_anonymous_limits if is_anonymous else settings.default_user_limits
    )
    limit = getattr(account, "daily_tts_char_limit", None)
    if limit is None:
        limit = default_limits.daily_tts_char_limit

    used = await get_daily_usage(account.id)
    return TokensBalanceResponse(
        bonus_tts_chars=getattr(account, "bonus_tts_chars", 0) or 0,
        daily_tts_chars_used=used,
        daily_tts_chars_limit=limit,
        daily_tts_chars_remaining=max(0, limit - used),
    )
