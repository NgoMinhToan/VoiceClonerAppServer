"""Dashboard session endpoints: login, status, and logout."""

import os
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from VoiceClonerAppServer.core.redis_client import redis_client
from VoiceClonerAppServer.core.security import (
    DASHBOARD_SESSION_COOKIE,
    DASHBOARD_SESSION_TTL_SECONDS,
    create_dashboard_session_token,
    resolve_client_ip,
    verify_dashboard_session_token,
)
from VoiceClonerAppServer.core.settings_manager import settings_manager

router = APIRouter(prefix="/v1/dashboard", tags=["Dashboard"])

LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300

NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class DashboardLoginRequest(BaseModel):
    key: str = Field(..., min_length=1)


def _cookie_secure() -> bool:
    return os.getenv("DASHBOARD_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


async def _attempts(ip: str) -> int:
    return int(await redis_client.get(f"dashboard_login:{ip}") or 0)


async def _record_failure(ip: str) -> int:
    key = f"dashboard_login:{ip}"
    count = await redis_client.incrby(key, 1)
    if count == 1:
        await redis_client.expire(key, LOGIN_WINDOW_SECONDS)
    return count


@router.post("/session")
async def login(
    payload: DashboardLoginRequest,
    request: Request,
    response: Response,
):
    """Exchange the management key for a signed, HttpOnly session cookie."""
    configured = settings_manager.get_effective_management_key()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Management key is not configured",
            headers=dict(NO_STORE_HEADERS),
        )

    ip = resolve_client_ip(request)
    if await _attempts(ip) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts",
            headers={**NO_STORE_HEADERS, "Retry-After": str(LOGIN_WINDOW_SECONDS)},
        )

    # Compare UTF-8 bytes so non-ASCII input yields 401 instead of TypeError.
    if not secrets.compare_digest(
        payload.key.encode("utf-8"), configured.encode("utf-8")
    ):
        await _record_failure(ip)
        raise HTTPException(
            status_code=401,
            detail="Invalid management key",
            headers=dict(NO_STORE_HEADERS),
        )

    await redis_client.delete(f"dashboard_login:{ip}")
    token = create_dashboard_session_token(configured)
    response.set_cookie(
        DASHBOARD_SESSION_COOKIE,
        token,
        max_age=DASHBOARD_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(),
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, "expires_in": DASHBOARD_SESSION_TTL_SECONDS}


@router.get("/session")
async def status(request: Request, response: Response):
    """Report whether the caller holds a valid dashboard session."""
    configured = settings_manager.get_effective_management_key()
    token = request.cookies.get(DASHBOARD_SESSION_COOKIE, "")
    if not configured or not verify_dashboard_session_token(token, configured):
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers=dict(NO_STORE_HEADERS),
        )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True}


@router.delete("/session")
async def logout():
    """Clear the dashboard session cookie."""
    out = Response(status_code=204, headers=dict(NO_STORE_HEADERS))
    out.delete_cookie(DASHBOARD_SESSION_COOKIE, path="/")
    return out
