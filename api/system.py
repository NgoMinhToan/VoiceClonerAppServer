import os
import socket
from fastapi import APIRouter
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.models.schemas import (
    SystemBenchmarks,
    SystemHardwareInfo,
    SystemStatusResponse,
)

router = APIRouter(tags=["System"])


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@router.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "VoiceClonerAppServer",
        "version": "1.0.0",
    }


@router.get("/v1/system/status", response_model=SystemStatusResponse)
async def system_status():
    """System hardware and benchmark metrics aligned with Android and Web clients."""
    settings = settings_manager.get_settings()
    workers_count = len(settings.model_workers)

    return SystemStatusResponse(
        status="online",
        engine="VieNeu-TTS-v3-Turbo",
        backend=f"ONNX Runtime ({workers_count} Model Worker{'s' if workers_count != 1 else ''})",
        sample_rate=48000,
        hardware=SystemHardwareInfo(
            device_type="CPU Cluster",
            device_name=f"Host: {get_local_ip()}:{settings.server.port}",
            threads=settings.concurrency.max_concurrent_tts * 4,
            memory_used_mb=512,
            temperature_celsius=42,
        ),
        benchmarks=SystemBenchmarks(
            avg_ttfa_ms=120,
            active_streams=0,
            max_concurrent_streams=settings.concurrency.max_concurrent_tts,
        ),
    )
