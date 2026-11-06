import asyncio
import contextlib
import io
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

import httpx
import numpy as np
import soundfile as sf

from VoiceClonerAppServer.core.settings_manager import ModelWorkerConfig, settings_manager

logger = logging.getLogger("VoiceClonerAppServer.ModelDispatcher")

CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_SEC = 30.0


def _normalize_url(url: str) -> str:
    """Canonical worker URL used as the key for every per-worker stat map."""
    return (url or "").strip().rstrip("/")


class WorkerCapability:
    def __init__(
        self,
        worker_id: str,
        worker_url: str,
        supported_voices: Set[str],
        supports_cloning: bool = True,
        last_discovered: float = 0.0,
    ):
        self.worker_id = worker_id
        self.worker_url = worker_url
        self.supported_voices = {v.strip().lower() for v in supported_voices}
        self.supports_cloning = supports_cloning
        self.last_discovered = last_discovered or time.time()


class ModelDispatcher:
    def __init__(self):
        self._lock = threading.RLock()
        self._worker_stats: Dict[str, Dict[str, Any]] = {}
        self._worker_caps: Dict[str, WorkerCapability] = {}
        self._http_client: Optional[httpx.AsyncClient] = None
        self._round_robin_idx: int = 0
        self._health_task: Optional[asyncio.Task] = None

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
            self._http_client = httpx.AsyncClient(limits=limits, timeout=300.0)
        return self._http_client

    def _get_worker_stat(self, url: str) -> Dict[str, Any]:
        with self._lock:
            key = _normalize_url(url)
            if key not in self._worker_stats:
                self._worker_stats[key] = {
                    "active_jobs": 0,
                    "consecutive_failures": 0,
                    "circuit_state": "closed",
                    "last_failure_time": 0.0,
                    "probing": False,
                    "healthy": True,
                    "last_error": None,
                    "last_probe_at": 0.0,
                }
            return self._worker_stats[key]

    def _record_success(self, url: str):
        with self._lock:
            stat = self._get_worker_stat(url)
            stat["consecutive_failures"] = 0
            stat["circuit_state"] = "closed"
            stat["probing"] = False
            stat["healthy"] = True
            stat["last_error"] = None

    def _record_failure(self, url: str):
        with self._lock:
            stat = self._get_worker_stat(url)
            stat["consecutive_failures"] += 1
            stat["last_failure_time"] = time.time()
            if stat["consecutive_failures"] >= CIRCUIT_FAILURE_THRESHOLD:
                stat["circuit_state"] = "open"
                stat["probing"] = False
                stat["healthy"] = False

    def _is_configured_worker(self, url: str) -> bool:
        key = _normalize_url(url)
        return any(_normalize_url(w.url) == key for w in settings_manager.get_settings().model_workers)

    def mark_worker_health(self, worker_url: str, healthy: bool, error: Optional[str] = None) -> None:
        """Record a health probe result. Never mutates breaker counters or circuit state."""
        with self._lock:
            if not self._is_configured_worker(worker_url):
                return
            stat = self._get_worker_stat(worker_url)
            stat["healthy"] = healthy
            stat["last_probe_at"] = time.time()
            stat["last_error"] = None if healthy else error

    def get_worker_health(self, worker_url: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._get_worker_stat(worker_url))

    def register_worker_capabilities(
        self,
        worker_url: str,
        supported_voices: Set[str],
        supports_cloning: bool = True,
        worker_id: str = "",
    ):
        """Record advertised capabilities and supported voice IDs for a worker node."""
        with self._lock:
            clean_url = _normalize_url(worker_url)
            self._worker_caps[clean_url] = WorkerCapability(
                worker_id=worker_id,
                worker_url=clean_url,
                supported_voices=supported_voices,
                supports_cloning=supports_cloning,
                last_discovered=time.time(),
            )

    def get_worker_capabilities(self, worker_url: str) -> Optional[WorkerCapability]:
        with self._lock:
            clean_url = _normalize_url(worker_url)
            return self._worker_caps.get(clean_url)

    def _select_worker(
        self,
        voice_id: Optional[str] = None,
        is_clone: bool = False,
        exclude_urls: Optional[Set[str]] = None,
    ) -> Optional[Tuple[ModelWorkerConfig, str]]:
        """
        Capability-Aware Utilization-Based Load Balancer:
        1. Filters active workers against circuit breaker status (and optional excluded URLs).
        2. Filters candidates to only those supporting voice_id (or cloning).
        3. Filters out workers at or over their max_concurrent capacity limit.
        4. Selects candidate with the lowest utilization ratio (active_jobs / max_concurrent).
        5. Round-robin tiebreaker when multiple workers have equal utilization ratio.
        """
        settings = settings_manager.get_settings()
        workers = [w for w in settings.model_workers if w.is_active]
        if not workers:
            return None

        clean_voice = (voice_id or "").strip().lower()
        now = time.time()

        with self._lock:
            # 1. Check circuit breaker state and exclusions.
            # Health probes only gate workers whose breaker is closed: an open
            # breaker must still be able to half-open and self-heal after the
            # cooldown, otherwise a probed-down worker could never come back.
            norm_excludes = {_normalize_url(u) for u in exclude_urls} if exclude_urls else set()
            healthy_workers: List[ModelWorkerConfig] = []
            for w in workers:
                key = _normalize_url(w.url)
                if key in norm_excludes:
                    continue
                stat = self._get_worker_stat(key)
                if stat["circuit_state"] == "open":
                    if now - stat["last_failure_time"] >= CIRCUIT_COOLDOWN_SEC:
                        stat["circuit_state"] = "half-open"
                        stat["probing"] = True
                        healthy_workers.append(w)
                    continue
                if stat["circuit_state"] == "half-open":
                    if stat["probing"]:
                        healthy_workers.append(w)
                    continue
                if not stat.get("healthy", True):
                    continue
                healthy_workers.append(w)

            if not healthy_workers:
                return None

            # 2. Filter candidates by capability
            capable_workers: List[ModelWorkerConfig] = []
            has_any_discovered_caps = any(_normalize_url(w.url) in self._worker_caps for w in healthy_workers)

            for w in healthy_workers:
                clean_url = _normalize_url(w.url)
                cap = self._worker_caps.get(clean_url)

                if cap is None:
                    # Cold boot grace: if no capabilities discovered yet for this worker, consider it capable
                    if not has_any_discovered_caps:
                        capable_workers.append(w)
                    continue

                if is_clone:
                    if cap.supports_cloning:
                        capable_workers.append(w)
                elif clean_voice:
                    if clean_voice in cap.supported_voices:
                        capable_workers.append(w)
                else:
                    capable_workers.append(w)

            # If no worker advertised specific voice but capabilities were not yet discovered,
            # fallback to all healthy workers to avoid false 503s on cold boot.
            if not capable_workers and not has_any_discovered_caps:
                capable_workers = healthy_workers

            if not capable_workers:
                logger.warning(
                    f"No capable workers found for voice='{voice_id}' (is_clone={is_clone}) "
                    f"among {len(healthy_workers)} healthy workers."
                )
                return None

            # 3. Filter candidates by max_concurrent capacity
            available_workers = [
                w for w in capable_workers
                if self._get_worker_stat(w.url)["active_jobs"] < getattr(w, "max_concurrent", 2)
            ]
            if not available_workers:
                logger.warning(
                    f"All {len(capable_workers)} capable workers are at maximum capacity for voice='{voice_id}'."
                )
                return None

            # 4. Utilization-ratio selection on available candidates
            def _utilization_ratio(w: ModelWorkerConfig) -> float:
                cap = max(getattr(w, "max_concurrent", 2), 1)
                return self._get_worker_stat(w.url)["active_jobs"] / cap

            sorted_candidates = sorted(available_workers, key=_utilization_ratio)
            min_ratio = _utilization_ratio(sorted_candidates[0])
            tied = [w for w in sorted_candidates if abs(_utilization_ratio(w) - min_ratio) < 1e-6]

            # 5. Round-robin tiebreaker
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
                self.mark_worker_health(clean_url, True)
                cap = self.get_worker_capabilities(url)
                return {
                    "status": "online",
                    "latency_ms": round(latency_ms, 2),
                    "model_loaded": data.get("model_loaded", False),
                    "engine_mode": data.get("engine_mode", "unknown"),
                    "threads": data.get("threads", 8),
                    "supported_voices_count": len(cap.supported_voices) if cap else data.get("total_voices", 0),
                    "supports_cloning": cap.supports_cloning if cap else True,
                }
            self.mark_worker_health(clean_url, False, f"HTTP {res.status_code}")
            return {"status": "error", "code": res.status_code, "detail": res.text[:100]}
        except Exception as e:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self.mark_worker_health(clean_url, False, str(e))
            return {"status": "offline", "latency_ms": round(latency_ms, 2), "error": str(e)}

    async def probe_all_workers(
        self,
        timeout: float = 3.0,
        on_recovery: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> Dict[str, bool]:
        """Probe every active worker's health endpoint and update routing health."""
        settings = settings_manager.get_settings()
        workers = [w for w in settings.model_workers if w.is_active]
        if not workers:
            return {}

        client = self._get_http_client()

        async def _probe(worker: ModelWorkerConfig) -> Tuple[str, bool, Optional[str]]:
            clean_url = _normalize_url(worker.url)
            headers = {"X-API-Key": worker.api_key} if worker.api_key else {}
            try:
                res = await client.get(
                    f"{clean_url}/internal/v1/health", headers=headers, timeout=timeout
                )
                if res.status_code == 200:
                    return clean_url, True, None
                return clean_url, False, f"HTTP {res.status_code}"
            except Exception as e:
                return clean_url, False, str(e)

        results = await asyncio.gather(*[_probe(w) for w in workers])

        recovered: List[str] = []
        outcome: Dict[str, bool] = {}
        for url, healthy, error in results:
            previous = self.get_worker_health(url)["healthy"]
            self.mark_worker_health(url, healthy, error)
            outcome[url] = healthy
            if healthy and not previous:
                recovered.append(url)

        if recovered:
            logger.info(f"Worker(s) recovered and eligible for routing again: {', '.join(recovered)}")
            if on_recovery is not None:
                try:
                    await on_recovery()
                except Exception as e:
                    logger.error(f"Worker recovery callback failed: {e}")

        return outcome

    async def _health_loop(
        self,
        interval: float,
        timeout: float,
        on_recovery: Optional[Callable[[], Awaitable[None]]],
    ) -> None:
        while True:
            try:
                await self.probe_all_workers(timeout=timeout, on_recovery=on_recovery)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker health probe cycle failed")
            await asyncio.sleep(interval)

    async def start_health_monitor(
        self,
        interval_sec: Optional[float] = None,
        on_recovery: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        if self._health_task is not None and not self._health_task.done():
            return
        interval = (
            interval_sec
            if interval_sec is not None
            else float(os.getenv("WORKER_HEALTH_INTERVAL_SEC", "15"))
        )
        timeout = float(os.getenv("WORKER_HEALTH_TIMEOUT_SEC", "3"))
        self._health_task = asyncio.create_task(self._health_loop(interval, timeout, on_recovery))
        logger.info(f"Worker health monitor started (interval={interval}s, timeout={timeout}s)")

    async def stop_health_monitor(self) -> None:
        task = self._health_task
        if task is None:
            return
        self._health_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logger.info("Worker health monitor stopped")

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
        Dispatch TTS synthesis to chosen capable worker with 1x retry on 5xx or connection errors.
        Returns: (wav_audio_bytes, execution_time_sec, worker_id)
        """
        is_clone = bool(ref_npz_base64 or ref_audio_path or ref_npz_path or (voice and voice.startswith("cloned_")))

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

        client = self._get_http_client()
        tried_urls: Set[str] = set()
        max_attempts = 2
        last_error: Optional[Exception] = None
        t0 = time.perf_counter()

        for attempt in range(max_attempts):
            selection = self._select_worker(voice_id=voice, is_clone=is_clone, exclude_urls=tried_urls)
            if selection is None:
                if attempt == 0:
                    raise RuntimeError(f"No capable workers available for voice='{voice}' (is_clone={is_clone})")
                break

            worker, worker_url = selection
            tried_urls.add(_normalize_url(worker_url))
            headers = {"X-API-Key": worker.api_key} if worker.api_key else {}

            try:
                resp = await client.post(
                    f"{worker_url}/internal/v1/synthesize",
                    json=payload,
                    headers=headers,
                    timeout=300.0,
                )
                if resp.status_code == 200:
                    self._record_success(worker_url)
                    return resp.content, time.perf_counter() - t0, worker.id

                self._record_failure(worker_url)
                if resp.status_code >= 500:
                    logger.warning(
                        f"Worker {worker.id} ({worker_url}) returned HTTP {resp.status_code}. Attempt {attempt + 1}/{max_attempts}."
                    )
                    last_error = RuntimeError(f"Worker {worker.id} returned status {resp.status_code}: {resp.text}")
                    continue
                else:
                    raise RuntimeError(f"Worker {worker.id} returned status {resp.status_code}: {resp.text}")

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as net_err:
                self._record_failure(worker_url)
                logger.warning(
                    f"Worker {worker.id} ({worker_url}) network error ({net_err}). Attempt {attempt + 1}/{max_attempts}."
                )
                last_error = net_err
                continue
            except Exception as e:
                self._record_failure(worker_url)
                logger.error(f"Error dispatching to worker {worker.id}: {e}")
                raise
            finally:
                self._release_worker(worker_url)

        if last_error:
            raise last_error
        raise RuntimeError(f"No capable workers available for voice='{voice}' (is_clone={is_clone})")

    async def extract_embedding(self, audio_bytes: bytes, filename: str = "sample.wav") -> bytes:
        """
        Forward reference audio to upstream model worker to extract .npz speaker embedding.
        Routes to workers supporting voice cloning with 1x retry on 5xx or connection errors.
        Returns: raw bytes of the .npz file.
        """
        client = self._get_http_client()
        files = {"audio_file": (filename, audio_bytes, "audio/wav")}
        tried_urls: Set[str] = set()
        max_attempts = 2
        last_error: Optional[Exception] = None

        for attempt in range(max_attempts):
            selection = self._select_worker(is_clone=True, exclude_urls=tried_urls)
            if selection is None:
                if attempt == 0:
                    raise RuntimeError("No upstream workers available for cloning embedding extraction")
                break

            worker, worker_url = selection
            tried_urls.add(_normalize_url(worker_url))
            headers = {"X-API-Key": worker.api_key} if worker.api_key else {}

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
                if resp.status_code >= 500:
                    logger.warning(
                        f"Worker {worker.id} embed returned HTTP {resp.status_code}. Attempt {attempt + 1}/{max_attempts}."
                    )
                    last_error = RuntimeError(f"Worker {worker.id} embed returned status {resp.status_code}: {resp.text}")
                    continue
                else:
                    raise RuntimeError(f"Worker {worker.id} embed returned status {resp.status_code}: {resp.text}")

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as net_err:
                self._record_failure(worker_url)
                logger.warning(
                    f"Worker {worker.id} embed network error ({net_err}). Attempt {attempt + 1}/{max_attempts}."
                )
                last_error = net_err
                continue
            except Exception as e:
                self._record_failure(worker_url)
                logger.error(f"Error extracting embedding with worker {worker.id}: {e}")
                raise
            finally:
                self._release_worker(worker_url)

        if last_error:
            raise last_error
        raise RuntimeError("No upstream workers available for cloning embedding extraction")

    async def aclose(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


model_dispatcher = ModelDispatcher()
