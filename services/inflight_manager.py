import asyncio
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger("VoiceClonerAppServer.InFlightManager")


class InFlightTask:
    """
    Encapsulates state for an active in-flight TTS synthesis job.
    Supports singleflight deduplication across concurrent duplicate requests.
    """

    def __init__(self, cache_key: str, initiator_user_id: str, char_count: int):
        self.cache_key = cache_key
        self.initiator_user_id = initiator_user_id
        self.char_count = char_count
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        self.future: asyncio.Future = loop.create_future()
        self.initiator_paid: bool = False
        self.is_billed: bool = False
        self.claim_lock = asyncio.Lock()


class InFlightManager:
    """
    Coordinates concurrent synthesis requests for identical cache keys (SingleFlight pattern).
    Prevents duplicate upstream inference and manages handoff for unbilled cache lifecycle.
    """

    def __init__(self):
        self._tasks: Dict[str, InFlightTask] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self, cache_key: str, user_id: str, char_count: int
    ) -> Tuple[InFlightTask, bool]:
        """
        Retrieve an existing in-flight task for cache_key or register a new one.
        Returns (task, is_creator).
        """
        async with self._lock:
            if cache_key in self._tasks:
                return self._tasks[cache_key], False
            task = InFlightTask(cache_key, user_id, char_count)
            self._tasks[cache_key] = task
            return task, True

    async def remove(self, cache_key: str) -> None:
        """Remove a task from the active registry once synthesis completes or aborts."""
        async with self._lock:
            self._tasks.pop(cache_key, None)

    def get(self, cache_key: str) -> Optional[InFlightTask]:
        """Look up active in-flight task for cache_key without locking."""
        return self._tasks.get(cache_key)


inflight_manager = InFlightManager()
