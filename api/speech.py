import base64
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.database import get_db
from VoiceClonerAppServer.core.redis_client import (
    check_and_increment_daily_quota,
    check_rate_limit,
    rollback_daily_quota,
)
from VoiceClonerAppServer.core.security import get_optional_user, verify_client_api_key
from VoiceClonerAppServer.core.settings_manager import ClientApiKeyConfig, settings_manager
from VoiceClonerAppServer.models.db_models import User, Voice
from VoiceClonerAppServer.models.schemas import PreviewSpeechRequest, SynthesisRequest
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
from VoiceClonerAppServer.services.usage_tracker import usage_tracker
from VoiceClonerAppServer.services.voice_service import CLONED_VOICES_DIR, voice_service

logger = logging.getLogger("VoiceClonerAppServer.SpeechAPI")

router = APIRouter(tags=["Speech"])


@router.post("/v1/audio/speech")
async def create_speech(
    request: SynthesisRequest,
    req_raw: Request,
    user: Optional[User] = Depends(get_optional_user),
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

    if not user and not client_key:
        # Check if settings require client key
        if settings_manager.get_settings().security.require_client_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Client API key required. Please provide a valid X-API-Key header or Bearer Token.",
                headers={"WWW-Authenticate": "Bearer, ApiKey"},
            )

    # 2. Parse text input
    input_text = (request.input or request.text or "").strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="Văn bản cần tổng hợp không được để trống.")

    char_count = len(input_text)
    voice_name = request.voice_id or request.voice or "minh_duc"
    response_format = (request.response_format or request.format or "wav").lower()

    # 3. Check rate limit & user daily quota if authenticated
    if user:
        if not await check_rate_limit(user.id):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau.",
            )

        daily_limit = settings_manager.get_settings().concurrency.daily_tts_char_limit
        if not await check_and_increment_daily_quota(user.id, char_count, limit=daily_limit):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Bạn đã sử dụng hết hạn mức {daily_limit:,} ký tự hôm nay. Hạn mức sẽ được làm mới vào 00:00.",
            )

    # 4. Resolve voice and cloned embeddings
    ref_npz_base64 = request.ref_npz_base64
    ref_audio_path = request.ref_audio_path

    # Check if voice_name refers to a database cloned voice
    stmt = select(Voice).where(Voice.id == voice_name)
    res = await db.execute(stmt)
    db_voice = res.scalar_one_or_none()

    if db_voice and not db_voice.is_builtin:
        if not user or db_voice.user_id != user.id:
            if user:
                await rollback_daily_quota(user.id, char_count)
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

        # Asynchronous usage logging
        client_id = client_key.id if client_key else (user.id if user else "anonymous")
        client_name = client_key.name if client_key else (user.full_name if user else "Anonymous Client")
        await usage_tracker.log_usage(
            client_key_id=client_id,
            client_key_name=client_name,
            endpoint="/v1/audio/speech",
            model_worker_id=worker_id,
            char_count=char_count,
            latency_ms=duration_ms,
            status_code=200,
        )

        media_type = "audio/pcm" if response_format == "pcm" else "audio/wav"
        headers = {
            "Content-Type": media_type,
            "X-Sample-Rate": "48000",
            "X-Execution-Time-Ms": f"{duration_ms:.2f}",
            "X-Model-Worker": worker_id,
        }
        return Response(content=audio_bytes, media_type=media_type, headers=headers)
    except Exception as e:
        if user:
            await rollback_daily_quota(user.id, char_count)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        logger.error(f"Speech synthesis failure: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dịch vụ tổng hợp âm thanh tạm thời không khả dụng, vui lòng thử lại sau.",
        )


@router.post("/v1/voices/preview")
async def preview_voice(
    request: PreviewSpeechRequest,
    user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Quick preview playback for voice catalog.
    Uses sample text or provided text, returns 48kHz WAV audio.
    """
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

        if not db_voice.is_builtin and (not user or db_voice.user_id != user.id):
            raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

        if not sample_text:
            sample_text = db_voice.sample_text or "Xin chào, đây là bản nghe thử giọng nói."

        npz_file = CLONED_VOICES_DIR / f"{db_voice.id}.npz"
        if npz_file.exists():
            ref_npz_base64 = base64.b64encode(npz_file.read_bytes()).decode("utf-8")
        elif db_voice.ref_audio_path:
            ref_audio_path = db_voice.ref_audio_path

    if not sample_text:
        sample_text = "Xin chào! Đây là bản nghe thử được kết xuất trực tiếp bằng công nghệ AI VieNeu-TTS 48kHz."

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
        return Response(content=audio_bytes, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Preview synthesis error: {e}")
        raise HTTPException(status_code=500, detail="Không thể kết xuất bản nghe thử.")
