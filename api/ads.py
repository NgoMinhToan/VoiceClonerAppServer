"""Rewarded-ad endpoints: AdMob server-side verification and client ad config."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.accounts import (
    ACCOUNT_TYPE_ANONYMOUS,
    ACCOUNT_TYPE_USER,
    Account,
    account_type_of,
    get_account_by_id,
)
from VoiceClonerAppServer.core.database import get_db
from VoiceClonerAppServer.core.security import get_current_user
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.models.schemas import AdsConfigResponse
from VoiceClonerAppServer.services import ad_rewards_service, admob_ssv

logger = logging.getLogger("VoiceClonerAppServer.AdsAPI")

router = APIRouter(tags=["Ads"])

_REQUIRED_SSV_PARAMS = ("transaction_id", "user_id", "signature", "key_id")


async def _resolve_account_across_tables(
    db: AsyncSession, account_id: str
) -> Optional[Account]:
    """Find an account by id without guessing its type from the id itself."""
    account = await get_account_by_id(db, account_id, ACCOUNT_TYPE_USER)
    if account is not None:
        return account
    return await get_account_by_id(db, account_id, ACCOUNT_TYPE_ANONYMOUS)


@router.get("/ads/ssv")
async def admob_ssv_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """AdMob server-side verification callback.

    Lives outside `/v1` because AdMob calls it directly without a client key or
    user JWT. The reward amount is always decided by server settings.
    """
    ads = settings_manager.get_settings().ad_rewards
    if not ads.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ad rewards are disabled",
        )

    params = request.query_params
    missing = [name for name in _REQUIRED_SSV_PARAMS if not params.get(name)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required SSV parameters: {', '.join(missing)}",
        )

    key_id = params["key_id"]
    verifier_url = ads.ssv_verifier_url
    try:
        keys = await admob_ssv.fetch_verifier_keys(verifier_url)
        if key_id not in keys and not admob_ssv.verifier_keys_are_fresh(verifier_url):
            # Only a stale/failed key load may justify another fetch. A fresh
            # set is authoritative for its TTL, so unknown keys fail closed
            # without outbound I/O; forced refreshes are cooldown-limited.
            keys = await admob_ssv.fetch_verifier_keys(
                verifier_url, force_refresh=True
            )
    except Exception as exc:
        logger.error("AdMob verifier keys unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AdMob verifier keys are unavailable",
        )

    if not admob_ssv.verify_signature(
        request.url.query, params["signature"], key_id, keys
    ):
        logger.warning("Rejected AdMob SSV callback with an invalid signature")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid AdMob SSV signature",
        )

    ad_unit = params.get("ad_unit")
    ad_rewards_service.validate_reward_request(ad_unit)

    account = await _resolve_account_across_tables(db, params["user_id"])
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    await ad_rewards_service.credit_ad_reward(
        db,
        params["transaction_id"],
        account.id,
        account_type_of(account),
        ad_unit,
    )
    return PlainTextResponse("OK")


@router.get("/v1/ads/config", response_model=AdsConfigResponse)
async def get_ads_config(
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return rewarded-ad configuration and today's remaining ad allowance."""
    ads = settings_manager.get_settings().ad_rewards
    watched_today = await ad_rewards_service.ads_watched_today(
        db, current_user.id, account_type_of(current_user)
    )
    return AdsConfigResponse(
        enabled=ads.enabled,
        chars_per_ad=ads.chars_per_ad,
        max_ads_per_day=ads.max_ads_per_user_per_day,
        ads_watched_today=watched_today,
        ads_remaining_today=max(0, ads.max_ads_per_user_per_day - watched_today),
        rewarded_ad_unit_id=ads.rewarded_ad_unit_id,
    )
