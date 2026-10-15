import socket
from fastapi import APIRouter
from VoiceClonerAppServer.config.settings_manager import settings_manager

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


@router.get("/v1/system/status")
async def system_status():
    settings = settings_manager.get_settings()
    return {
        "server": {
            "host": settings.server.host,
            "port": settings.server.port,
            "lan_ip": get_local_ip(),
        },
        "concurrency": {
            "max_concurrent_tts": settings.concurrency.max_concurrent_tts,
            "max_queue_waiters": settings.concurrency.max_queue_waiters,
        },
        "workers_count": len(settings.model_workers),
        "client_keys_count": len(settings.client_api_keys),
    }
