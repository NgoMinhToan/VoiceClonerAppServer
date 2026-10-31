import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.database import get_db
from VoiceClonerAppServer.core.redis_client import (
    blacklist_token,
    get_daily_usage,
)
from VoiceClonerAppServer.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    oauth2_scheme,
    verify_google_id_token,
    verify_password,
)
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.models.db_models import User, Voice
from VoiceClonerAppServer.models.schemas import (
    GoogleAuthRequest,
    LoginRequest,
    LogoutRequest,
    RefreshTokenResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserProfileResponse,
    UserResponse,
)

logger = logging.getLogger("VoiceClonerAppServer.AuthAPI")

router = APIRouter(prefix="/v1/auth", tags=["Authentication"])


def build_user_response(user: User, cloned_count: int = 0) -> UserResponse:
    default_limits = settings_manager.get_settings().default_user_limits
    daily_limit = getattr(user, "daily_tts_char_limit", None) or default_limits.daily_tts_char_limit
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        auth_provider=user.auth_provider,
        max_cloned_slots=getattr(user, "max_cloned_slots", None) or default_limits.max_cloned_slots,
        cloned_voices_count=cloned_count,
        daily_tts_char_limit=daily_limit,
        daily_tts_chars_limit=daily_limit,
        rate_limit_requests_per_minute=getattr(user, "rate_limit_requests_per_minute", None) or default_limits.rate_limit_requests_per_minute,
        max_speech_char_limit=getattr(user, "max_speech_char_limit", None) or default_limits.max_speech_char_limit,
        max_preview_char_limit=getattr(user, "max_preview_char_limit", None) or default_limits.max_preview_char_limit,
        max_concurrent_tts=getattr(user, "max_concurrent_tts", None) or default_limits.max_concurrent_tts,
        max_queue_waiters=getattr(user, "max_queue_waiters", None) or default_limits.max_queue_waiters,
    )


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """User registration with email and password."""
    stmt = select(User).where(User.email == req.email)
    res = await db.execute(stmt)
    if res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email đã được đăng ký",
        )

    user_id = str(uuid.uuid4())
    default_limits = settings_manager.get_settings().default_user_limits
    user = User(
        id=user_id,
        email=req.email,
        password_hash=hash_password(req.password),
        full_name=req.full_name.strip(),
        auth_provider="local",
        max_cloned_slots=default_limits.max_cloned_slots,
        daily_tts_char_limit=default_limits.daily_tts_char_limit,
        rate_limit_requests_per_minute=default_limits.rate_limit_requests_per_minute,
        max_speech_char_limit=default_limits.max_speech_char_limit,
        max_preview_char_limit=default_limits.max_preview_char_limit,
        max_concurrent_tts=default_limits.max_concurrent_tts,
        max_queue_waiters=default_limits.max_queue_waiters,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    user_resp = build_user_response(user, cloned_count=0)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=user_resp,
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """User authentication with email and password."""
    email = req.email.strip().lower()
    stmt = select(User).where(User.email == email)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không chính xác",
        )

    # Count cloned voices
    count_stmt = select(func.count(Voice.id)).where(Voice.user_id == user.id)
    count_res = await db.execute(count_stmt)
    cloned_count = count_res.scalar() or 0

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    user_resp = build_user_response(user, cloned_count=cloned_count)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=user_resp,
    )


@router.post("/google", response_model=TokenResponse)
async def google_auth(req: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    """OAuth2 Google ID Token verification and account link/creation."""
    id_info = verify_google_id_token(req.id_token)
    if not id_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google token không hợp lệ",
        )

    email = id_info.get("email", "").strip().lower()
    full_name = id_info.get("name", email.split("@")[0])
    avatar_url = id_info.get("picture")

    stmt = select(User).where(User.email == email)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user:
        default_limits = settings_manager.get_settings().default_user_limits
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=None,
            full_name=full_name,
            avatar_url=avatar_url,
            auth_provider="google",
            max_cloned_slots=default_limits.max_cloned_slots,
            daily_tts_char_limit=default_limits.daily_tts_char_limit,
            rate_limit_requests_per_minute=default_limits.rate_limit_requests_per_minute,
            max_speech_char_limit=default_limits.max_speech_char_limit,
            max_preview_char_limit=default_limits.max_preview_char_limit,
            max_concurrent_tts=default_limits.max_concurrent_tts,
            max_queue_waiters=default_limits.max_queue_waiters,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    count_stmt = select(func.count(Voice.id)).where(Voice.user_id == user.id)
    count_res = await db.execute(count_stmt)
    cloned_count = count_res.scalar() or 0

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    user_resp = build_user_response(user, cloned_count=cloned_count)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=user_resp,
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Issue a new access token using a valid refresh token."""
    try:
        payload = decode_token(req.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ")
        user_id = payload.get("sub")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ hoặc đã hết hạn")

    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Người dùng không tồn tại")

    new_access_token = create_access_token(user.id)
    return RefreshTokenResponse(access_token=new_access_token, token_type="bearer")


@router.post("/logout")
async def logout(
    req: LogoutRequest = LogoutRequest(),
    token: Optional[str] = Depends(oauth2_scheme),
):
    """Revoke active JWT tokens by blacklisting in Redis."""
    if token:
        await blacklist_token(token, ttl_seconds=86400)
    if req.refresh_token:
        await blacklist_token(req.refresh_token, ttl_seconds=7 * 86400)
    return {"status": "success", "message": "Đăng xuất thành công"}


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve currently authenticated user profile, quota usage, and clone slots."""
    count_stmt = select(func.count(Voice.id)).where(Voice.user_id == current_user.id)
    count_res = await db.execute(count_stmt)
    cloned_count = count_res.scalar() or 0

    daily_used = await get_daily_usage(current_user.id)
    default_limits = settings_manager.get_settings().default_user_limits
    daily_limit = getattr(current_user, "daily_tts_char_limit", None) or default_limits.daily_tts_char_limit

    return UserProfileResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        avatar_url=current_user.avatar_url,
        auth_provider=current_user.auth_provider,
        cloned_voices_count=cloned_count,
        max_cloned_slots=getattr(current_user, "max_cloned_slots", None) or default_limits.max_cloned_slots,
        daily_tts_chars_used=daily_used,
        daily_tts_chars_limit=daily_limit,
        daily_tts_char_limit=daily_limit,
        rate_limit_requests_per_minute=getattr(current_user, "rate_limit_requests_per_minute", None) or default_limits.rate_limit_requests_per_minute,
        max_speech_char_limit=getattr(current_user, "max_speech_char_limit", None) or default_limits.max_speech_char_limit,
        max_preview_char_limit=getattr(current_user, "max_preview_char_limit", None) or default_limits.max_preview_char_limit,
        max_concurrent_tts=getattr(current_user, "max_concurrent_tts", None) or default_limits.max_concurrent_tts,
        max_queue_waiters=getattr(current_user, "max_queue_waiters", None) or default_limits.max_queue_waiters,
    )
