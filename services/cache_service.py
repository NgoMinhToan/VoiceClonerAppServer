import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("VoiceClonerAppServer.CacheService")

STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
AUDIO_CACHE_DIR = STORAGE_DIR / "audio_cache"
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class CacheService:
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or AUDIO_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cache_key(
        self,
        voice_id: str,
        text: str,
        smart_pause: bool = True,
        sample_rate: int = 48000,
    ) -> str:
        """
        Generate deterministic SHA-256 cache key.
        Speed and pitch are handled client-side in the player for zero-latency tuning,
        while smart_pause influences phoneme and pause silence synthesis.
        """
        norm_text = text.strip()
        payload = f"{voice_id}:{norm_text}:{smart_pause}:{sample_rate}hz"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_cached_file(self, cache_key: str) -> Optional[Path]:
        """Return path to cached WAV file if it exists and contains valid audio data."""
        p = self.cache_dir / f"{cache_key}.wav"
        if p.exists() and p.is_file() and p.stat().st_size > 44:
            return p
        return None

    def save_cache(self, cache_key: str, wav_bytes: bytes) -> Path:
        """Atomically persist synthesized WAV audio bytes to cache file."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        p = self.cache_dir / f"{cache_key}.wav"
        temp_p = self.cache_dir / f"{cache_key}.tmp_{id(wav_bytes)}"
        try:
            temp_p.write_bytes(wav_bytes)
            temp_p.replace(p)
            logger.info(f"Saved audio cache: {p.name} ({len(wav_bytes)} bytes @ 48kHz)")
            return p
        except Exception as e:
            if temp_p.exists():
                try:
                    temp_p.unlink()
                except Exception:
                    pass
            logger.warning(f"Could not persist audio cache {p.name}: {e}")
            raise

    def get_unbilled_cached_file(self, cache_key: str) -> Optional[Path]:
        p = self.cache_dir / f"unbilled_{cache_key}.wav"
        if p.exists() and p.is_file():
            if p.stat().st_size <= 44:
                try:
                    p.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(f"Could not delete corrupt unbilled cache {p.name}: {e}")
                return None
            return p
        return None

    def save_unbilled_cache(self, cache_key: str, wav_bytes: bytes) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        p = self.cache_dir / f"unbilled_{cache_key}.wav"
        temp_p = self.cache_dir / f"unbilled_{cache_key}.tmp_{id(wav_bytes)}"
        try:
            temp_p.write_bytes(wav_bytes)
            temp_p.replace(p)
            logger.info(f"Saved unbilled audio cache: {p.name} ({len(wav_bytes)} bytes @ 48kHz)")
            return p
        except Exception as e:
            if temp_p.exists():
                try:
                    temp_p.unlink()
                except Exception:
                    pass
            logger.warning(f"Could not persist unbilled audio cache {p.name}: {e}")
            raise

    def promote_unbilled_cache(self, cache_key: str) -> Optional[Path]:
        unbilled_p = self.cache_dir / f"unbilled_{cache_key}.wav"
        dest_p = self.cache_dir / f"{cache_key}.wav"
        if not unbilled_p.exists():
            if dest_p.exists() and dest_p.stat().st_size > 44:
                return dest_p
            return None

        try:
            if dest_p.exists():
                unbilled_p.unlink(missing_ok=True)
                return dest_p
            unbilled_p.replace(dest_p)
            logger.info(f"Promoted unbilled audio cache {unbilled_p.name} -> {dest_p.name}")
            return dest_p
        except Exception as e:
            logger.warning(f"Could not promote unbilled cache {cache_key}: {e}")
            if dest_p.exists():
                return dest_p
            raise


    def purge_cache(self) -> int:
        """Purge all cached audio WAV files and return the number of deleted files."""
        deleted_count = 0
        if not self.cache_dir.exists():
            return 0
        for item in self.cache_dir.glob("*.wav"):
            try:
                if item.is_file():
                    item.unlink()
                    deleted_count += 1
            except Exception as e:
                logger.warning(f"Could not delete cache file {item.name}: {e}")
        logger.info(f"Purged {deleted_count} audio cache files.")
        return deleted_count


cache_service = CacheService()
