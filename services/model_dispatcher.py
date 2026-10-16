import asyncio
import io
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import httpx
import numpy as np
import soundfile as sf

from VoiceClonerAppServer.core.settings_manager import ModelWorkerConfig, settings_manager

logger = logging.getLogger("VoiceClonerAppServer.ModelDispatcher")

CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_SEC = 30.0


class ModelDispatcher:
    def __init__(self):
        self._lock = threading.RLock()
        self._worker_stats: Dict[str, Dict[str, Any]] = {}
        self._http_client: Optional[httpx.AsyncClient] = None
        self._round_robin_idx: int = 0

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
            self._http_client = httpx.AsyncClient(limits=limits, timeout=60.0)
        return self._http_client

    def _get_worker_stat(self, url: str) -> Dict[str, Any]:
        with self._lock:
            if url not in self._worker_stats:
                self._worker_stats[url] = {
                    "active_jobs": 0,
                    "consecutive_failures": 0,
                    "circuit_state": "closed",
                    "last_failure_time": 0.0,
                    "probing": False,
                }
            return self._worker_stats[url]

    def _record_success(self, url: str):
        with self._lock:
            stat = self._get_worker_stat(url)
            stat["consecutive_failures"] = 0
            stat["circuit_state"] = "closed"
            stat["probing"] = False

    def _record_failure(self, url: str):
        with self._lock:
            stat = self._get_worker_stat(url)
            stat["consecutive_failures"] += 1
            stat["last_failure_time"] = time.time()
            if stat["consecutive_failures"] >= CIRCUIT_FAILURE_THRESHOLD:
                stat["circuit_state"] = "open"
                stat["probing"] = False

    def _select_worker(self) -> Optional[Tuple[ModelWorkerConfig, str]]:
        settings = settings_manager.get_settings()
        workers = [w for w in settings.model_workers if w.is_active]
        if not workers:
            return None

        now = time.time()
        candidates: List[ModelWorkerConfig] = []
        with self._lock:
            for w in workers:
                stat = self._get_worker_stat(w.url)
                if stat["circuit_state"] == "closed":
                    candidates.append(w)
                elif stat["circuit_state"] == "open":
                    if now - stat["last_failure_time"] >= CIRCUIT_COOLDOWN_SEC:
                        stat["circuit_state"] = "half-open"
                        stat["probing"] = True
                        candidates.append(w)
                elif stat["circuit_state"] == "half-open" and stat["probing"]:
                    candidates.append(w)

            if not candidates:
                return None

            # Least-active selection
            sorted_candidates = sorted(candidates, key=lambda w: self._get_worker_stat(w.url)["active_jobs"])
            min_active = self._get_worker_stat(sorted_candidates[0].url)["active_jobs"]
            tied = [w for w in sorted_candidates if self._get_worker_stat(w.url)["active_jobs"] == min_active]

            chosen = tied[self._round_robin_idx % len(tied)]
            self._round_robin_idx += 1
            self._get_worker_stat(chosen.url)["active_jobs"] += 1
            return chosen, chosen.url

    def _release_worker(self, url: str):
        with self._lock:
            stat = self._get_worker_stat(url)
            stat["active_jobs"] = max(0, stat["active_jobs"] - 1)

    async def ping_worker(self, url: str, api_key: str) -> Dict[str, Any]:
        """Test health probe and latency to upstream worker node."""
        client = self._get_http_client()
        clean_url = url.rstrip("/")
        headers = {"X-API-Key": api_key} if api_key else {}
        t0 = time.perf_counter()
        try:
            res = await client.get(f"{clean_url}/internal/v1/health", headers=headers, timeout=5.0)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if res.status_code == 200:
                data = res.json()
                return {
                    "status": "online",
                    "latency_ms": round(latency_ms, 2),
                    "model_loaded": data.get("model_loaded", False),
                    "engine_mode": data.get("engine_mode", "unknown"),
                    "threads": data.get("threads", 8),
                }
            return {"status": "error", "code": res.status_code, "detail": res.text[:100]}
        except Exception as e:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return {"status": "offline", "latency_ms": round(latency_ms, 2), "error": str(e)}

    async def synthesize(
        self,
        text: str,
        voice: str = "minh_duc",
        speed: float = 1.0,
        pitch: int = 0,
        emotion: int = 0,
        denoise: bool = True,
        smart_pause: bool = True,
        response_format: str = "wav",
        ref_audio_path: Optional[str] = None,
        ref_npz_path: Optional[str] = None,
        ref_npz_base64: Optional[str] = None,
    ) -> Tuple[bytes, float, str]:
        """
        Dispatch TTS synthesis to chosen worker.
        Returns: (wav_audio_bytes, execution_time_sec, worker_id)
        """
        selection = self._select_worker()
        if selection is None:
            # Fallback mock synthesis if no workers are reachable
            logger.info("No upstream workers available. Producing fallback 48kHz WAV audio.")
            t0 = time.perf_counter()
            duration = min(3.0, max(0.5, len(text) * 0.05 / max(0.5, speed)))
            sr = 48000
            t = np.linspace(0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
            base_freq = 220.0 * (2.0 ** (pitch / 12.0))
            audio = 0.25 * np.sin(2.0 * np.pi * base_freq * t)
            buf = io.BytesIO()
            sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
            return buf.getvalue(), time.perf_counter() - t0, "in-process-fallback"

        worker, worker_url = selection
        client = self._get_http_client()
        headers = {"X-API-Key": worker.api_key} if worker.api_key else {}

        # If ref_npz_path provided on server, encode to base64 for network transfer
        npz_b64 = ref_npz_base64
        if not npz_b64 and ref_npz_path and Path(ref_npz_path).exists():
            try:
                import base64
                npz_b64 = base64.b64encode(Path(ref_npz_path).read_bytes()).decode("utf-8")
            except Exception as e:
                logger.warning(f"Failed to read/encode ref_npz_path {ref_npz_path}: {e}")

        payload = {
            "text": text,
            "voice": voice,
            "speed": speed,
            "pitch": pitch,
            "emotion": emotion,
            "denoise": denoise,
            "smart_pause": smart_pause,
            "format": response_format,
            "ref_audio_path": ref_audio_path,
            "ref_npz_base64": npz_b64,
        }

        t0 = time.perf_counter()
        try:
            resp = await client.post(
                f"{worker_url}/internal/v1/synthesize",
                json=payload,
                headers=headers,
                timeout=60.0,
            )
            if resp.status_code == 200:
                self._record_success(worker_url)
                return resp.content, time.perf_counter() - t0, worker.id
            self._record_failure(worker_url)
            raise RuntimeError(f"Worker {worker.id} returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            self._record_failure(worker_url)
            logger.error(f"Error dispatching to worker {worker.id}: {e}")
            raise
        finally:
            self._release_worker(worker_url)

    async def extract_embedding(self, audio_bytes: bytes, filename: str = "sample.wav") -> bytes:
        """
        Forward reference audio to upstream model worker to extract .npz speaker embedding.
        Returns: raw bytes of the .npz file.
        """
        selection = self._select_worker()
        if selection is None:
            # Fallback mock embedding if no upstream workers available
            logger.info("No upstream workers available. Producing fallback .npz embedding.")
            buf = io.BytesIO()
            np.savez(buf, speaker_emb=np.zeros(192, dtype=np.float32), codes=np.zeros((1, 256), dtype=np.int64))
            return buf.getvalue()

        worker, worker_url = selection
        client = self._get_http_client()
        headers = {"X-API-Key": worker.api_key} if worker.api_key else {}
        files = {"audio_file": (filename, audio_bytes, "audio/wav")}

        try:
            resp = await client.post(
                f"{worker_url}/internal/v1/embed",
                files=files,
                headers=headers,
                timeout=60.0,
            )
            if resp.status_code == 200:
                self._record_success(worker_url)
                return resp.content
            self._record_failure(worker_url)
            raise RuntimeError(f"Worker {worker.id} embed returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            self._record_failure(worker_url)
            logger.error(f"Error extracting embedding with worker {worker.id}: {e}")
            raise
        finally:
            self._release_worker(worker_url)

    async def aclose(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


model_dispatcher = ModelDispatcher()
