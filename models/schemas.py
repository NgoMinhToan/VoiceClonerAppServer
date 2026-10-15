from datetime import datetime, timezone
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class VoiceProfileModel(BaseModel):
    id: str
    user_id: Optional[str] = None
    name: str
    tag: str = "Cloud AI"
    spec: str = "48kHz • HQ"
    lang: str = "vi"
    accent: str = "Chuẩn Nam Bắc"
    gender: str = "male"
    description: str = ""
    is_default: bool = False
    speed: float = 1.0
    pitch: int = 0
    emotion: int = 85
    denoise: bool = True
    smart_pause: bool = True
    category: str = "Thuyết minh"
    accent_color_hex: int = 0xFF4648D4
    icon_name: str = "graphic_eq"
    sample_text: str = "Chào bạn! Đây là giọng đọc mẫu chuẩn 48kHz."
    is_builtin: bool = True
    ref_audio_path: Optional[str] = None
    status: str = "ready"
    created_at: Optional[str] = None


class SynthesisRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice: Optional[str] = Field("minh_duc")
    voice_id: Optional[str] = Field(None)
    speed: float = Field(1.0, ge=0.5, le=2.0)
    pitch: int = Field(0, ge=-12, le=12)
    emotion: int = Field(0, ge=0, le=100)
    denoise: bool = Field(True)
    smart_pause: bool = Field(True)
    format: Optional[Literal["wav", "pcm"]] = Field("wav")
    response_format: Optional[Literal["wav", "pcm"]] = Field(None)


class PreviewSpeechRequest(BaseModel):
    voice_id: str
    text: Optional[str] = None
