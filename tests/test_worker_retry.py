import httpx
import pytest
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.services.model_dispatcher import ModelDispatcher


@pytest.fixture
def two_workers():
    dispatcher = ModelDispatcher()
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-1:8001",
        supported_voices={"v1"},
        supports_cloning=True,
        worker_id="worker-1",
    )
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-2:8002",
        supported_voices={"v1"},
        supports_cloning=True,
        worker_id="worker-2",
    )

    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({
        "model_workers": [
            {"id": "worker-1", "url": "http://worker-1:8001", "api_key": "k1", "max_concurrent": 2, "is_active": True},
            {"id": "worker-2", "url": "http://worker-2:8002", "api_key": "k2", "max_concurrent": 2, "is_active": True},
        ]
    })
    yield dispatcher
    settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})


@pytest.mark.asyncio
async def test_synthesize_retries_on_5xx(two_workers, monkeypatch):
    dispatcher = two_workers

    # First call (worker-1) returns 500, retry (worker-2) returns 200
    call_log = []

    async def mock_post(url, **kwargs):
        call_log.append(str(url))
        if "worker-1" in str(url):
            return httpx.Response(500, text="Internal Worker Error", request=httpx.Request("POST", url))
        return httpx.Response(200, content=b"RIFFvalidwavfromworker2", request=httpx.Request("POST", url))

    client = dispatcher._get_http_client()
    monkeypatch.setattr(client, "post", mock_post)

    audio_bytes, exec_sec, worker_id = await dispatcher.synthesize(text="Xin chao", voice="v1")
    assert audio_bytes == b"RIFFvalidwavfromworker2"
    assert worker_id == "worker-2"
    assert len(call_log) == 2
    assert "worker-1" in call_log[0]
    assert "worker-2" in call_log[1]


@pytest.mark.asyncio
async def test_synthesize_retries_on_connection_error(two_workers, monkeypatch):
    dispatcher = two_workers
    call_log = []

    async def mock_post(url, **kwargs):
        call_log.append(str(url))
        if "worker-1" in str(url):
            raise httpx.ConnectError("Connection refused", request=httpx.Request("POST", url))
        return httpx.Response(200, content=b"RIFFvalidwavfromworker2", request=httpx.Request("POST", url))

    client = dispatcher._get_http_client()
    monkeypatch.setattr(client, "post", mock_post)

    audio_bytes, exec_sec, worker_id = await dispatcher.synthesize(text="Xin chao", voice="v1")
    assert audio_bytes == b"RIFFvalidwavfromworker2"
    assert worker_id == "worker-2"
    assert len(call_log) == 2


@pytest.mark.asyncio
async def test_synthesize_no_retry_on_4xx(two_workers, monkeypatch):
    dispatcher = two_workers
    call_log = []

    async def mock_post(url, **kwargs):
        call_log.append(str(url))
        return httpx.Response(400, text="Bad Request: invalid voice parameters", request=httpx.Request("POST", url))

    client = dispatcher._get_http_client()
    monkeypatch.setattr(client, "post", mock_post)

    with pytest.raises(RuntimeError, match="returned status 400"):
        await dispatcher.synthesize(text="Xin chao", voice="v1")

    # Must NOT have retried worker-2 on 4xx
    assert len(call_log) == 1
    assert "worker-1" in call_log[0]


@pytest.mark.asyncio
async def test_synthesize_raises_when_no_workers():
    dispatcher = ModelDispatcher()
    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({"model_workers": []})
    try:
        with pytest.raises(RuntimeError, match="No capable workers available"):
            await dispatcher.synthesize(text="Xin chao", voice="v1")
    finally:
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})


@pytest.mark.asyncio
async def test_extract_embedding_raises_when_no_workers():
    dispatcher = ModelDispatcher()
    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({"model_workers": []})
    try:
        with pytest.raises(RuntimeError, match="No upstream workers available"):
            await dispatcher.extract_embedding(audio_bytes=b"RIFFtestaudio")
    finally:
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})


@pytest.mark.asyncio
async def test_synthesize_4xx_does_not_strike_worker(two_workers, monkeypatch):
    dispatcher = two_workers

    async def mock_post(url, **kwargs):
        return httpx.Response(400, text="invalid reference", request=httpx.Request("POST", url))

    monkeypatch.setattr(dispatcher._get_http_client(), "post", mock_post)

    with pytest.raises(RuntimeError, match="returned status 400"):
        await dispatcher.synthesize(text="Xin chao", voice="v1")

    health = dispatcher.get_worker_health("http://worker-1:8001")
    assert health["consecutive_failures"] == 0
    assert health["healthy"] is True


@pytest.mark.asyncio
async def test_synthesize_5xx_strikes_worker(two_workers, monkeypatch):
    dispatcher = two_workers

    async def mock_post(url, **kwargs):
        return httpx.Response(503, text="engine down", request=httpx.Request("POST", url))

    monkeypatch.setattr(dispatcher._get_http_client(), "post", mock_post)

    with pytest.raises(RuntimeError):
        await dispatcher.synthesize(text="Xin chao", voice="v1")

    assert dispatcher.get_worker_health("http://worker-1:8001")["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_extract_embedding_4xx_does_not_strike_worker(two_workers, monkeypatch):
    dispatcher = two_workers

    async def mock_post(url, **kwargs):
        return httpx.Response(400, text="invalid audio", request=httpx.Request("POST", url))

    monkeypatch.setattr(dispatcher._get_http_client(), "post", mock_post)

    with pytest.raises(RuntimeError):
        await dispatcher.extract_embedding(audio_bytes=b"RIFFtest")

    assert dispatcher.get_worker_health("http://worker-1:8001")["consecutive_failures"] == 0
