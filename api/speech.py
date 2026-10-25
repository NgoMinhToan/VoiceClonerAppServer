import base64
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
from VoiceClonerAppServer.services.usage_tracker import usage_tracker
from VoiceClonerAppServer.services.voice_service import CLONED_VOICES_DIR, voice_service

logger = logging.getLogger("VoiceClonerAppServer.SpeechAPI")

router = APIRouter(tags=["Speech"])


@router.post("/v1/audio/speech")
async def create_speech(
    request: SynthesisRequest,
    req_raw: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    OpenAI-compatible text-to-speech synthesis endpoint.
    Supports User Bearer JWT authentication (with user daily quota)
    and/or Client API Key (with client quota tracking).
    Standard 48kHz WAV audio output.
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

    logger.info(
        f"🎙️ [TTS REQUEST] voice='{voice_name}' | text='{input_text[:50]}...' ({char_count} chars) | "
        f"user='{current_user.email}' | client='{client_name}' | format={response_format}"
    )

    # 3. Check max speech character limit from settings
    max_speech_limit = settings_manager.get_settings().concurrency.max_speech_char_limit
    if char_count > max_speech_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Văn bản cần tổng hợp vượt quá giới hạn tối đa cho phép ({char_count}/{max_speech_limit} ký tự).",
        )

    # 4. Check remaining daily quota
    daily_limit = settings_manager.get_settings().concurrency.daily_tts_char_limit
    current_used = await get_daily_usage(current_user.id)
    remaining_chars = max(0, daily_limit - current_used)
    if char_count > remaining_chars:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Hạn mức ký tự còn lại hôm nay không đủ ({remaining_chars:,} ký tự còn lại, yêu cầu {char_count:,} ký tự). Hạn mức sẽ được làm mới vào 00:00.",
        )

    # 5. Check rate limit
    if not await check_rate_limit(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau.",
        )

    # 6. Check Audio Cache (deterministic SHA-256 excluding speed/pitch for player-side tuning)
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

        if response_format == "pcm":
            pcm_data = wav_bytes[44:] if len(wav_bytes) > 44 else wav_bytes
            headers = {
                "Content-Type": "audio/pcm",
                "X-Sample-Rate": "48000",
                "X-Execution-Time-Ms": f"{duration_ms:.2f}",
                "X-Cache-Status": "HIT",
                "X-Model-Worker": "cache",
            }
            return Response(content=pcm_data, media_type="audio/pcm", headers=headers)
        else:
            headers = {
                "Content-Type": "audio/wav",
                "X-Sample-Rate": "48000",
                "X-Execution-Time-Ms": f"{duration_ms:.2f}",
                "X-Cache-Status": "HIT",
                "X-Model-Worker": "cache",
            }
            return Response(content=wav_bytes, media_type="audio/wav", headers=headers)

    logger.info(f"🔄 [AUDIO CACHE MISS] voice='{voice_name}', text_len={char_count} -> Checking quota and dispatching to worker...")

    # 7. Deduct daily quota atomically (Cache MISS path)
    if not await check_and_increment_daily_quota(current_user.id, char_count, limit=daily_limit):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Bạn đã sử dụng hết hạn mức {daily_limit:,} ký tự hôm nay. Hạn mức sẽ được làm mới vào 00:00.",
        )

    # 5. Resolve voice and cloned embeddings
    ref_npz_base64 = request.ref_npz_base64
    ref_audio_path = request.ref_audio_path

    # Check if voice_name refers to a database cloned voice
    stmt = select(Voice).where(Voice.id == voice_name)
    res = await db.execute(stmt)
    db_voice = res.scalar_one_or_none()

    if db_voice and not db_voice.is_builtin:
        if db_voice.user_id != current_user.id:
            await rollback_daily_quota(current_user.id, char_count)
            raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

        # Load precomputed .npz embedding if present
        npz_file = CLONED_VOICES_DIR / f"{db_voice.id}.npz"
        if npz_file.exists():
            ref_npz_base64 = base64.b64encode(npz_file.read_bytes()).decode("utf-8")
        elif db_voice.ref_audio_path:
            ref_audio_path = db_voice.ref_audio_path

    t0 = time.perf_counter()
    try:
        audio_bytes, exec_sec, worker_id = await model_dispatcher.synthesize(
            text=input_text,
            voice=voice_name,
            speed=request.speed,
            pitch=request.pitch,
            emotion=request.emotion_level if request.emotion_level is not None else request.emotion,
            denoise=request.denoise,
            smart_pause=request.smart_pause,
            response_format=response_format,
            ref_audio_path=ref_audio_path,
            ref_npz_base64=ref_npz_base64,
        )

        duration_ms = (time.perf_counter() - t0) * 1000.0

        # Persist synthesized WAV to audio cache
        if response_format == "wav" and audio_bytes and len(audio_bytes) > 44:
            try:
                cache_service.save_cache(cache_key, audio_bytes)
                logger.info(f"💾 [AUDIO CACHE SAVED] Key {cache_key[:12]}... ({len(audio_bytes):,} bytes @ 48kHz saved to storage/audio_cache)")
            except Exception as ce:
                logger.warning(f"Could not persist audio cache: {ce}")

        # Asynchronous usage logging
        await usage_tracker.log_usage(
            client_key_id=client_id,
            client_key_name=client_name,
            endpoint="/v1/audio/speech",
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
            logger.warning(f"Could not persist DailyUsage record: {ue}")

        logger.info(f"✅ [TTS COMPLETED] via worker '{worker_id}' in {duration_ms:.2f}ms: received {len(audio_bytes):,} bytes @ 48kHz")

        media_type = "audio/pcm" if response_format == "pcm" else "audio/wav"
        headers = {
            "Content-Type": media_type,
            "X-Sample-Rate": "48000",
            "X-Execution-Time-Ms": f"{duration_ms:.2f}",
            "X-Cache-Status": "MISS",
            "X-Model-Worker": worker_id,
        }
        return Response(content=audio_bytes, media_type=media_type, headers=headers)
    except Exception as e:
        await rollback_daily_quota(current_user.id, char_count)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        logger.error(f"Speech synthesis failure: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dịch vụ tổng hợp âm thanh tạm thời không khả dụng, vui lòng thử lại sau.",
        )


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
    ref_audio_path = None

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

        npz_file = CLONED_VOICES_DIR / f"{db_voice.id}.npz"
        if npz_file.exists():
            ref_npz_base64 = base64.b64encode(npz_file.read_bytes()).decode("utf-8")
        elif db_voice.ref_audio_path:
            ref_audio_path = db_voice.ref_audio_path

    if not sample_text:
        sample_text = "Xin chào! Đây là bản nghe thử được kết xuất trực tiếp bằng công nghệ AI Neural 48kHz."

    char_count = len(sample_text)

    # 1. Check max preview character limit from settings
    max_preview_limit = settings_manager.get_settings().concurrency.max_preview_char_limit
    if char_count > max_preview_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Văn bản nghe thử vượt quá giới hạn tối đa cho phép ({char_count}/{max_preview_limit} ký tự).",
        )

    # 2. Check remaining daily quota
    daily_limit = settings_manager.get_settings().concurrency.daily_tts_char_limit
    current_used = await get_daily_usage(current_user.id)
    remaining_chars = max(0, daily_limit - current_used)
    if char_count > remaining_chars:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Hạn mức ký tự còn lại hôm nay không đủ ({remaining_chars:,} ký tự còn lại, yêu cầu {char_count:,} ký tự). Hạn mức sẽ được làm mới vào 00:00.",
        )

    # 3. Check rate limit
    if not await check_rate_limit(current_user.id):
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

    # 4. Cache MISS: Deduct daily quota atomically
    if not await check_and_increment_daily_quota(current_user.id, char_count, limit=daily_limit):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Bạn đã sử dụng hết hạn mức {daily_limit:,} ký tự hôm nay. Hạn mức sẽ được làm mới vào 00:00.",
        )

    t0 = time.perf_counter()
    try:
        audio_bytes, exec_sec, worker_id = await model_dispatcher.synthesize(
            text=sample_text,
            voice=voice_id,
            speed=request.speed or 1.0,
            pitch=request.pitch or 0,
            emotion=request.emotion or 85,
            response_format="wav",
            ref_audio_path=ref_audio_path,
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
    except Exception as e:
        await rollback_daily_quota(current_user.id, char_count)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        logger.error(f"Preview synthesis error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không thể kết xuất bản nghe thử, dịch vụ tổng hợp tạm thời không khả dụng.",
        )
