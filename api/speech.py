import time
import logging
from fastapi import APIRouter, Depends, HTTPException, Response, status

from VoiceClonerAppServer.core.settings_manager import ClientApiKeyConfig
from VoiceClonerAppServer.core.security import verify_client_api_key
from VoiceClonerAppServer.models.schemas import PreviewSpeechRequest, SynthesisRequest
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
from VoiceClonerAppServer.services.usage_tracker import usage_tracker
from VoiceClonerAppServer.services.voice_service import voice_service

logger = logging.getLogger("VoiceClonerAppServer.SpeechAPI")

router = APIRouter(prefix="/v1", tags=["Speech"])


@router.post("/audio/speech")
async def create_speech(
    request: SynthesisRequest,
    client_key: ClientApiKeyConfig = Depends(verify_client_api_key),
):
    """
    Synthesize speech from text.
    Protected by Client API Key. Standard 48kHz WAV output with usage auditing.
    """
    t0 = time.perf_counter()
    char_count = len(request.text)
    voice_name = request.voice_id or request.voice or "minh_duc"
    response_format = (request.response_format or request.format or "wav").lower()

    try:
        audio_bytes, exec_sec, worker_id = await model_dispatcher.synthesize(
            text=request.text,
            voice=voice_name,
            speed=request.speed,
            pitch=request.pitch,
            emotion=request.emotion,
            denoise=request.denoise,
            smart_pause=request.smart_pause,
            response_format=response_format,
        )

        duration_ms = (time.perf_counter() - t0) * 1000.0

        # Asynchronous usage recording
        await usage_tracker.log_usage(
            client_key_id=client_key.id,
            client_key_name=client_key.name,
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
        duration_ms = (time.perf_counter() - t0) * 1000.0
        await usage_tracker.log_usage(
            client_key_id=client_key.id,
            client_key_name=client_key.name,
            endpoint="/v1/audio/speech",
            model_worker_id="error",
            char_count=char_count,
            latency_ms=duration_ms,
            status_code=500,
        )
        logger.error(f"Speech synthesis failure: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dịch vụ tổng hợp âm thanh tạm thời không khả dụng, vui lòng thử lại sau.",
        )


@router.post("/voices/preview")
async def preview_voice(
    request: PreviewSpeechRequest,
    client_key: ClientApiKeyConfig = Depends(verify_client_api_key),
):
    """Preview sample playback for a voice."""
    preset = voice_service.get_voice_by_id(request.voice_id)
    text = request.text or (preset.sample_text if preset else "Xin chào bạn.")

    t0 = time.perf_counter()
    try:
        audio_bytes, exec_sec, worker_id = await model_dispatcher.synthesize(
            text=text,
            voice=request.voice_id,
        )
        duration_ms = (time.perf_counter() - t0) * 1000.0
        headers = {
            "Content-Type": "audio/wav",
            "X-Sample-Rate": "48000",
            "X-Execution-Time-Ms": f"{duration_ms:.2f}",
        }
        return Response(content=audio_bytes, media_type="audio/wav", headers=headers)
    except Exception as e:
        logger.error(f"Preview synthesis failure: {e}")
        raise HTTPException(status_code=503, detail="Lỗi khi tạo bản nghe thử.")
