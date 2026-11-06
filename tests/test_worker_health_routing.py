import time

import httpx
import pytest
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.services.model_dispatcher import ModelDispatcher


@pytest.fixture
def two_workers():
    dispatcher = ModelDispatcher()
    dispatcher.register_worker_capabilities("http://worker-1:8001", {"v1"}, True, "worker-1")
    dispatcher.register_worker_capabilities("http://worker-2:8002", {"v1"}, True, "worker-2")
    original = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({"model_workers": [
        {"id": "worker-1", "url": "http://worker-1:8001", "api_key": "k1", "max_concurrent": 2, "is_active": True},
        {"id": "worker-2", "url": "http://worker-2:8002", "api_key": "k2", "max_concurrent": 2, "is_active": True},
    ]})
    yield dispatcher
    settings_manager.update_settings({"model_workers": [w.model_dump() for w in original]})


def test_unhealthy_worker_is_never_selected(two_workers):
    dispatcher = two_workers
    dispatcher.mark_worker_health("http://worker-2:8002", False, "connect error")

    for _ in range(5):
        chosen, url = dispatcher._select_worker(voice_id="v1")
        assert url == "http://worker-1:8001"
        dispatcher._release_worker(url)


def test_selection_returns_none_when_all_workers_unhealthy(two_workers):
    dispatcher = two_workers
    dispatcher.mark_worker_health("http://worker-1:8001", False, "down")
    dispatcher.mark_worker_health("http://worker-2:8002", False, "down")

    assert dispatcher._select_worker(voice_id="v1") is None


def test_mark_unhealthy_does_not_open_circuit(two_workers):
    dispatcher = two_workers
    dispatcher.mark_worker_health("http://worker-2:8002", False, "down")

    health = dispatcher.get_worker_health("http://worker-2:8002")
    assert health["healthy"] is False
    assert health["circuit_state"] == "closed"
    assert health["consecutive_failures"] == 0


def test_mark_healthy_does_not_reset_breaker_state(two_workers):
    dispatcher = two_workers
    for _ in range(3):
        dispatcher._record_failure("http://worker-2:8002")
    dispatcher.mark_worker_health("http://worker-2:8002", True)

    health = dispatcher.get_worker_health("http://worker-2:8002")
    assert health["healthy"] is True
    assert health["consecutive_failures"] == 3
    assert health["circuit_state"] == "open"


def test_half_open_probe_is_still_eligible_after_cooldown(two_workers):
    dispatcher = two_workers
    dispatcher.mark_worker_health("http://worker-1:8001", False, "down")
    for _ in range(3):
        dispatcher._record_failure("http://worker-2:8002")
    stat = dispatcher._get_worker_stat("http://worker-2:8002")
    assert stat["healthy"] is False
    stat["last_failure_time"] = time.time() - 31.0

    chosen, url = dispatcher._select_worker(voice_id="v1")
    assert url == "http://worker-2:8002"
    assert stat["circuit_state"] == "half-open"
    dispatcher._release_worker(url)


def test_trailing_slash_url_health_is_honored():
    dispatcher = ModelDispatcher()
    original = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({"model_workers": [
        {"id": "w-slash", "url": "http://worker-slash:8002/", "api_key": "k", "max_concurrent": 2, "is_active": True},
    ]})
    try:
        dispatcher.mark_worker_health("http://worker-slash:8002", False, "down")
        assert dispatcher.get_worker_health("http://worker-slash:8002/")["healthy"] is False
        assert dispatcher._select_worker(voice_id="v1") is None
    finally:
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original]})
