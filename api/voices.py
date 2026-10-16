import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.core.database import get_db
from VoiceClonerAppServer.core.security import get_current_user, get_optional_user
from VoiceClonerAppServer.models.db_models import User, Voice, utc_now
from VoiceClonerAppServer.models.schemas import (
    CloneVoiceResponse,
    VoiceListResponse,
    VoiceProfileModel,
    VoiceUpdateRequest,
)
from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher
from VoiceClonerAppServer.services.voice_service import CLONED_VOICES_DIR, voice_service

logger = logging.getLogger("VoiceClonerAppServer.VoicesAPI")

router = APIRouter(prefix="/v1/voices", tags=["Voices"])


def voice_db_to_profile(voice: Voice) -> VoiceProfileModel:
    """Helper to convert database Voice model to API VoiceProfileModel."""
    return VoiceProfileModel(
        id=voice.id,
        user_id=voice.user_id,
        name=voice.name,
        tag=voice.tag or "Cloud AI",
        spec=voice.spec or "48kHz • HQ",
        lang=voice.lang or "vi",
        accent=voice.accent or "Chuẩn Nam Bắc",
        gender=voice.gender or "male",
        description=voice.description or "",
        is_default=bool(voice.is_default),
        speed=float(voice.speed if voice.speed is not None else 1.0),
        pitch=int(voice.pitch if voice.pitch is not None else 0),
        emotion=int(voice.emotion if voice.emotion is not None else 85),
        denoise=bool(voice.denoise if voice.denoise is not None else True),
        smart_pause=bool(voice.smart_pause if voice.smart_pause is not None else True),
        category=voice.category or "Thuyết minh",
        accent_color_hex=int(voice.accent_color_hex if voice.accent_color_hex is not None else 0xFF4648D4),
        icon_name=voice.icon_name or "graphic_eq",
        sample_text=voice.sample_text or "Chào bạn!",
        is_builtin=bool(voice.is_builtin),
        ref_audio_path=voice.ref_audio_path,
        status=voice.status or "ready",
        created_at=voice.created_at.isoformat() if voice.created_at else None,
    )


@router.get("", response_model=VoiceListResponse)
async def list_voices(
    lang: Optional[str] = None,
    user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve all voice profiles:
    - If authenticated: 23 built-in presets + user's own cloned voices.
    - If unauthenticated / guest: 23 built-in presets only.
    """
    builtin_voices = voice_service.list_builtin_voices()
    if lang:
        builtin_voices = [v for v in builtin_voices if v.lang == lang]

    cloned_profiles = []
    cloned_count = 0
    max_slots = 0

    if user:
        stmt = (
            select(Voice)
            .where(Voice.user_id == user.id)
            .order_by(Voice.created_at.desc())
        )
        if lang:
            stmt = stmt.where(Voice.lang == lang)
        res = await db.execute(stmt)
        user_voices = res.scalars().all()
        cloned_profiles = [voice_db_to_profile(v) for v in user_voices]
        cloned_count = len(cloned_profiles)
        max_slots = user.max_cloned_slots

    all_voices = cloned_profiles + builtin_voices

    return VoiceListResponse(
        status="success",
        count=len(all_voices),
        cloned_count=cloned_count,
        max_slots=max_slots,
        data=all_voices,
    )


@router.get("/{voice_id}", response_model=VoiceProfileModel)
async def get_voice_detail(
    voice_id: str,
    user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve details for a specific voice profile (presets or user's custom clone)."""
    # 1. Check presets
    builtin = voice_service.get_voice_by_id(voice_id)
    if builtin:
        return builtin

    # 2. Check DB
    stmt = select(Voice).where(Voice.id == voice_id)
    res = await db.execute(stmt)
    voice = res.scalar_one_or_none()
    if not voice:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

    if voice.is_builtin:
        return voice_db_to_profile(voice)

    if user is None or voice.user_id != user.id:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

    return voice_db_to_profile(voice)


@router.get("/{voice_id}/audio")
async def get_voice_audio(
    voice_id: str,
    user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve reference audio file for custom cloned voices with ownership verification."""
    stmt = select(Voice).where(Voice.id == voice_id)
    res = await db.execute(stmt)
    voice = res.scalar_one_or_none()
    if not voice:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

    if not voice.is_builtin:
        if user is None or voice.user_id != user.id:
            raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

    if not voice.ref_audio_path:
        raise HTTPException(status_code=404, detail="Không có tệp âm thanh tham chiếu.")

    audio_path = Path(voice.ref_audio_path).resolve()
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Tệp âm thanh không tồn tại trên ổ đĩa.")

    return FileResponse(str(audio_path), media_type="audio/wav")


@router.post("/clone", response_model=CloneVoiceResponse)
async def clone_voice(
    audio_file: UploadFile = File(..., description="WAV/M4A 3-15s audio reference clip"),
    name: str = Form(..., description="Voice profile display name"),
    gender: str = Form("male", description="'male' or 'female'"),
    region: str = Form("bac", description="'bac', 'trung', or 'nam'"),
    category: str = Form("Thuyết minh", description="Category for voice usage"),
    denoise: bool = Form(True, description="Clean noise before embedding extraction"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Instant Voice Cloning endpoint:
    Checks user slot quota, saves audio sample, forwards to upstream worker to extract
    .npz speaker embedding, and stores records in PostgreSQL/SQLite.
    """
    if not audio_file.filename:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp tệp âm thanh hợp lệ.")

    # 1. Quota check
    res = await db.execute(select(func.count(Voice.id)).where(Voice.user_id == current_user.id))
    cloned_count = res.scalar() or 0
    if cloned_count >= current_user.max_cloned_slots:
        raise HTTPException(
            status_code=403,
            detail=f"Bạn đã sử dụng hết {cloned_count}/{current_user.max_cloned_slots} slot giọng clone. Vui lòng xóa giọng cũ để tạo mới.",
        )

    # 2. File paths setup
    voice_id = f"cloned_{uuid.uuid4().hex[:8]}"
    ext = Path(audio_file.filename).suffix.lower() or ".wav"
    if ext not in [".wav", ".m4a", ".mp3", ".ogg", ".flac"]:
        ext = ".wav"

    saved_audio_filename = f"{voice_id}{ext}"
    saved_path = CLONED_VOICES_DIR / saved_audio_filename
    saved_npz_path = CLONED_VOICES_DIR / f"{voice_id}.npz"

    region_labels = {
        "bac": "Miền Bắc",
        "trung": "Miền Trung",
        "nam": "Miền Nam",
    }
    accent_label = region_labels.get(region.lower(), "Miền Bắc")
    color_hex = 0xFF4648D4 if gender.lower() == "male" else 0xFFF43F5E
    icon_name = "graphic_eq" if gender.lower() == "male" else "record_voice_over"

    # 3. Read & save audio content
    content = await audio_file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Tệp âm thanh trống.")

    saved_path.write_bytes(content)

    # 4. Extract speaker embedding via upstream worker
    try:
        npz_bytes = await model_dispatcher.extract_embedding(content, filename=saved_audio_filename)
        saved_npz_path.write_bytes(npz_bytes)
        logger.info(f"Speaker embedding extracted and saved to: {saved_npz_path}")
    except Exception as e:
        logger.error(f"Error extracting embedding for {voice_id}: {e}")
        # Even if remote extraction fails, we can still proceed with ref_audio_path

    # 5. Insert Voice record into DB
    voice_record = Voice(
        id=voice_id,
        user_id=current_user.id,
        name=name.strip(),
        tag="Cloned Voice",
        spec="Instant Clone • 48kHz",
        lang="vi",
        accent=accent_label,
        gender=gender.lower(),
        description=f"Giọng nhân bản từ phòng thu • Phương ngữ {accent_label}",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        denoise=denoise,
        smart_pause=True,
        category=category,
        accent_color_hex=color_hex,
        icon_name=icon_name,
        sample_text=f"Xin chào, đây là giọng nói nhân bản của {name.strip()}.",
        is_builtin=False,
        ref_audio_path=str(saved_path),
        status="ready",
        created_at=utc_now(),
    )
    db.add(voice_record)
    await db.commit()
    await db.refresh(voice_record)

    profile = voice_db_to_profile(voice_record)
    return CloneVoiceResponse(
        status="success",
        message=f"Đã nhân bản thành công giọng nói '{name}'",
        voice=profile,
    )


@router.put("/{voice_id}", response_model=VoiceProfileModel)
async def update_voice(
    voice_id: str,
    req: VoiceUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update settings of a custom cloned voice profile."""
    stmt = select(Voice).where(Voice.id == voice_id)
    res = await db.execute(stmt)
    voice = res.scalar_one_or_none()
    if not voice:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

    if voice.is_builtin:
        raise HTTPException(status_code=400, detail="Không thể chỉnh sửa giọng nói có sẵn.")

    if voice.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

    update_dict = req.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        if hasattr(voice, field) and val is not None:
            setattr(voice, field, val)

    await db.commit()
    await db.refresh(voice)
    return voice_db_to_profile(voice)


@router.delete("/{voice_id}")
async def delete_voice(
    voice_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a custom cloned voice profile and free up a clone slot."""
    stmt = select(Voice).where(Voice.id == voice_id)
    res = await db.execute(stmt)
    voice = res.scalar_one_or_none()
    if not voice:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

    if voice.is_builtin:
        raise HTTPException(status_code=400, detail="Không thể xóa giọng nói có sẵn.")

    if voice.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng nói.")

    if voice.ref_audio_path:
        audio_path = Path(voice.ref_audio_path)
        if audio_path.exists():
            try:
                audio_path.unlink()
            except Exception as e:
                logger.warning(f"Could not remove audio file {audio_path}: {e}")

    npz_file = CLONED_VOICES_DIR / f"{voice_id}.npz"
    if npz_file.exists():
        try:
            npz_file.unlink()
        except Exception as e:
            logger.warning(f"Could not remove npz file {npz_file}: {e}")

    await db.delete(voice)
    await db.commit()
    return {"status": "success", "message": f"Đã xóa thành công giọng nói {voice_id}"}
