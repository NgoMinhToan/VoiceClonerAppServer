import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Auth Schemas
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=6)
    full_name: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_REGEX.match(v):
            raise ValueError("Email không hợp lệ")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    avatar_url: Optional[str] = None
    auth_provider: str
    max_cloned_slots: int
    cloned_voices_count: int


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfileResponse(BaseModel):
    id: str
    email: str
    full_name: str
    avatar_url: Optional[str] = None
    auth_provider: str
    cloned_voices_count: int
    max_cloned_slots: int
    daily_tts_chars_used: int
    daily_tts_chars_limit: int


# ---------------------------------------------------------------------------
# Voice & Synthesis Schemas
# ---------------------------------------------------------------------------
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
    sample_text: str = "Chào bạn! Đây là bản nghe thử được kết xuất trực tiếp bằng công nghệ AI Neural Engine."
    is_builtin: bool = True
    ref_audio_path: Optional[str] = None
    status: Optional[str] = "ready"
    created_at: Optional[str] = None


class VoiceListResponse(BaseModel):
    status: str = "success"
    count: int = 0
    cloned_count: int = 0
    max_slots: int = 0
    data: List[VoiceProfileModel]


class VoiceUpdateRequest(BaseModel):
    name: Optional[str] = None
    icon_name: Optional[str] = None
    speed: Optional[float] = None
    pitch: Optional[int] = None
    emotion: Optional[int] = None
    denoise: Optional[bool] = None
    smart_pause: Optional[bool] = None
    category: Optional[str] = None
    accent_color_hex: Optional[int] = None


class CloneVoiceResponse(BaseModel):
    status: str = "success"
    message: str
    voice: VoiceProfileModel


class SynthesisRequest(BaseModel):
    model: str = Field(default="vieneu-v3-turbo")
    input: Optional[str] = Field(default=None)
    text: Optional[str] = Field(default=None)
    voice: Optional[str] = Field(default=None)
    voice_id: Optional[str] = Field(default=None)
    response_format: Optional[Literal["wav", "pcm"]] = Field(None)
    format: Optional[Literal["wav", "pcm"]] = Field("wav")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: int = Field(default=0, ge=-12, le=12)
    emotion: int = Field(default=0, ge=0, le=100)
    emotion_level: Optional[int] = Field(default=None, ge=0, le=100)
    denoise: bool = Field(default=True)
    smart_pause: bool = Field(default=True)
    ref_audio_path: Optional[str] = Field(None)
    ref_npz_path: Optional[str] = Field(None)
    ref_npz_base64: Optional[str] = Field(None)


class PreviewSpeechRequest(BaseModel):
    voice_id: str
    text: Optional[str] = None
    speed: Optional[float] = 1.0
    pitch: Optional[int] = 0
    emotion: Optional[int] = 85


# ---------------------------------------------------------------------------
# System Status Schemas
# ---------------------------------------------------------------------------
class SystemHardwareInfo(BaseModel):
    device_type: str = "CPU"
    device_name: str = "Intel / AMD"
    threads: int = 8
    memory_used_mb: int = 0
    temperature_celsius: int = 42


class SystemBenchmarks(BaseModel):
    avg_ttfa_ms: int = 125
    active_streams: int = 0
    max_concurrent_streams: int = 16


class SystemStatusResponse(BaseModel):
    status: str = "online"
    engine: str = "VieNeu-TTS-v3-Turbo"
    backend: str = "ONNX Runtime"
    sample_rate: int = 48000
    hardware: SystemHardwareInfo
    benchmarks: SystemBenchmarks
