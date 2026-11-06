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


@pytest.mark.asyncio
async def test_probe_marks_unreachable_worker_unhealthy(two_workers, monkeypatch):
    dispatcher = two_workers

    async def mock_get(url, **kwargs):
        request = httpx.Request("GET", url)
        if "worker-1" in str(url):
            return httpx.Response(200, json={"status": "healthy"}, request=request)
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(dispatcher._get_http_client(), "get", mock_get)
    try:
        results = await dispatcher.probe_all_workers()
        assert results["http://worker-1:8001"] is True
        assert results["http://worker-2:8002"] is False
        assert dispatcher.get_worker_health("http://worker-2:8002")["healthy"] is False
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_probe_invokes_recovery_callback_once_on_recovery(two_workers, monkeypatch):
    dispatcher = two_workers
    calls = []
    state = {"up": False}

    async def callback():
        calls.append(1)

    async def mock_get(url, **kwargs):
        request = httpx.Request("GET", url)
        if not state["up"]:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"status": "healthy"}, request=request)

    monkeypatch.setattr(dispatcher._get_http_client(), "get", mock_get)
    try:
        await dispatcher.probe_all_workers(on_recovery=callback)
        assert calls == []

        state["up"] = True
        await dispatcher.probe_all_workers(on_recovery=callback)
        assert calls == [1]

        await dispatcher.probe_all_workers(on_recovery=callback)
        assert calls == [1]
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_start_and_stop_health_monitor_is_idempotent(two_workers):
    dispatcher = two_workers

    async def noop_probe(*args, **kwargs):
        return {}

    dispatcher.probe_all_workers = noop_probe
    await dispatcher.start_health_monitor(interval_sec=60)
    await dispatcher.start_health_monitor(interval_sec=60)
    await dispatcher.stop_health_monitor()
    await dispatcher.stop_health_monitor()


def test_http_client_has_short_connect_timeout(two_workers):
    dispatcher = two_workers
    client = dispatcher._get_http_client()

    assert client.timeout.connect == 10.0
    assert client.timeout.read == 300.0


@pytest.mark.asyncio
async def test_synthesize_uses_short_connect_timeout(two_workers, monkeypatch):
    dispatcher = two_workers
    captured = {}

    async def mock_post(url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return httpx.Response(200, content=b"RIFFok", request=httpx.Request("POST", url))

    monkeypatch.setattr(dispatcher._get_http_client(), "post", mock_post)

    await dispatcher.synthesize(text="Xin chao", voice="v1")
    assert captured["timeout"].connect == 10.0
