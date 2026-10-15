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
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from VoiceClonerAppServer.api.management import router as management_router
from VoiceClonerAppServer.api.speech import router as speech_router
from VoiceClonerAppServer.api.system import router as system_router
from VoiceClonerAppServer.api.voices import router as voices_router
from VoiceClonerAppServer.core.database import init_db
from VoiceClonerAppServer.core.redis_client import redis_client
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher

logger = logging.getLogger("VoiceClonerAppServer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = settings_manager.get_settings()
    logger.info("Initializing VoiceClonerAppServer database schema...")
    try:
        await init_db()
    except Exception as e:
        logger.warning(f"Database initialization warning: {e}")

    logger.info(
        f"VoiceClonerAppServer running on http://{settings.server.host}:{settings.server.port} "
        f"(Workers: {len(settings.model_workers)}, Client Keys: {len(settings.client_api_keys)})"
    )
    yield
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


# Add CORSMiddleware first
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add PrivateNetworkAccessMiddleware after CORSMiddleware so it wraps outer
app.add_middleware(PrivateNetworkAccessMiddleware)

# Static files directory
STATIC_DIR = _SERVER_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
DASHBOARD_DIR = STATIC_DIR / "dashboard"
DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include Routers
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
