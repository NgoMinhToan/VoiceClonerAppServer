import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select

from VoiceClonerAppServer.core.settings_manager import (
    AppSettings,
    ClientApiKeyConfig,
    ModelWorkerConfig,
    settings_manager,
)
from VoiceClonerAppServer.core.database import AsyncSessionLocal
from VoiceClonerAppServer.core.redis_client import get_daily_usage
from VoiceClonerAppServer.core.security import verify_management_key
from VoiceClonerAppServer.models.db_models import DailyUsage, User, Voice
from VoiceClonerAppServer.services.cache_service import cache_service
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
from VoiceClonerAppServer.services.usage_tracker import usage_tracker

logger = logging.getLogger("VoiceClonerAppServer.ManagementAPI")

router = APIRouter(
    prefix="/v1/management",
    tags=["Management"],
    dependencies=[Depends(verify_management_key)],
)


class CreateClientKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    daily_char_limit: int = Field(50000, ge=1000)


class UpdateSettingsRequest(BaseModel):
    server: Optional[Dict[str, Any]] = None
    security: Optional[Dict[str, Any]] = None
    concurrency: Optional[Dict[str, Any]] = None
    audio_cache: Optional[Dict[str, Any]] = None
    model_workers: Optional[List[Dict[str, Any]]] = None


class TestWorkerRequest(BaseModel):
    url: str
    api_key: Optional[str] = ""


class CreateWorkerRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1)
    api_key: Optional[str] = ""
    weight: int = Field(1, ge=1, le=100)
    is_active: bool = True


class UpdateUserRequest(BaseModel):
    max_cloned_slots: Optional[int] = Field(None, ge=1, le=100)
    full_name: Optional[str] = Field(None, min_length=1, max_length=255)


@router.get("/settings")
async def get_settings() -> Dict[str, Any]:
    """Retrieve active system configuration."""
    settings = settings_manager.get_settings()
    data = settings.model_dump()
    return data


@router.put("/settings")
async def update_settings(payload: UpdateSettingsRequest) -> Dict[str, Any]:
    """Update system configuration in settings.json with immediate hot-reload."""
    updates = payload.model_dump(exclude_unset=True)
    updated = settings_manager.update_settings(updates)
    return updated.model_dump()


@router.get("/keys", response_model=List[ClientApiKeyConfig])
async def list_client_keys():
    """List all configured client API keys."""
    settings = settings_manager.get_settings()
    return settings.client_api_keys


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_client_key(payload: CreateClientKeyRequest) -> Dict[str, Any]:
    """Generate and register a new client API key."""
    created = settings_manager.generate_client_key(
        name=payload.name, daily_char_limit=payload.daily_char_limit
    )
    return created.model_dump()


@router.delete("/keys/{key_id}")
async def revoke_client_key(key_id: str):
    """Revoke and delete a client API key."""
    success = settings_manager.revoke_client_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="API Key not found")
    return {"status": "success", "message": f"Key {key_id} revoked"}


@router.patch("/keys/{key_id}/toggle")
async def toggle_client_key(key_id: str, is_active: bool = Query(...)):
    """Enable or disable a client API key."""
    success = settings_manager.toggle_client_key(key_id, is_active)
    if not success:
        raise HTTPException(status_code=404, detail="API Key not found")
    return {"status": "success", "is_active": is_active}


@router.get("/workers")
async def list_workers():
    """Get status of all configured model inference worker nodes concurrently."""
    settings = settings_manager.get_settings()
    workers = settings.model_workers

    async def _ping_worker(worker):
        ping = await model_dispatcher.ping_worker(worker.url, worker.api_key)
        return {
            "id": worker.id,
            "url": worker.url,
            "api_key_masked": worker.api_key[:4] + "****" if len(worker.api_key) > 4 else ("set" if worker.api_key else "none"),
            "weight": worker.weight,
            "is_active": worker.is_active,
            "telemetry": ping,
        }

    results = await asyncio.gather(*[_ping_worker(w) for w in workers])
    return list(results)


@router.post("/workers/test")
async def test_worker(payload: TestWorkerRequest):
    """Ping test an arbitrary worker URL."""
    res = await model_dispatcher.ping_worker(payload.url, payload.api_key or "")
    return res


@router.post("/workers", status_code=status.HTTP_201_CREATED)
async def create_worker(payload: CreateWorkerRequest):
    """Add a new model inference worker node with URL normalization."""
    settings = settings_manager.get_settings()
    workers = [w.model_dump() for w in settings.model_workers]
    clean_id = payload.id.strip()
    clean_url = payload.url.strip().rstrip("/")
    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Worker URL must start with http:// or https://")
    if any(w["id"] == clean_id for w in workers):
        raise HTTPException(status_code=400, detail="Worker ID already exists")

    new_worker = {
        "id": clean_id,
        "url": clean_url,
        "api_key": (payload.api_key or "").strip(),
        "weight": payload.weight,
        "is_active": payload.is_active,
    }
    workers.append(new_worker)
    settings_manager.update_settings({"model_workers": workers})
    return {"status": "success", "message": f"Worker {clean_id} created"}


@router.delete("/workers/{worker_id}")
async def delete_worker(worker_id: str):
    """Remove a model inference worker node."""
    settings = settings_manager.get_settings()
    workers = [w.model_dump() for w in settings.model_workers]
    new_workers = [w for w in workers if w["id"] != worker_id]
    if len(new_workers) == len(workers):
        raise HTTPException(status_code=404, detail="Worker not found")
    settings_manager.update_settings({"model_workers": new_workers})
    return {"status": "success", "message": f"Worker {worker_id} deleted"}


@router.patch("/workers/{worker_id}/toggle")
async def toggle_worker(worker_id: str, is_active: bool = Query(...)):
    """Enable or disable a model inference worker node."""
    settings = settings_manager.get_settings()
    workers = [w.model_dump() for w in settings.model_workers]
    found = False
    for w in workers:
        if w["id"] == worker_id:
            w["is_active"] = is_active
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Worker not found")
    settings_manager.update_settings({"model_workers": workers})
    return {"status": "success", "worker_id": worker_id, "is_active": is_active}


@router.post("/cache/purge")
async def purge_audio_cache():
    """Purge all cached audio WAV files from disk."""
    deleted_files = cache_service.purge_cache()
    return {
        "status": "success",
        "deleted_files": deleted_files,
        "message": f"Successfully deleted {deleted_files} cache files",
    }


@router.get("/metrics/summary")
async def get_metrics_summary(
    hours: int = Query(24, ge=1, le=720),
    key_name: Optional[str] = Query(None),
):
    """Retrieve aggregated usage metrics and charts data."""
    summary = await usage_tracker.get_metrics_summary(hours=hours, key_name=key_name)
    settings = settings_manager.get_settings()
    summary["active_keys_count"] = len([k for k in settings.client_api_keys if k.is_active])
    summary["active_workers_count"] = len([w for w in settings.model_workers if w.is_active])
    return summary


# ── USER MANAGEMENT ENDPOINTS ──

@router.get("/users")
async def list_users():
    """List registered users with clone slots and today's TTS character usage."""
    today = datetime.now(timezone.utc).date()
    async with AsyncSessionLocal() as session:
        query = select(User).order_by(desc(User.created_at)).limit(100)
        res = await session.execute(query)
        users = res.scalars().all()

        results = []
        for u in users:
            # 1. Query cloned voices count
            cloned_stmt = select(func.count(Voice.id)).where(Voice.user_id == u.id)
            cloned_res = await session.execute(cloned_stmt)
            cloned_count = cloned_res.scalar() or 0

            # 2. Query usage today from Redis (real-time) with DB fallback
            chars_today = await get_daily_usage(u.id)
            if not chars_today:
                usage_query = select(DailyUsage.tts_characters_used).where(
                    DailyUsage.user_id == u.id,
                    DailyUsage.usage_date == today,
                )
                usage_res = await session.execute(usage_query)
                chars_today = usage_res.scalar_one_or_none() or 0

            results.append({
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "auth_provider": u.auth_provider,
                "avatar_url": u.avatar_url,
                "cloned_voices_count": cloned_count,
                "max_cloned_slots": u.max_cloned_slots,
                "tts_chars_today": chars_today,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            })
        return results


@router.patch("/users/{user_id}")
async def update_user(user_id: str, payload: UpdateUserRequest):
    """Update user quota or profile details."""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if payload.max_cloned_slots is not None:
            user.max_cloned_slots = payload.max_cloned_slots
        if payload.full_name is not None:
            user.full_name = payload.full_name

        await session.commit()
        await session.refresh(user)
        return {
            "status": "success",
            "id": user.id,
            "full_name": user.full_name,
            "max_cloned_slots": user.max_cloned_slots,
        }


@router.delete("/users/{user_id}")
async def delete_user(user_id: str):
    """Delete a user account and associated voices."""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        await session.delete(user)
        await session.commit()
        return {"status": "success", "message": f"User {user_id} deleted"}
