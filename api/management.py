import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from VoiceClonerAppServer.config.settings_manager import (
    AppSettings,
    ClientApiKeyConfig,
    ModelWorkerConfig,
    settings_manager,
)
from VoiceClonerAppServer.core.security import verify_management_key
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
    """Get status of all configured model inference worker nodes."""
    settings = settings_manager.get_settings()
    results = []
    for worker in settings.model_workers:
        ping = await model_dispatcher.ping_worker(worker.url, worker.api_key)
        results.append({
            "id": worker.id,
            "url": worker.url,
            "api_key_masked": worker.api_key[:4] + "****" if len(worker.api_key) > 4 else ("set" if worker.api_key else "none"),
            "weight": worker.weight,
            "is_active": worker.is_active,
            "telemetry": ping,
        })
    return results


@router.post("/workers/test")
async def test_worker(payload: TestWorkerRequest):
    """Ping test an arbitrary worker URL."""
    res = await model_dispatcher.ping_worker(payload.url, payload.api_key or "")
    return res


@router.get("/metrics/summary")
async def get_metrics_summary(hours: int = Query(24, ge=1, le=168)):
    """Retrieve aggregated usage metrics and charts data."""
    summary = await usage_tracker.get_metrics_summary(hours=hours)
    settings = settings_manager.get_settings()
    summary["active_keys_count"] = len([k for k in settings.client_api_keys if k.is_active])
    summary["active_workers_count"] = len([w for w in settings.model_workers if w.is_active])
    return summary
