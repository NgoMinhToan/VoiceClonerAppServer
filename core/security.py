import secrets
from typing import Optional
from fastapi import Header, HTTPException, Query, status

from VoiceClonerAppServer.config.settings_manager import (
    ClientApiKeyConfig,
    settings_manager,
)


async def verify_management_key(
    x_management_key: Optional[str] = Header(None, alias="X-Management-Key"),
    management_key: Optional[str] = Query(None),
) -> str:
    """Verify Management API Key for Admin Console and /v1/management/* endpoints."""
    configured_key = settings_manager.get_settings().security.management_key
    if not configured_key:
        # Open access if admin key is explicitly left blank
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
        # Fallback pseudo client key if auth requirement disabled
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
