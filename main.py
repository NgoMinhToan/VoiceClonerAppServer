import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Add VoiceClonerAppServer to sys.path
_SERVER_DIR = Path(__file__).resolve().parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from VoiceClonerAppServer.api.auth import router as auth_router
from VoiceClonerAppServer.api.management import router as management_router
from VoiceClonerAppServer.api.speech import router as speech_router
from VoiceClonerAppServer.api.system import get_local_ip, router as system_router
from VoiceClonerAppServer.api.voices import router as voices_router
from VoiceClonerAppServer.core.database import init_db
from VoiceClonerAppServer.core.redis_client import redis_client
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
from VoiceClonerAppServer.services.voice_service import voice_service

logger = logging.getLogger("VoiceClonerAppServer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = settings_manager.get_settings()
    lan_ip = get_local_ip()

    print("\n" + "=" * 68)
    print(" [VOICE CLONER APP SERVER] API GATEWAY & REVERSE PROXY")
    print(f" * Audio Standard:       48,000 Hz (48kHz WAV Studio Standard)")
    print(f" * Local Gateway URL:    http://localhost:{settings.server.port}")
    print(f" * Real Phone (LAN) URL: http://{lan_ip}:{settings.server.port}")
    print(f" * Dashboard Console:    http://localhost:{settings.server.port}/dashboard")
    print(f" * Swagger API Docs:     http://localhost:{settings.server.port}/docs")
    print(f" * Upstream Workers:     {len(settings.model_workers)}")
    print(f" * Active Client Keys:   {len(settings.client_api_keys)}")
    print("=" * 68 + "\n")

    logger.info("Initializing VoiceClonerAppServer database schema...")
    try:
        await init_db()
    except Exception as e:
        logger.warning(f"Database initialization warning: {e}")

    # Trigger worker capability discovery and voice aggregation on boot
    try:
        discovered_count = await voice_service.discover_all_workers()
        logger.info(f"Discovered and aggregated {discovered_count} voices from upstream workers.")
    except Exception as e:
        logger.warning(f"Worker capability discovery notice on startup: {e}")

    # Register dynamic hook when workers or settings are updated
    settings_manager.register_on_change(lambda _: asyncio.create_task(voice_service.discover_all_workers()))

    yield
    print("\n[VoiceClonerAppServer draining connections and shutting down...]\n")
    logger.info("Draining connections and shutting down VoiceClonerAppServer...")
    await model_dispatcher.aclose()
    await redis_client.aclose()


app = FastAPI(
    title="VoiceClonerAppServer",
    description="API Gateway, Reverse Proxy & Management Server (CLIProxyAPI Pattern)",
    version="1.0.0",
    lifespan=lifespan,
)


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """
    Middleware enabling Chrome/Edge Private Network Access (PNA).
    Permits public HTTPS websites (e.g. https://ngominhtoan.github.io)
    to query local/private network addresses (http://localhost:8000).
    """
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            origin = request.headers.get("origin", "*")
            headers = {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT",
                "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*"),
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Private-Network": "true",
            }
            return Response(status_code=200, headers=headers)

        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


class ApiKeyValidationMiddleware(BaseHTTPMiddleware):
    """
    Validates X-API-Key on /v1/* client endpoints (excluding /v1/management).
    When require_client_api_key is True, all client requests must provide a valid active key.
    Even with a valid User Bearer token, requests without or with an invalid client key are rejected.
    """
    async def dispatch(self, request: Request, call_next):
        if request.method != "OPTIONS" and request.url.path.startswith("/v1/") and not request.url.path.startswith("/v1/management"):
            settings = settings_manager.get_settings()
            x_api_key = request.headers.get("x-api-key") or request.query_params.get("api_key")
            cleaned_key = (x_api_key or "").strip()

            if settings.security.require_client_api_key:
                if not cleaned_key:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Client API key required. Please provide a valid X-API-Key header."},
                        headers={"WWW-Authenticate": "ApiKey"},
                    )
                key_record = settings_manager.find_client_key(cleaned_key)
                if not key_record:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or deactivated client API key."},
                        headers={"WWW-Authenticate": "ApiKey"},
                    )
            else:
                if cleaned_key:
                    key_record = settings_manager.find_client_key(cleaned_key)
                    if not key_record:
                        return JSONResponse(
                            status_code=401,
                            content={"detail": "Invalid or deactivated client API key."},
                            headers={"WWW-Authenticate": "ApiKey"},
                        )
        return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add PrivateNetworkAccessMiddleware after CORSMiddleware so it wraps outer
app.add_middleware(PrivateNetworkAccessMiddleware)
app.add_middleware(ApiKeyValidationMiddleware)


# Static files directory
STATIC_DIR = _SERVER_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
DASHBOARD_DIR = STATIC_DIR / "dashboard"
DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include Routers
app.include_router(auth_router)
app.include_router(management_router)
app.include_router(speech_router)
app.include_router(voices_router)
app.include_router(system_router)


@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    dashboard_index = DASHBOARD_DIR / "index.html"
    if dashboard_index.exists():
        return HTMLResponse(content=dashboard_index.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Voice Cloner Management Console</h1><p>Dashboard UI initializing...</p>")


@app.get("/", response_class=RedirectResponse)
async def root_redirect():
    return RedirectResponse(url="/dashboard")


if __name__ == "__main__":
    import uvicorn
    settings = settings_manager.get_settings()
    uvicorn.run(
        "VoiceClonerAppServer.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=False,
    )
