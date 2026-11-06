import asyncio
import base64
import io
import uuid

import numpy as np
import pytest

from VoiceClonerAppServer.core.database import init_db
from VoiceClonerAppServer.services import clone_reference
from VoiceClonerAppServer.services.clone_reference import (
    CloneReferenceUnavailable,
    resolve_clone_reference,
)
from VoiceClonerAppServer.services.voice_service import CLONED_VOICES_DIR

API_HEADERS = {"X-API-Key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def _npz_bytes() -> bytes:
    buf = io.BytesIO()
    np.savez(buf, speaker_emb=np.ones(192, dtype=np.float32), codes=np.zeros((1, 256), dtype=np.int64))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_returns_existing_npz_without_extraction(monkeypatch):
    voice_id = "cloned_test_existing"
    npz_path = CLONED_VOICES_DIR / f"{voice_id}.npz"
    npz_path.write_bytes(_npz_bytes())
    called = []

    async def fake_extract(audio_bytes, filename="sample.wav"):
        called.append(1)
        return _npz_bytes()

    monkeypatch.setattr(clone_reference.model_dispatcher, "extract_embedding", fake_extract)
    try:
        b64 = await resolve_clone_reference(voice_id, None)
        assert base64.b64decode(b64) == _npz_bytes()
        assert called == []
    finally:
        npz_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_backfills_npz_from_stored_wav(monkeypatch):
    voice_id = "cloned_test_backfill"
    wav_path = CLONED_VOICES_DIR / f"{voice_id}.wav"
    npz_path = CLONED_VOICES_DIR / f"{voice_id}.npz"
    wav_path.write_bytes(b"RIFF" + b"\x00" * 60)
    npz_path.unlink(missing_ok=True)

    async def fake_extract(audio_bytes, filename="sample.wav"):
        assert audio_bytes.startswith(b"RIFF")
        return _npz_bytes()

    monkeypatch.setattr(clone_reference.model_dispatcher, "extract_embedding", fake_extract)
    try:
        b64 = await resolve_clone_reference(voice_id, str(wav_path))
        assert base64.b64decode(b64) == _npz_bytes()
        assert npz_path.exists()
    finally:
        wav_path.unlink(missing_ok=True)
        npz_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_raises_when_no_reference_files_exist():
    with pytest.raises(CloneReferenceUnavailable):
        await resolve_clone_reference("cloned_test_missing", "/nonexistent/path/cloned_test_missing.wav")


@pytest.mark.asyncio
async def test_falls_back_to_storage_dir_by_basename(monkeypatch):
    voice_id = "cloned_test_basename"
    wav_path = CLONED_VOICES_DIR / f"{voice_id}.wav"
    npz_path = CLONED_VOICES_DIR / f"{voice_id}.npz"
    wav_path.write_bytes(b"RIFF" + b"\x00" * 60)
    npz_path.unlink(missing_ok=True)

    async def fake_extract(audio_bytes, filename="sample.wav"):
        return _npz_bytes()

    monkeypatch.setattr(clone_reference.model_dispatcher, "extract_embedding", fake_extract)
    try:
        b64 = await resolve_clone_reference(voice_id, f"/app/VoiceClonerAppServer/storage/cloned_voices/{voice_id}.wav")
        assert b64
    finally:
        wav_path.unlink(missing_ok=True)
        npz_path.unlink(missing_ok=True)


def _register(api):
    email = f"clone_ref_{uuid.uuid4().hex[:8]}@example.com"
    token = api.post("/v1/auth/register", json={
        "email": email, "password": "password123", "full_name": "Clone Ref"
    }).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _clone(api, headers, name="Ref Voice"):
    res = api.post(
        "/v1/voices/clone",
        headers=headers,
        files={"audio_file": ("s.wav", b"RIFF" + b"\x00" * 40, "audio/wav")},
        data={"name": name},
    )
    assert res.status_code == 200
    return res.json()["voice"]["id"]


def test_create_speech_ignores_client_ref_audio_path(monkeypatch):
    from fastapi.testclient import TestClient
    from VoiceClonerAppServer.main import app
    from VoiceClonerAppServer.services import model_dispatcher

    api = TestClient(app, headers=API_HEADERS)
    headers = _register(api)
    captured = {}

    async def mock_synth(**kwargs):
        captured.update(kwargs)
        return b"RIFFfakeWAVaudio48k", 0.05, "mock-worker"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)

    res = api.post("/v1/audio/speech", headers=headers, json={
        "text": "Xin chào", "voice": "minh_duc", "ref_audio_path": "/etc/passwd.wav",
    })
    assert res.status_code == 200
    assert captured["ref_audio_path"] is None


def test_create_speech_backfills_missing_npz(monkeypatch):
    from fastapi.testclient import TestClient
    from VoiceClonerAppServer.main import app
    from VoiceClonerAppServer.services import model_dispatcher

    api = TestClient(app, headers=API_HEADERS)
    captured = {}

    async def mock_extract(audio_bytes, filename="sample.wav"):
        return _npz_bytes()

    async def mock_synth(**kwargs):
        captured.update(kwargs)
        return b"RIFFfakeWAVaudio48k", 0.05, "mock-worker"

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "extract_embedding", mock_extract)
    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)

    headers = _register(api)
    voice_id = _clone(api, headers)
    npz_path = CLONED_VOICES_DIR / f"{voice_id}.npz"
    npz_path.unlink()
    try:
        res = api.post("/v1/audio/speech", headers=headers, json={
            "text": "Xin chào", "voice": voice_id,
        })
        assert res.status_code == 200
        assert captured["ref_npz_base64"]
        assert captured["ref_audio_path"] is None
        assert npz_path.exists()
    finally:
        npz_path.unlink(missing_ok=True)


def _run_db(coro_fn):
    return asyncio.run(coro_fn())


def _insert_builtin_voice_row(voice_id: str, sample_text: str):
    from VoiceClonerAppServer.core.database import AsyncSessionLocal
    from VoiceClonerAppServer.models.db_models import Voice

    async def _do():
        await init_db()
        async with AsyncSessionLocal() as session:
            session.add(Voice(
                id=voice_id,
                user_id=None,
                name="DB Preset Voice",
                sample_text=sample_text,
                is_builtin=True,
                ref_audio_path=None,
            ))
            await session.commit()

    _run_db(_do)


def _delete_voice_row(voice_id: str):
    from sqlalchemy import delete

    from VoiceClonerAppServer.core.database import AsyncSessionLocal
    from VoiceClonerAppServer.models.db_models import Voice

    async def _do():
        async with AsyncSessionLocal() as session:
            await session.execute(delete(Voice).where(Voice.id == voice_id))
            await session.commit()

    _run_db(_do)


def test_preview_db_builtin_row_synthesizes_without_embedding(monkeypatch):
    from fastapi.testclient import TestClient
    from VoiceClonerAppServer.main import app
    from VoiceClonerAppServer.services import model_dispatcher

    api = TestClient(app, headers=API_HEADERS)
    headers = _register(api)
    voice_id = f"db_preset_{uuid.uuid4().hex[:8]}"
    _insert_builtin_voice_row(voice_id, "Xin chào từ DB preset")

    captured = {}

    async def mock_synth(**kwargs):
        captured.update(kwargs)
        return b"RIFFfakeWAVaudio48k", 0.05, "mock-worker"

    async def fail_extract(*args, **kwargs):
        raise AssertionError("a builtin DB row must not resolve a clone embedding")

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", mock_synth)
    monkeypatch.setattr(model_dispatcher.model_dispatcher, "extract_embedding", fail_extract)

    try:
        res = api.post("/v1/voices/preview", headers=headers, json={
            "voice_id": voice_id, "text": "Xin chào",
        })
        assert res.status_code == 200
        assert captured["ref_npz_base64"] is None
        assert captured["ref_audio_path"] is None
    finally:
        _delete_voice_row(voice_id)


def test_preview_cache_hit_serves_clone_without_npz(monkeypatch):
    from fastapi.testclient import TestClient
    from VoiceClonerAppServer.main import app
    from VoiceClonerAppServer.services import model_dispatcher
    from VoiceClonerAppServer.services.cache_service import cache_service

    api = TestClient(app, headers=API_HEADERS)
    headers = _register(api)

    async def success_extract(audio_bytes, filename="sample.wav"):
        return _npz_bytes()

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "extract_embedding", success_extract)
    voice_id = _clone(api, headers)
    npz_path = CLONED_VOICES_DIR / f"{voice_id}.npz"
    npz_path.unlink()

    sample_text = api.get(f"/v1/voices/{voice_id}", headers=headers).json()["sample_text"]
    cache_key = cache_service.get_cache_key(
        voice_id=f"preview_{voice_id}",
        text=sample_text,
        smart_pause=True,
        sample_rate=48000,
    )
    cache_service.save_cache(cache_key, b"RIFF" + b"\x00" * 120)
    cached_file = cache_service.get_cached_file(cache_key)

    async def fail_worker(*args, **kwargs):
        raise AssertionError("a cache hit must not call any worker")

    monkeypatch.setattr(model_dispatcher.model_dispatcher, "synthesize", fail_worker)
    monkeypatch.setattr(model_dispatcher.model_dispatcher, "extract_embedding", fail_worker)

    try:
        res = api.post("/v1/voices/preview", headers=headers, json={
            "voice_id": voice_id, "text": sample_text,
        })
        assert res.status_code == 200
        assert res.headers["X-Cache-Status"] == "HIT"
    finally:
        if cached_file is not None:
            cached_file.unlink(missing_ok=True)
        npz_path.unlink(missing_ok=True)
