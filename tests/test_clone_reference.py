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
