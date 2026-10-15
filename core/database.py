import logging
import os
from pathlib import Path
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from VoiceClonerAppServer.models.db_models import Base

logger = logging.getLogger("VoiceClonerAppServer.Database")

DATA_DIR = Path(__file__).resolve().parent.parent / "storage"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SQLITE_DEFAULT = f"sqlite+aiosqlite:///{DATA_DIR / 'voicecloner.db'}"

DATABASE_URL = os.getenv("DATABASE_URL", SQLITE_DEFAULT)


def _build_engine(url: str):
    if url.startswith("sqlite"):
        if "///" in url and not url.startswith("sqlite+aiosqlite:///:memory:"):
            raw_path = url.split("///", 1)[1]
            if "?" in raw_path:
                raw_path = raw_path.split("?", 1)[0]
            db_dir = Path(raw_path).parent
            if str(db_dir) not in ("", "."):
                db_dir.mkdir(parents=True, exist_ok=True)
        return create_async_engine(url, echo=False)
    return create_async_engine(url, echo=False, pool_pre_ping=True)


engine = _build_engine(DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    global engine
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info(f"Database schema initialized via {engine.url}")
    except Exception as e:
        if not str(engine.url).startswith("sqlite"):
            logger.warning(f"Failed to connect to primary DB ({e}). Falling back to SQLite.")
            try:
                await engine.dispose()
            except Exception:
                pass
            engine = _build_engine(SQLITE_DEFAULT)
            AsyncSessionLocal.configure(bind=engine)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database schema initialized with SQLite fallback.")
        else:
            logger.error(f"Failed to initialize database: {e}")
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
