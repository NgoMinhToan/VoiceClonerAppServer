import logging
from typing import Dict, List, Optional
from VoiceClonerAppServer.models.schemas import VoiceProfileModel

logger = logging.getLogger("VoiceClonerAppServer.VoiceService")

BUILTIN_PRESETS = [
    VoiceProfileModel(
        id="minh_duc",
        name="Minh Đức (Mặc định)",
        tag="Cloud AI",
        spec="48kHz • HQ",
        lang="vi",
        accent="Nam Miền Bắc",
        gender="male",
        description="Trầm ấm, thời sự & tin tức • 48kHz",
        is_default=True,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Thuyết minh",
        accent_color_hex=0xFF4648D4,
        icon_name="graphic_eq",
        sample_text="Chào bạn! Đây là bản nghe thử được kết xuất trực tiếp bằng công nghệ AI Neural Engine v3.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="mai_anh",
        name="Mai Anh",
        tag="Cloud AI",
        spec="48kHz • Nữ Tính",
        lang="vi",
        accent="Nữ Miền Bắc",
        gender="female",
        description="Dịu dàng, truyền cảm, giọng đọc radio",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Thuyết minh",
        accent_color_hex=0xFFEC4899,
        icon_name="record_voice_over",
        sample_text="Chào bạn, chúc bạn một ngày mới tràn ngập niềm vui và năng lượng tích cực.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="thai_son",
        name="Thái Sơn",
        tag="Cloud AI",
        spec="48kHz • Nam Tính",
        lang="vi",
        accent="Nam Miền Nam",
        gender="male",
        description="Hào sảng, tự nhiên, phong cách kể chuyện",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Kể chuyện",
        accent_color_hex=0xFF3B82F6,
        icon_name="graphic_eq",
        sample_text="Chào anh chị, chúc mọi người có một ngày làm việc tràn đầy năng lượng.",
        is_builtin=True,
    ),
]


class VoiceService:
    def __init__(self):
        self._presets: Dict[str, VoiceProfileModel] = {v.id: v for v in BUILTIN_PRESETS}

    def list_builtin_voices(self) -> List[VoiceProfileModel]:
        return list(self._presets.values())

    def get_voice_by_id(self, voice_id: str) -> Optional[VoiceProfileModel]:
        return self._presets.get(voice_id)


voice_service = VoiceService()
