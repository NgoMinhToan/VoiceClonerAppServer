import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import redis.asyncio as aioredis

logger = logging.getLogger("VoiceClonerAppServer.Redis")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


class InMemoryCache:
    def __init__(self):
        self._data: dict[str, tuple[str, Optional[float]]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            if key not in self._data:
                return None
            val, exp = self._data[key]
            if exp is not None and time.time() > exp:
                del self._data[key]
                return None
            return val

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        async with self._lock:
            exp_time = time.time() + ex if ex is not None else None
            self._data[key] = (str(value), exp_time)
            return True

    async def incrby(self, key: str, amount: int = 1) -> int:
        async with self._lock:
            val = 0
            exp_time = None
            if key in self._data:
                raw, exp = self._data[key]
                if exp is not None and time.time() > exp:
                    del self._data[key]
                else:
                    try:
                        val = int(raw)
                    except ValueError:
                        val = 0
                    exp_time = exp
            new_val = val + amount
            self._data[key] = (str(new_val), exp_time)
            return new_val

    async def expire(self, key: str, ttl: int) -> bool:
        async with self._lock:
            if key in self._data:
                val, _ = self._data[key]
                self._data[key] = (val, time.time() + ttl)
                return True
            return False

    async def delete(self, key: str) -> bool:
        async with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    async def close(self):
        pass


class RedisClient:
    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._fallback: InMemoryCache = InMemoryCache()
        self._is_connected: bool = False
        self._lock = asyncio.Lock()

    async def get_client(self):
        async with self._lock:
            if self._is_connected and self._redis:
                return self._redis
            try:
                client = aioredis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2.0)
                await client.ping()
                self._redis = client
                self._is_connected = True
                return self._redis
            except Exception as e:
                logger.warning(f"Redis unavailable at {REDIS_URL} ({e}). Falling back to InMemoryCache.")
                self._is_connected = False
                return self._fallback

    async def get(self, key: str) -> Optional[str]:
        client = await self.get_client()
        return await client.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        client = await self.get_client()
        return await client.set(key, value, ex=ex)

    async def incrby(self, key: str, amount: int = 1) -> int:
        client = await self.get_client()
        return await client.incrby(key, amount)

    async def expire(self, key: str, ttl: int) -> bool:
        client = await self.get_client()
        return await client.expire(key, ttl)

    async def delete(self, key: str) -> bool:
        client = await self.get_client()
        return await client.delete(key)

    async def aclose(self):
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass


redis_client = RedisClient()
