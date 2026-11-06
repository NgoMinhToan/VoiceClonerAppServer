import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.database import get_db
from VoiceClonerAppServer.core.redis_client import (
    check_and_increment_daily_quota,
    check_rate_limit,
    get_daily_usage,
    rollback_daily_quota,
)
from VoiceClonerAppServer.core.security import get_current_user, get_optional_user, verify_client_api_key
from VoiceClonerAppServer.core.settings_manager import ClientApiKeyConfig, settings_manager
from VoiceClonerAppServer.models.db_models import DailyUsage, User, Voice, utc_now
from VoiceClonerAppServer.models.schemas import PreviewSpeechRequest, SynthesisRequest
from VoiceClonerAppServer.services.cache_service import cache_service
from VoiceClonerAppServer.services.clone_reference import (
    CloneReferenceUnavailable,
    resolve_clone_reference,
)
from VoiceClonerAppServer.services.concurrency_limiter import user_concurrency_limiter
from VoiceClonerAppServer.services.inflight_manager import inflight_manager
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
from VoiceClonerAppServer.services.usage_tracker import usage_tracker
from VoiceClonerAppServer.services.voice_service import voice_service

logger = logging.getLogger("VoiceClonerAppServer.SpeechAPI")

router = APIRouter(tags=["Speech"])


def _build_audio_response(
    audio_bytes: bytes,
    response_format: str,
    duration_ms: float,
    cache_status: str,
    worker_id: str,
) -> Response:
    if response_format == "pcm":
        media_type = "audio/pcm"
        content = audio_bytes[44:] if len(audio_bytes) > 44 and audio_bytes.startswith(b"RIFF") else audio_bytes
    elif response_format == "wav":
        media_type = "audio/wav"
        content = audio_bytes
    else:
        media_type = f"audio/{response_format}"
        content = audio_bytes

    headers = {
        "Content-Type": media_type,
        "X-Sample-Rate": "48000",
        "X-Execution-Time-Ms": f"{duration_ms:.2f}",
        "X-Cache-Status": cache_status,
        "X-Model-Worker": worker_id,
    }
    return Response(content=content, media_type=media_type, headers=headers)


async def _commit_daily_usage(db: AsyncSession, user_id: str, char_count: int) -> None:
    try:
        today = datetime.now(timezone.utc).date()
        usage_stmt = select(DailyUsage).where(
            DailyUsage.user_id == user_id,
            DailyUsage.usage_date == today,
        )
        usage_res = await db.execute(usage_stmt)
        daily_usage_record = usage_res.scalar_one_or_none()
        if daily_usage_record:
            daily_usage_record.tts_characters_used += char_count
            daily_usage_record.updated_at = utc_now()
        else:
            daily_usage_record = DailyUsage(
                user_id=user_id,
                usage_date=today,
                tts_characters_used=char_count,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(daily_usage_record)
        await db.commit()
    except Exception as ue:
        await db.rollback()
        logger.warning(f"Could not persist DailyUsage record: {ue}")


@router.post("/v1/audio/speech")
async def create_speech(
    request: SynthesisRequest,
    req_raw: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_client_timeout: Optional[float] = Header(120.0, alias="X-Client-Timeout"),
):
    """
    OpenAI-compatible text-to-speech synthesis endpoint.
    Supports User Bearer JWT authentication (with user daily quota)
    and/or Client API Key (with client quota tracking).
    Standard 48kHz WAV audio output with in-flight deduplication and unbilled cache.
    """
    # 1. Validate authentication credentials
    client_key: Optional[ClientApiKeyConfig] = None
    if x_api_key:
        client_key = settings_manager.find_client_key(x_api_key)
        if not client_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or deactivated client API key.",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    if settings_manager.get_settings().security.require_client_api_key and not client_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Client API key required. Please provide a valid X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # 2. Parse text input
    input_text = (request.input or request.text or "").strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="Văn bản cần tổng hợp không được để trống.")

    char_count = len(input_text)
    voice_name = request.voice_id or request.voice or "minh_duc"
    response_format = (request.response_format or request.format or "wav").lower()
    client_name = client_key.name if client_key else current_user.full_name
    client_id = client_key.id if client_key else current_user.id
    client_timeout_sec = float(x_client_timeout) if (x_client_timeout is not None and x_client_timeout > 0) else 120.0

    logger.info(
        f"🎙️ [TTS REQUEST] voice='{voice_name}' | text='{input_text[:50]}...' ({char_count} chars) | "
        f"user='{current_user.email}' | client='{client_name}' | format={response_format}"
    )

    # 3. Check user limits
    default_limits = settings_manager.get_settings().default_user_limits
    max_speech_limit = getattr(current_user, "max_speech_char_limit", None) or default_limits.max_speech_char_limit
    daily_limit = getattr(current_user, "daily_tts_char_limit", None) or default_limits.daily_tts_char_limit
    user_rate_limit = getattr(current_user, "rate_limit_requests_per_minute", None) or default_limits.rate_limit_requests_per_minute
    user_concurrent = getattr(current_user, "max_concurrent_tts", None) or default_limits.max_concurrent_tts
    user_queue = getattr(current_user, "max_queue_waiters", None) if getattr(current_user, "max_queue_waiters", None) is not None else default_limits.max_queue_waiters

    if char_count > max_speech_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Văn bản cần tổng hợp vượt quá giới hạn tối đa cho phép ({char_count}/{max_speech_limit} ký tự).",
        )

    # Check rate limit
    if not await check_rate_limit(current_user.id, max_requests=user_rate_limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau.",
        )

    # Step 0: Resolve voice and cloned embeddings & IDOR check (before cache or in-flight lookups)
    ref_npz_base64 = request.ref_npz_base64
    db_clone_voice: Optional[Voice] = None

    stmt = select(Voice).where(Voice.id == voice_name)
    res = await db.execute(stmt)
    db_voice = res.scalar_one_or_none()

    if db_voice and not db_voice.is_builtin:
        if db_voice.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")
        db_clone_voice = db_voice
        # The embedding is resolved lazily after the cache checks, so a cached
        # recording still plays when a legacy clone has no .npz on disk.

    # Step 1: Standard Audio Cache Check (deterministic SHA-256 excluding speed/pitch for player-side tuning)
    cache_key = cache_service.get_cache_key(
        voice_id=voice_name,
        text=input_text,
        smart_pause=request.smart_pause,
        sample_rate=48000,
    )
    cached_file = cache_service.get_cached_file(cache_key)

    if cached_file and response_format in ("wav", "pcm"):
        t_cache = time.perf_counter()
        wav_bytes = cached_file.read_bytes()
        duration_ms = (time.perf_counter() - t_cache) * 1000.0

        logger.info(
            f"⚡ [AUDIO CACHE HIT] voice='{voice_name}', text_len={char_count} -> Instant 48kHz WAV "
            f"served from cache ({cached_file.name}, {len(wav_bytes):,} bytes, {duration_ms:.2f}ms) [Zero Inference & Quota Exempt]"
        )

        await usage_tracker.log_usage(
            client_key_id=client_id,
            client_key_name=client_name,
            endpoint="/v1/audio/speech",
            model_worker_id="cache-hit",
            char_count=char_count,
            latency_ms=duration_ms,
            status_code=200,
        )

        return _build_audio_response(wav_bytes, response_format, duration_ms, cache_status="HIT", worker_id="cache")

    # Step 2: Unbilled Audio Cache Check (orphaned audio from previous disconnected synthesis)
    unbilled_file = cache_service.get_unbilled_cached_file(cache_key)
    if unbilled_file and response_format in ("wav", "pcm"):
        t_claim = time.perf_counter()
        current_used = await get_daily_usage(current_user.id)
        remaining_chars = max(0, daily_limit - current_used)
        if char_count > remaining_chars or not await check_and_increment_daily_quota(current_user.id, char_count, limit=daily_limit):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Hạn mức ký tự còn lại hôm nay không đủ ({remaining_chars:,} ký tự còn lại, yêu cầu {char_count:,} ký tự). Hạn mức sẽ được làm mới vào 00:00.",
            )

        try:
            promoted_path = cache_service.promote_unbilled_cache(cache_key)
            target_path = promoted_path if promoted_path and promoted_path.exists() else unbilled_file
            wav_bytes = target_path.read_bytes()
        except Exception as e:
            logger.error(f"Failed to read/promote unbilled cache file for key {cache_key}: {e}")
            await rollback_daily_quota(current_user.id, char_count)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Lỗi khi truy xuất tệp âm thanh từ bộ nhớ đệm.",
            )
        duration_ms = (time.perf_counter() - t_claim) * 1000.0

        await _commit_daily_usage(db, current_user.id, char_count)

        await usage_tracker.log_usage(
            client_key_id=client_id,
            client_key_name=client_name,
            endpoint="/v1/audio/speech",
            model_worker_id="unbilled-cache-claimed",
            char_count=char_count,
            latency_ms=duration_ms,
            status_code=200,
        )

        logger.info(
            f"🏷️ [UNBILLED CACHE CLAIMED] voice='{voice_name}', text_len={char_count} -> Promoted to standard cache "
            f"and billed {char_count} characters to user '{current_user.email}'"
        )

        return _build_audio_response(wav_bytes, response_format, duration_ms, cache_status="HIT-CLAIMED", worker_id="cache")

    # Step 3: In-Flight Deduplication (SingleFlight pattern)
    logger.info(f"🔄 [AUDIO CACHE MISS] voice='{voice_name}', text_len={char_count} -> In-flight coordination...")

    task, is_creator = await inflight_manager.get_or_create(cache_key, current_user.id, char_count)

    if not is_creator:
        t_waiter = time.perf_counter()
        logger.info(f"⏳ [IN-FLIGHT WAITER] Joined active TTS job for key {cache_key[:12]}... (user='{current_user.email}')")

        waiter_disconnected = False

        async def _watch_waiter_disconnect():
            nonlocal waiter_disconnected
            try:
                while not waiter_disconnected:
                    msg = await req_raw.receive()
                    if msg.get("type") == "http.disconnect":
                        waiter_disconnected = True
                        logger.info(f"🔌 [WAITER DISCONNECTED] Waiter connection dropped for key {cache_key[:12]}...")
                        break
            except Exception:
                pass

        waiter_disconnect_watcher = asyncio.create_task(_watch_waiter_disconnect())

        try:
            try:
                audio_bytes, exec_sec, worker_id = await task.future
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"In-flight task failed for waiter: {e}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Dịch vụ tổng hợp âm thanh tạm thời không khả dụng, vui lòng thử lại sau.",
                )

            duration_sec = time.perf_counter() - t_waiter
            duration_ms = duration_sec * 1000.0

            is_waiter_timed_out = duration_sec >= client_timeout_sec
            is_waiter_abandoned = waiter_disconnected or is_waiter_timed_out or (await req_raw.is_disconnected())

            async with task.claim_lock:
                if task.initiator_paid or task.is_billed or (cache_service.get_cached_file(cache_key) is not None):
                    if is_waiter_abandoned:
                        logger.info(
                            f"🔌 [WAITER ABANDONED (CACHE HIT)] waiter duration={duration_sec:.2f}s >= timeout={client_timeout_sec:.2f}s "
                            f"or disconnected={waiter_disconnected}; returning 499 (quota exempt)"
                        )
                        return Response(status_code=499)

                    logger.info(
                        f"⚡ [IN-FLIGHT CACHE HIT] voice='{voice_name}', text_len={char_count} -> Served as HIT from completed in-flight job"
                    )
                    await usage_tracker.log_usage(
                        client_key_id=client_id,
                        client_key_name=client_name,
                        endpoint="/v1/audio/speech",
                        model_worker_id="cache-hit",
                        char_count=char_count,
                        latency_ms=duration_ms,
                        status_code=200,
                    )
                    return _build_audio_response(audio_bytes, response_format, duration_ms, cache_status="HIT", worker_id="cache")
                else:
                    # Initiator disconnected and not yet billed
                    if is_waiter_abandoned:
                        logger.info(
                            f"🔌 [WAITER ABANDONED (UNBILLED)] waiter duration={duration_sec:.2f}s >= timeout={client_timeout_sec:.2f}s "
                            f"or disconnected={waiter_disconnected}; skipping claim to protect quota, returning 499"
                        )
                        return Response(status_code=499)

                    current_used = await get_daily_usage(current_user.id)
                    remaining_chars = max(0, daily_limit - current_used)
                    if char_count > remaining_chars or not await check_and_increment_daily_quota(current_user.id, char_count, limit=daily_limit):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Hạn mức ký tự còn lại hôm nay không đủ ({remaining_chars:,} ký tự còn lại, yêu cầu {char_count:,} ký tự). Hạn mức sẽ được làm mới vào 00:00.",
                        )

                    try:
                        promoted = cache_service.promote_unbilled_cache(cache_key)
                        if not promoted and response_format == "wav" and audio_bytes and len(audio_bytes) > 44:
                            cache_service.save_cache(cache_key, audio_bytes)
                    except Exception as ce:
                        logger.warning(f"Could not persist cache during waiter claim: {ce}")
                        await rollback_daily_quota(current_user.id, char_count)
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Lỗi lưu trữ âm thanh, vui lòng thử lại sau.",
                        )

                    task.is_billed = True

                    await _commit_daily_usage(db, current_user.id, char_count)

                    await usage_tracker.log_usage(
                        client_key_id=client_id,
                        client_key_name=client_name,
                        endpoint="/v1/audio/speech",
                        model_worker_id="unbilled-cache-claimed",
                        char_count=char_count,
                        latency_ms=duration_ms,
                        status_code=200,
                    )

                    logger.info(
                        f"🏷️ [IN-FLIGHT UNBILLED CLAIMED] voice='{voice_name}', text_len={char_count} -> Billed {char_count} chars to waiter '{current_user.email}'"
                    )

                    return _build_audio_response(audio_bytes, response_format, duration_ms, cache_status="HIT-CLAIMED", worker_id="cache")
        finally:
            if waiter_disconnect_watcher and not waiter_disconnect_watcher.done():
                waiter_disconnect_watcher.cancel()

    # Creator path: Deduct daily quota upfront for creator
    current_used = await get_daily_usage(current_user.id)
    remaining_chars = max(0, daily_limit - current_used)
    if char_count > remaining_chars or not await check_and_increment_daily_quota(current_user.id, char_count, limit=daily_limit):
        await inflight_manager.remove(cache_key)
        exc = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Hạn mức ký tự còn lại hôm nay không đủ ({remaining_chars:,} ký tự còn lại, yêu cầu {char_count:,} ký tự). Hạn mức sẽ được làm mới vào 00:00.",
        )
        task.future.set_exception(exc)
        try:
            _ = task.future.exception()
        except Exception:
            pass
        raise exc

    t0 = time.perf_counter()
    quota_rolled_back = False

    async def _safe_rollback_quota():
        nonlocal quota_rolled_back
        if not quota_rolled_back:
            quota_rolled_back = True
            await rollback_daily_quota(current_user.id, char_count)

    client_disconnected = False

    async def _watch_disconnect():
        nonlocal client_disconnected
        try:
            while not client_disconnected:
                msg = await req_raw.receive()
                if msg.get("type") == "http.disconnect":
                    client_disconnected = True
                    logger.info(f"🔌 [CLIENT DISCONNECTED] Client connection dropped during synthesis for key {cache_key[:12]}...")
                    with anyio.CancelScope(shield=True):
                        await _safe_rollback_quota()
                    break
        except Exception:
            pass

    disconnect_watcher = asyncio.create_task(_watch_disconnect())

    try:
        async def _execute_synthesis():
            ref_b64 = ref_npz_base64
            if db_clone_voice is not None:
                try:
                    ref_b64 = await resolve_clone_reference(
                        db_clone_voice.id, db_clone_voice.ref_audio_path
                    )
                except CloneReferenceUnavailable as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
                    )
            async with user_concurrency_limiter.acquire(
                current_user.id,
                max_concurrent=user_concurrent,
                max_waiters=user_queue,
            ):
                return await model_dispatcher.synthesize(
                    text=input_text,
                    voice=voice_name,
                    speed=request.speed,
                    pitch=request.pitch,
                    emotion=request.emotion_level if request.emotion_level is not None else request.emotion,
                    denoise=request.denoise,
                    smart_pause=request.smart_pause,
                    response_format=response_format,
                    ref_audio_path=None,
                    ref_npz_base64=ref_b64,
                )

        synth_task = asyncio.create_task(_execute_synthesis())

        try:
            audio_bytes, exec_sec, worker_id = await asyncio.shield(synth_task)
        except asyncio.CancelledError:
            client_disconnected = True
            with anyio.CancelScope(shield=True):
                try:
                    audio_bytes, exec_sec, worker_id = await synth_task
                except Exception as synth_err:
                    logger.error(f"Synthesis task failed after client disconnect: {synth_err}")
                    if not task.future.done():
                        task.future.set_exception(synth_err)
                        try:
                            _ = task.future.exception()
                        except Exception:
                            pass
                    await _safe_rollback_quota()
                    raise asyncio.CancelledError()

                logger.info(f"🔌 [CLIENT DISCONNECTED] Creator disconnected; saving unbilled cache for key {cache_key[:12]}...")
                await _safe_rollback_quota()
                if response_format == "wav" and audio_bytes and len(audio_bytes) > 44:
                    try:
                        cache_service.save_unbilled_cache(cache_key, audio_bytes)
                    except Exception as ce:
                        logger.warning(f"Could not persist unbilled audio cache: {ce}")
                if not task.future.done():
                    task.future.set_result((audio_bytes, exec_sec, worker_id))
            raise asyncio.CancelledError()

        duration_sec = time.perf_counter() - t0
        duration_ms = duration_sec * 1000.0

        is_client_timed_out = duration_sec >= client_timeout_sec
        is_abandoned = client_disconnected or is_client_timed_out or (await req_raw.is_disconnected())

        if is_abandoned:
            logger.info(
                f"🔌 [CLIENT TIMEOUT/DISCONNECT] duration={duration_sec:.2f}s >= timeout={client_timeout_sec:.2f}s "
                f"or disconnected={client_disconnected}; saving unbilled cache for key {cache_key[:12]}..."
            )
            with anyio.CancelScope(shield=True):
                await _safe_rollback_quota()
                if response_format == "wav" and audio_bytes and len(audio_bytes) > 44:
                    try:
                        cache_service.save_unbilled_cache(cache_key, audio_bytes)
                    except Exception as ce:
                        logger.warning(f"Could not persist unbilled audio cache: {ce}")
                if not task.future.done():
                    task.future.set_result((audio_bytes, exec_sec, worker_id))
            return Response(status_code=499)

        # Client is online and receives audio:
        if response_format == "wav" and audio_bytes and len(audio_bytes) > 44:
            try:
                cache_service.save_cache(cache_key, audio_bytes)
                logger.info(f"💾 [AUDIO CACHE SAVED] Key {cache_key[:12]}... ({len(audio_bytes):,} bytes @ 48kHz saved to storage/audio_cache)")
            except Exception as ce:
                logger.warning(f"Could not persist audio cache: {ce}")

        task.initiator_paid = True

        await _commit_daily_usage(db, current_user.id, char_count)

        if not task.future.done():
            task.future.set_result((audio_bytes, exec_sec, worker_id))

        await usage_tracker.log_usage(
            client_key_id=client_id,
            client_key_name=client_name,
            endpoint="/v1/audio/speech",
            model_worker_id=worker_id,
            char_count=char_count,
            latency_ms=duration_ms,
            status_code=200,
        )

        logger.info(f"✅ [TTS COMPLETED] via worker '{worker_id}' in {duration_ms:.2f}ms: received {len(audio_bytes):,} bytes @ 48kHz")

        return _build_audio_response(audio_bytes, response_format, duration_ms, cache_status="MISS", worker_id=worker_id)

    except HTTPException:
        await _safe_rollback_quota()
        if not task.future.done():
            task.future.set_exception(
                HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Dịch vụ tổng hợp âm thanh tạm thời không khả dụng, vui lòng thử lại sau.",
                )
            )
            try:
                _ = task.future.exception()
            except Exception:
                pass
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await _safe_rollback_quota()
        if not task.future.done():
            task.future.set_exception(e)
            try:
                _ = task.future.exception()
            except Exception:
                pass
        duration_ms = (time.perf_counter() - t0) * 1000.0
        logger.error(f"Speech synthesis failure: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dịch vụ tổng hợp âm thanh tạm thời không khả dụng, vui lòng thử lại sau.",
        )
    finally:
        if disconnect_watcher and not disconnect_watcher.done():
            disconnect_watcher.cancel()
        with anyio.CancelScope(shield=True):
            await inflight_manager.remove(cache_key)


@router.post("/v1/voices/preview")
async def preview_voice(
    request: PreviewSpeechRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Quick preview playback for voice catalog.
    Uses sample text or provided text, returns 48kHz WAV audio.
    Enforces maximum preview character limit and daily quota tracking.
    """
    client_key: Optional[ClientApiKeyConfig] = None
    if x_api_key:
        client_key = settings_manager.find_client_key(x_api_key)
    client_name = client_key.name if client_key else current_user.full_name
    client_id = client_key.id if client_key else current_user.id

    voice_id = request.voice_id
    sample_text = (request.text or "").strip()

    # Check preset
    builtin = voice_service.get_voice_by_id(voice_id)
    if builtin and not sample_text:
        sample_text = builtin.sample_text

    ref_npz_base64 = None
    db_voice: Optional[Voice] = None

    if not builtin:
        # Check custom voice in DB
        stmt = select(Voice).where(Voice.id == voice_id)
        res = await db.execute(stmt)
        db_voice = res.scalar_one_or_none()
        if not db_voice:
            raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

        if not db_voice.is_builtin and db_voice.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

        if not sample_text:
            sample_text = db_voice.sample_text or "Xin chào, đây là bản nghe thử giọng nói."

        # Embedding resolution happens after the cache lookup below so cached
        # previews keep working for legacy clones without a stored .npz.

    if not sample_text:
        sample_text = "Xin chào! Đây là bản nghe thử được kết xuất trực tiếp bằng công nghệ AI Neural 48kHz."

    char_count = len(sample_text)

    # 1. Check user limits
    default_limits = settings_manager.get_settings().default_user_limits
    max_preview_limit = getattr(current_user, "max_preview_char_limit", None) or default_limits.max_preview_char_limit
    daily_limit = getattr(current_user, "daily_tts_char_limit", None) or default_limits.daily_tts_char_limit
    user_rate_limit = getattr(current_user, "rate_limit_requests_per_minute", None) or default_limits.rate_limit_requests_per_minute
    user_concurrent = getattr(current_user, "max_concurrent_tts", None) or default_limits.max_concurrent_tts
    user_queue = getattr(current_user, "max_queue_waiters", None) if getattr(current_user, "max_queue_waiters", None) is not None else default_limits.max_queue_waiters

    if char_count > max_preview_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Văn bản nghe thử vượt quá giới hạn tối đa cho phép ({char_count}/{max_preview_limit} ký tự).",
        )

    # 2. Check remaining daily quota
    current_used = await get_daily_usage(current_user.id)
    remaining_chars = max(0, daily_limit - current_used)
    if char_count > remaining_chars:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Hạn mức ký tự còn lại hôm nay không đủ ({remaining_chars:,} ký tự còn lại, yêu cầu {char_count:,} ký tự). Hạn mức sẽ được làm mới vào 00:00.",
        )

    # 3. Check rate limit
    if not await check_rate_limit(current_user.id, max_requests=user_rate_limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau.",
        )

    preview_cache_key = cache_service.get_cache_key(
        voice_id=f"preview_{voice_id}",
        text=sample_text,
        smart_pause=True,
        sample_rate=48000,
    )
    cached_preview = cache_service.get_cached_file(preview_cache_key)
    if cached_preview:
        t_cache = time.perf_counter()
        wav_bytes = cached_preview.read_bytes()
        duration_ms = (time.perf_counter() - t_cache) * 1000.0

        logger.info(f"⚡ [PREVIEW CACHE HIT] voice='{voice_id}' -> Instant preview returned from cache ({cached_preview.name}, {duration_ms:.2f}ms)")

        await usage_tracker.log_usage(
            client_key_id=client_id,
            client_key_name=client_name,
            endpoint="/v1/voices/preview",
            model_worker_id="cache-hit",
            char_count=char_count,
            latency_ms=duration_ms,
            status_code=200,
        )

        headers = {
            "Content-Type": "audio/wav",
            "X-Sample-Rate": "48000",
            "X-Execution-Time-Ms": f"{duration_ms:.2f}",
            "X-Cache-Status": "HIT",
            "X-Model-Worker": "cache",
        }
        return Response(content=wav_bytes, media_type="audio/wav", headers=headers)

    # Resolve the clone embedding only on cache miss (never a server-local path).
    if db_voice is not None and not db_voice.is_builtin:
        try:
            ref_npz_base64 = await resolve_clone_reference(db_voice.id, db_voice.ref_audio_path)
        except CloneReferenceUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    # 4. Cache MISS: Deduct daily quota atomically
    if not await check_and_increment_daily_quota(current_user.id, char_count, limit=daily_limit):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Bạn đã sử dụng hết hạn mức {daily_limit:,} ký tự hôm nay. Hạn mức sẽ được làm mới vào 00:00.",
        )

    t0 = time.perf_counter()
    try:
        async with user_concurrency_limiter.acquire(
            current_user.id,
            max_concurrent=user_concurrent,
            max_waiters=user_queue,
        ):
            audio_bytes, exec_sec, worker_id = await model_dispatcher.synthesize(
                text=sample_text,
                voice=voice_id,
                speed=request.speed or 1.0,
                pitch=request.pitch or 0,
                emotion=request.emotion or 85,
                response_format="wav",
                ref_audio_path=None,
                ref_npz_base64=ref_npz_base64,
            )
        duration_ms = (time.perf_counter() - t0) * 1000.0

        if audio_bytes and len(audio_bytes) > 44:
            try:
                cache_service.save_cache(preview_cache_key, audio_bytes)
                logger.info(f"💾 [PREVIEW CACHE SAVED] Key {preview_cache_key[:12]}... ({len(audio_bytes):,} bytes @ 48kHz saved)")
            except Exception as ce:
                logger.warning(f"Could not persist preview audio cache: {ce}")

        # Asynchronous usage logging
        await usage_tracker.log_usage(
            client_key_id=client_id,
            client_key_name=client_name,
            endpoint="/v1/voices/preview",
            model_worker_id=worker_id,
            char_count=char_count,
            latency_ms=duration_ms,
            status_code=200,
        )

        # Persist DailyUsage for user in SQL database
        try:
            today = datetime.now(timezone.utc).date()
            usage_stmt = select(DailyUsage).where(
                DailyUsage.user_id == current_user.id,
                DailyUsage.usage_date == today,
            )
            usage_res = await db.execute(usage_stmt)
            daily_usage_record = usage_res.scalar_one_or_none()
            if daily_usage_record:
                daily_usage_record.tts_characters_used += char_count
                daily_usage_record.updated_at = utc_now()
            else:
                daily_usage_record = DailyUsage(
                    user_id=current_user.id,
                    usage_date=today,
                    tts_characters_used=char_count,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                db.add(daily_usage_record)
            await db.commit()
        except Exception as ue:
            logger.warning(f"Could not persist DailyUsage record for preview: {ue}")

        logger.info(f"✅ [PREVIEW COMPLETED] voice='{voice_id}' via worker '{worker_id}' in {duration_ms:.2f}ms: received {len(audio_bytes):,} bytes @ 48kHz")

        headers = {
            "Content-Type": "audio/wav",
            "X-Sample-Rate": "48000",
            "X-Execution-Time-Ms": f"{duration_ms:.2f}",
            "X-Cache-Status": "MISS",
            "X-Model-Worker": worker_id,
        }
        return Response(content=audio_bytes, media_type="audio/wav", headers=headers)
    except HTTPException:
        await rollback_daily_quota(current_user.id, char_count)
        raise
    except Exception as e:
        await rollback_daily_quota(current_user.id, char_count)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        logger.error(f"Preview synthesis error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không thể kết xuất bản nghe thử, dịch vụ tổng hợp tạm thời không khả dụng.",
        )
