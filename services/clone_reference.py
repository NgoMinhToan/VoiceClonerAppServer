import asyncio
import base64
import logging
from pathlib import Path
from typing import Dict, Optional

from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
from VoiceClonerAppServer.services.voice_service import CLONED_VOICES_DIR

logger = logging.getLogger("VoiceClonerAppServer.CloneReference")

CLONE_REFERENCE_UNAVAILABLE = (
    "Không thể khôi phục đặc trưng giọng đã nhân bản. Vui lòng tạo lại giọng nói."
)

_voice_locks: Dict[str, asyncio.Lock] = {}


class CloneReferenceUnavailable(RuntimeError):
    """A cloned voice has no usable reference (missing .npz and audio sample)."""


def _lock_for(voice_id: str) -> asyncio.Lock:
    lock = _voice_locks.get(voice_id)
    if lock is None:
        lock = asyncio.Lock()
        _voice_locks[voice_id] = lock
    return lock


def _resolve_source_wav(voice_id: str, ref_audio_path: Optional[str]) -> Optional[Path]:
    if ref_audio_path:
        candidate = Path(ref_audio_path)
        if candidate.is_file():
            return candidate
        # Stored paths are absolute at clone time; a different mount root on the
        # current host still lets us find the file by basename in storage.
        candidate = CLONED_VOICES_DIR / candidate.name
        if candidate.is_file():
            return candidate
    fallback = CLONED_VOICES_DIR / f"{voice_id}.wav"
    if fallback.is_file():
        return fallback
    return None


async def resolve_clone_reference(voice_id: str, ref_audio_path: Optional[str]) -> str:
    """
    Return the base64-encoded speaker embedding (.npz) for a cloned voice.

    Uses the stored .npz when present; otherwise re-extracts it from the saved
    audio sample through an upstream worker and persists it for later requests.
    Never returns a filesystem path: workers run on other hosts and cannot read
    the gateway's storage.
    """
    npz_path = CLONED_VOICES_DIR / f"{voice_id}.npz"
    if npz_path.is_file():
        return base64.b64encode(npz_path.read_bytes()).decode("utf-8")

    async with _lock_for(voice_id):
        if npz_path.is_file():
            return base64.b64encode(npz_path.read_bytes()).decode("utf-8")

        source = _resolve_source_wav(voice_id, ref_audio_path)
        if source is None:
            raise CloneReferenceUnavailable(CLONE_REFERENCE_UNAVAILABLE)

        try:
            npz_bytes = await model_dispatcher.extract_embedding(
                source.read_bytes(), filename=source.name
            )
        except Exception as e:
            logger.error(f"Failed to backfill speaker embedding for '{voice_id}': {e}")
            raise CloneReferenceUnavailable(CLONE_REFERENCE_UNAVAILABLE)

        tmp_path = npz_path.with_suffix(".npz.tmp")
        try:
            tmp_path.write_bytes(npz_bytes)
            tmp_path.replace(npz_path)
        except Exception as e:
            logger.error(f"Failed to persist speaker embedding for '{voice_id}': {e}")
            tmp_path.unlink(missing_ok=True)
            raise CloneReferenceUnavailable(CLONE_REFERENCE_UNAVAILABLE)

        logger.info(f"Backfilled speaker embedding for '{voice_id}' from {source.name}")
        return base64.b64encode(npz_bytes).decode("utf-8")
