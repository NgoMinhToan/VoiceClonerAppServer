import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, List, Optional
from fastapi import HTTPException, status

logger = logging.getLogger("VoiceClonerAppServer.ConcurrencyLimiter")


class UserConcurrencyLimiter:
    """
    In-memory async per-user concurrency and queue manager.
    Enforces maximum concurrent inference operations (max_concurrent)
    and maximum waiting queue depth (max_waiters) per user account.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._active: Dict[str, int] = {}
        self._waiters: Dict[str, List[asyncio.Event]] = {}

    def get_active_count(self, user_id: str) -> int:
        return self._active.get(user_id, 0)

    def get_waiters_count(self, user_id: str) -> int:
        return len(self._waiters.get(user_id, []))

    @asynccontextmanager
    async def acquire(
        self,
        user_id: str,
        max_concurrent: int = 1,
        max_waiters: int = 1,
        timeout_sec: float = 60.0,
    ):
        event: Optional[asyncio.Event] = None
        async with self._lock:
            active = self._active.get(user_id, 0)
            waiters = self._waiters.setdefault(user_id, [])

            if active < max_concurrent:
                self._active[user_id] = active + 1
            elif len(waiters) < max_waiters:
                event = asyncio.Event()
                waiters.append(event)
            else:
                logger.warning(
                    f"⛔ [CONCURRENCY REJECTED] user='{user_id}' exceeded queue depth ({len(waiters)}/{max_waiters}) with {active}/{max_concurrent} active jobs"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Hàng đợi xử lý của bạn đã đầy. Vui lòng thử lại sau.",
                )

        if event is not None:
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                async with self._lock:
                    waiters = self._waiters.get(user_id, [])
                    if event in waiters:
                        waiters.remove(event)
                    else:
                        # Event was set just before timeout: transfer token to next waiter or decrement
                        if waiters:
                            next_ev = waiters.pop(0)
                            next_ev.set()
                        else:
                            self._active[user_id] = max(0, self._active.get(user_id, 1) - 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Yêu cầu chờ trong hàng đợi quá lâu. Vui lòng thử lại sau.",
                )

        try:
            yield
        finally:
            async with self._lock:
                waiters = self._waiters.get(user_id, [])
                if waiters:
                    next_event = waiters.pop(0)
                    next_event.set()
                else:
                    self._active[user_id] = max(0, self._active.get(user_id, 1) - 1)
                    if self._active.get(user_id, 0) == 0:
                        self._active.pop(user_id, None)
                        self._waiters.pop(user_id, None)


user_concurrency_limiter = UserConcurrencyLimiter()
