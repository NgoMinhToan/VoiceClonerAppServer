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
BUSINESS_TIMEZONE = os.getenv("BUSINESS_TIMEZONE", "Asia/Ho_Chi_Minh")
RATE_LIMIT_REQUESTS_PER_MINUTE = int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "60"))
DAILY_TTS_CHAR_LIMIT = int(os.getenv("DAILY_TTS_CHAR_LIMIT", "100000"))


def get_current_business_date() -> str:
    """Return current date formatted as YYYY-MM-DD in the business timezone."""
    try:
        tz = ZoneInfo(BUSINESS_TIMEZONE)
    except (ZoneInfoNotFoundError, Exception):
        tz = timezone(timedelta(hours=7))
    return datetime.now(tz).strftime("%Y-%m-%d")


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

    async def exists(self, key: str) -> bool:
        val = await self.get(key)
        return val is not None

    async def check_and_increment_quota(self, key: str, char_count: int, limit: int, ttl: int = 172800) -> bool:
        async with self._lock:
            current = 0
            if key in self._data:
                raw, exp = self._data[key]
                if exp is None or time.time() <= exp:
                    try:
                        current = int(raw)
                    except ValueError:
                        current = 0
            if current + char_count > limit:
                return False
            new_val = current + char_count
            self._data[key] = (str(new_val), time.time() + ttl)
            return True


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
        try:
            client = await self.get_client()
            return await client.get(key)
        except Exception:
            self._is_connected = False
            self._redis = None
            return await self._fallback.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        try:
            client = await self.get_client()
            return await client.set(key, value, ex=ex)
        except Exception:
            self._is_connected = False
            self._redis = None
            return await self._fallback.set(key, value, ex=ex)

    async def incrby(self, key: str, amount: int = 1) -> int:
        try:
            client = await self.get_client()
            return await client.incrby(key, amount)
        except Exception:
            self._is_connected = False
            self._redis = None
            return await self._fallback.incrby(key, amount)

    async def expire(self, key: str, ttl: int) -> bool:
        try:
            client = await self.get_client()
            return await client.expire(key, ttl)
        except Exception:
            self._is_connected = False
            self._redis = None
            return await self._fallback.expire(key, ttl)

    async def delete(self, key: str) -> bool:
        try:
            client = await self.get_client()
            return await client.delete(key)
        except Exception:
            self._is_connected = False
            self._redis = None
            return await self._fallback.delete(key)

    async def exists(self, key: str) -> bool:
        try:
            client = await self.get_client()
            return bool(await client.exists(key))
        except Exception:
            self._is_connected = False
            self._redis = None
            return bool(await self._fallback.exists(key))

    async def aclose(self):
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass


redis_client = RedisClient()


CHECK_AND_INCR_QUOTA_LUA = """
local current = redis.call('GET', KEYS[1])
local current_num = current and tonumber(current) or 0
if current_num + tonumber(ARGV[1]) > tonumber(ARGV[2]) then
    return 0
else
    redis.call('INCRBY', KEYS[1], ARGV[1])
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    return 1
end
"""


async def check_and_increment_daily_quota(user_id: str, char_count: int, limit: int = DAILY_TTS_CHAR_LIMIT) -> bool:
    """Check user daily quota and increment if allowed."""
    key = f"user:{user_id}:chars:{get_current_business_date()}"
    client = await redis_client.get_client()
    if not isinstance(client, InMemoryCache):
        try:
            res = await client.eval(CHECK_AND_INCR_QUOTA_LUA, 1, key, char_count, limit, 172800)
            return int(res) == 1
        except Exception:
            pass
    return await redis_client._fallback.check_and_increment_quota(key, char_count, limit, 172800)


async def rollback_daily_quota(user_id: str, char_count: int) -> None:
    """Refund daily character usage upon synthesis failure."""
    if char_count <= 0:
        return
    key = f"user:{user_id}:chars:{get_current_business_date()}"
    try:
        val = await redis_client.get(key)
        if val:
            new_val = max(0, int(val) - char_count)
            await redis_client.set(key, new_val, ex=172800)
    except Exception as e:
        logger.warning(f"Error rolling back daily quota: {e}")


async def get_daily_usage(user_id: str) -> int:
    """Get character count used by user today."""
    key = f"user:{user_id}:chars:{get_current_business_date()}"
    try:
        val = await redis_client.get(key)
        return int(val) if val else 0
    except Exception:
        return 0


async def check_rate_limit(user_id: str, max_requests: int = RATE_LIMIT_REQUESTS_PER_MINUTE) -> bool:
    """Enforce per-minute sliding window rate limiting."""
    key = f"ratelimit:{user_id}:{int(time.time() // 60)}"
    try:
        count = await redis_client.incrby(key, 1)
        if count == 1:
            await redis_client.expire(key, 70)
        return count <= max_requests
    except Exception:
        return True


async def blacklist_token(token: str, ttl_seconds: int = 86400) -> None:
    """Revoke JWT token by storing in blacklist (both in-memory fallback and redis)."""
    key = f"bl:{token}"
    await redis_client._fallback.set(key, "1", ex=ttl_seconds)
    try:
        await redis_client.set(key, "1", ex=ttl_seconds)
    except Exception:
        pass


async def is_token_blacklisted(token: str) -> bool:
    """Check if token is revoked."""
    key = f"bl:{token}"
    if await redis_client._fallback.exists(key):
        return True
    try:
        return await redis_client.exists(key)
    except Exception:
        return False
