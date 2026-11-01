import asyncio
import pytest
from VoiceClonerAppServer.services.inflight_manager import InFlightManager


@pytest.mark.asyncio
async def test_inflight_manager_coalesces_duplicate_keys():
    manager = InFlightManager()
    cache_key = "test_key_coalesce"

    # First caller creates task
    task1, is_creator1 = await manager.get_or_create(cache_key, "user_1", 100)
    assert is_creator1 is True
    assert task1.cache_key == cache_key
    assert task1.initiator_user_id == "user_1"

    # Second caller joins existing task
    task2, is_creator2 = await manager.get_or_create(cache_key, "user_2", 100)
    assert is_creator2 is False
    assert task2 is task1

    # Creator resolves task with audio data
    audio_data = b"syntheticAudioData"
    task1.future.set_result((audio_data, 0.5, "worker-1"))

    # Both can read the result
    res = await task2.future
    assert res[0] == audio_data

    # Remove cleans up registry
    await manager.remove(cache_key)
    assert manager.get(cache_key) is None


@pytest.mark.asyncio
async def test_inflight_manager_propagates_error():
    manager = InFlightManager()
    cache_key = "test_key_error"

    task1, _ = await manager.get_or_create(cache_key, "user_1", 50)
    task2, _ = await manager.get_or_create(cache_key, "user_2", 50)

    task1.future.set_exception(RuntimeError("Worker crashed"))

    with pytest.raises(RuntimeError, match="Worker crashed"):
        await task2.future

    await manager.remove(cache_key)


@pytest.mark.asyncio
async def test_inflight_task_attributes_and_defaults():
    manager = InFlightManager()
    cache_key = "test_key_attributes"

    task, is_creator = await manager.get_or_create(cache_key, "user_attr", 250)
    assert is_creator is True
    assert task.cache_key == cache_key
    assert task.initiator_user_id == "user_attr"
    assert task.char_count == 250
    assert task.initiator_paid is False
    assert task.is_billed is False
    assert isinstance(task.claim_lock, asyncio.Lock)
    assert isinstance(task.future, asyncio.Future)
    assert not task.future.done()

    # get() retrieves task without modifying
    retrieved = manager.get(cache_key)
    assert retrieved is task

    # remove() works cleanly
    await manager.remove(cache_key)
    assert manager.get(cache_key) is None

    # remove() on absent key does not raise
    await manager.remove("nonexistent_key")
    assert manager.get("nonexistent_key") is None


def test_singleton_inflight_manager():
    from VoiceClonerAppServer.services.inflight_manager import inflight_manager
    assert isinstance(inflight_manager, InFlightManager)

