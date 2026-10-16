import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.database import get_db
from VoiceClonerAppServer.core.settings_manager import (
    ClientApiKeyConfig,
    settings_manager,
)
from VoiceClonerAppServer.models.db_models import User

# Passlib compatibility fix for bcrypt >= 4.0.0
if not hasattr(bcrypt, "__about__"):
    bcrypt.__about__ = type("About", (), {"__version__": bcrypt.__version__})

if not getattr(bcrypt, "_passlib_patched", False):
    _orig_hashpw = bcrypt.hashpw

    def _patched_hashpw(password, salt):
        if len(password) > 72:
            password = password[:72]
        return _orig_hashpw(password, salt)

    bcrypt.hashpw = _patched_hashpw
    bcrypt._passlib_patched = True

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login", auto_error=False)

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev_secret_key_please_change_in_production_min_64_chars_length")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode = {
        "sub": subject,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode = {
        "sub": subject,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])


def verify_google_id_token(token_str: str) -> Optional[Dict[str, Any]]:
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
        audience = GOOGLE_CLIENT_ID if GOOGLE_CLIENT_ID else None
        id_info = id_token.verify_oauth2_token(
            token_str,
            google_requests.Request(),
            audience=audience,
        )
        return id_info
    except Exception:
        return None


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency: Extract and validate user from JWT Bearer token."""
    from VoiceClonerAppServer.core.redis_client import is_token_blacklisted

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu xác thực",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if await is_token_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token đã bị vô hiệu hóa",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ")
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ")
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token không hợp lệ hoặc đã hết hạn",
            headers={"WWW-Authenticate": "Bearer"},
        )

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Người dùng không tồn tại")
    return user


async def get_optional_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Dependency: Extract user if token present and valid; return None if unauthenticated."""
    if not token:
        return None
    try:
        return await get_current_user(token=token, db=db)
    except HTTPException:
        return None


async def verify_management_key(
    x_management_key: Optional[str] = Header(None, alias="X-Management-Key"),
    management_key: Optional[str] = Query(None),
) -> str:
    """Verify Management API Key for Admin Console and /v1/management/* endpoints."""
    configured_key = settings_manager.get_settings().security.management_key
    if not configured_key:
        return ""

    provided_key = (x_management_key or management_key or "").strip()
    if not provided_key or not secrets.compare_digest(provided_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing management key",
            headers={"WWW-Authenticate": "ManagementKey"},
        )
    return provided_key


async def verify_client_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(None),
) -> ClientApiKeyConfig:
    """Verify Client API Key for public services (Android app, Web Demo)."""
    settings = settings_manager.get_settings()
    if not settings.security.require_client_api_key:
        return ClientApiKeyConfig(
            id="key_unrestricted",
            name="Unrestricted Client",
            key="unrestricted",
            is_active=True,
            daily_char_limit=1000000,
        )

    provided = (x_api_key or api_key or "").strip()
    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Client API key required. Please provide a valid X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    key_record = settings_manager.find_client_key(provided)
    if not key_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or deactivated client API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return key_record
