import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
from VoiceClonerAppServer.models.schemas import VoiceProfileModel, VoiceUpdateRequest

logger = logging.getLogger("VoiceClonerAppServer.VoiceService")

STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
CLONED_VOICES_DIR = STORAGE_DIR / "cloned_voices"
CLONED_VOICES_DIR.mkdir(parents=True, exist_ok=True)

# Cold-boot offline fallback voices (retained strictly for offline cold boot resilience before any worker is discovered)
FALLBACK_PRESET_VOICES: List[VoiceProfileModel] = [
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
        sample_text="Chào bạn! Đây là bản nghe thử được kết xuất trực tiếp bằng công nghệ AI Neural Engine v3.2.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="pham_tuyen",
        name="Phạm Tuyên",
        tag="Cloud AI",
        spec="48kHz • Trầm ấm",
        lang="vi",
        accent="Nam Miền Bắc",
        gender="male",
        description="Điềm đạm, ấm áp, giọng đọc tư vấn & tự nhiên",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=82,
        category="Thuyết minh",
        accent_color_hex=0xFF6366F1,
        icon_name="graphic_eq",
        sample_text="Xin chào, tôi là bác sĩ Tuyên, hôm nay tôi sẽ chia sẻ với bạn về cách chăm sóc sức khỏe.",
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
    VoiceProfileModel(
        id="xuan_vinh",
        name="Xuân Vĩnh",
        tag="Cloud AI",
        spec="48kHz • HQ",
        lang="vi",
        accent="Nam Miền Bắc",
        gender="male",
        description="Tự nhiên, phong thái lịch lãm, đời thường",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Thuyết minh",
        accent_color_hex=0xFF2563EB,
        icon_name="record_voice_over",
        sample_text="Kính chào quý vị, đây là chương trình giới thiệu các giải pháp công nghệ mới.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="thanh_binh",
        name="Thanh Bình",
        tag="Cloud AI",
        spec="48kHz • Diễn cảm",
        lang="vi",
        accent="Nam Miền Bắc",
        gender="male",
        description="Kể chuyện diễn cảm, giàu cảm xúc",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=88,
        category="Kể chuyện",
        accent_color_hex=0xFF1D4ED8,
        icon_name="auto_stories",
        sample_text="Đêm ấy trăng sáng vằng vặc trên dòng sông, tiếng gió thì thầm như lời tự tình.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="truc_ly",
        name="Trúc Ly",
        tag="Cloud AI",
        spec="48kHz • Tự nhiên",
        lang="vi",
        accent="Nữ Miền Bắc",
        gender="female",
        description="Phát thanh viên, mượt mà, chuẩn truyền thanh",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Thuyết minh",
        accent_color_hex=0xFF10B981,
        icon_name="record_voice_over",
        sample_text="Bản tin tóm tắt xu hướng thiết kế âm thanh và công nghệ số tuần này.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="ngoc_linh",
        name="Ngọc Linh",
        tag="Cloud AI",
        spec="48kHz • Truyền cảm",
        lang="vi",
        accent="Nữ Miền Bắc",
        gender="female",
        description="Kể chuyện sâu lắng, ấm áp, truyền cảm",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=88,
        category="Kể chuyện",
        accent_color_hex=0xFF059669,
        icon_name="auto_stories",
        sample_text="Thời gian trôi qua thật nhanh, những kỷ niệm xưa vẫn còn nguyên vẹn trong ký ức.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="doan_trang",
        name="Đoan Trang",
        tag="Cloud AI",
        spec="48kHz • Thanh lịch",
        lang="vi",
        accent="Nữ Miền Bắc",
        gender="female",
        description="Tự nhiên, thanh lịch, phong cách hội thoại nhẹ nhàng",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Thuyết minh",
        accent_color_hex=0xFF0D9488,
        icon_name="record_voice_over",
        sample_text="Chào bạn, hôm nay chúng ta sẽ cùng tìm hiểu về nghệ thuật sống cân bằng.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="mai_anh",
        name="Mai Anh",
        tag="Cloud AI",
        spec="48kHz • HQ",
        lang="vi",
        accent="Nữ Miền Bắc",
        gender="female",
        description="Tươi sáng, năng động, podcast tin tức • 48kHz",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=90,
        category="Podcast",
        accent_color_hex=0xFF645EFB,
        icon_name="record_voice_over",
        sample_text="Chào cả nhà, chúc mọi người một ngày mới tràn đầy năng lượng và tích cực!",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="thuc_doan",
        name="Thục Đoan",
        tag="Cloud AI",
        spec="48kHz • Truyền cảm",
        lang="vi",
        accent="Nữ Miền Nam",
        gender="female",
        description="Kể chuyện nhẹ nhàng, giọng miền Nam dịu dàng",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=88,
        category="Kể chuyện",
        accent_color_hex=0xFFEC4899,
        icon_name="auto_stories",
        sample_text="Những buổi chiều phương Nam với cơn mưa rào bất chợt luôn để lại nhiều thương nhớ.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="minh_triet",
        name="Minh Triết",
        tag="Cloud AI",
        spec="48kHz • Rành mạch",
        lang="vi",
        accent="Nam Miền Nam",
        gender="male",
        description="Tin tức rành mạch, phong cách thời sự hiện đại",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Tin tức",
        accent_color_hex=0xFF0284C7,
        icon_name="graphic_eq",
        sample_text="Bản tin kinh tế thị trường hôm nay cập nhật những diễn biến mới nhất trong nước và quốc tế.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="thuy_dung",
        name="Thùy Dung",
        tag="Cloud AI",
        spec="48kHz • Truyền cảm",
        lang="vi",
        accent="Nữ Miền Nam",
        gender="female",
        description="Ngọt ngào, tự nhiên, thích hợp cho video giải trí & sách nói",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=88,
        category="Đọc sách nói",
        accent_color_hex=0xFFF59E0B,
        icon_name="auto_stories",
        sample_text="Kính chào quý vị, đây là chương trình đọc sách trên sóng âm thanh trực tuyến.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="quang_son",
        name="Quang Sơn",
        tag="Cloud AI",
        spec="48kHz • Đặc trưng",
        lang="vi",
        accent="Nam Miền Trung",
        gender="male",
        description="Trầm lắng, sâu sắc, ngữ điệu Miền Trung cuốn hút",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=80,
        category="Đọc sách nói",
        accent_color_hex=0xFFD97706,
        icon_name="podcasts",
        sample_text="Vùng đất miền Trung mang đậm những giá trị văn hóa và lịch sử lâu đời.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="ngoc_tran",
        name="Ngọc Trân",
        tag="Cloud AI",
        spec="48kHz • Tự nhiên",
        lang="vi",
        accent="Nữ Miền Trung",
        gender="female",
        description="Tự nhiên, đằm thắm, mộc mạc phong cách miền Trung",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Thuyết minh",
        accent_color_hex=0xFFB45309,
        icon_name="record_voice_over",
        sample_text="Huế vào thu đẹp dịu dàng với tà áo dài và dòng sông Hương êm đềm trôi.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="my_duyen",
        name="Mỹ Duyên",
        tag="Cloud AI",
        spec="48kHz • Diễn cảm",
        lang="vi",
        accent="Nữ Miền Nam",
        gender="female",
        description="Đọc truyện diễn cảm, lôi cuốn, giọng miền Nam thân thương",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=90,
        category="Đọc sách nói",
        accent_color_hex=0xFF8B5CF6,
        icon_name="auto_stories",
        sample_text="Câu chuyện cổ tích bắt đầu từ một ngôi làng nhỏ bên bờ sông rợp bóng dừa xanh.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="quynh_anh",
        name="Quỳnh Anh",
        tag="Cloud AI",
        spec="48kHz • Văn học",
        lang="vi",
        accent="Nữ Miền Bắc",
        gender="female",
        description="Đọc truyện văn học, giàu nhạc điệu và cảm xúc",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=88,
        category="Đọc sách nói",
        accent_color_hex=0xFF7C3AED,
        icon_name="auto_stories",
        sample_text="Mùa thu Hà Nội se lạnh, thoang thoảng hương hoa sữa ngọt ngào góc phố cổ.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="duc_tri",
        name="Đức Trí",
        tag="Cloud AI",
        spec="48kHz • Cảm xúc",
        lang="vi",
        accent="Nam Miền Nam",
        gender="male",
        description="Đọc truyện cảm xúc, ấm áp, sâu lắng",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=86,
        category="Đọc sách nói",
        accent_color_hex=0xFF6D28D9,
        icon_name="auto_stories",
        sample_text="Có những chuyến đi không phải để đến một miền đất mới, mà là tìm lại chính mình.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="kim_thanh",
        name="Kim Thanh",
        tag="Cloud AI",
        spec="48kHz • Truyền cảm",
        lang="vi",
        accent="Nữ Miền Nam",
        gender="female",
        description="Đọc truyện truyền cảm, giọng Nam Bộ ngọt ngào",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=88,
        category="Đọc sách nói",
        accent_color_hex=0xFFDB2777,
        icon_name="auto_stories",
        sample_text="Mảnh đất phù sa miền Tây luôn hào phóng mang lại nguồn sống tươi vui cho muôn người.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="ngoc_huyen",
        name="Ngọc Huyền",
        tag="Cloud AI",
        spec="48kHz • Truyền cảm",
        lang="vi",
        accent="Nữ Miền Bắc",
        gender="female",
        description="Kể chuyện thiếu nhi, nhẹ nhàng, tự nhiên",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=92,
        category="Kể chuyện",
        accent_color_hex=0xFFF43F5E,
        icon_name="auto_stories",
        sample_text="Chào các bạn nhỏ, hôm nay chúng mình cùng lắng nghe một câu chuyện ngụ ngôn thú vị nhé.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="adam",
        name="Adam",
        tag="Cloud AI",
        spec="48kHz • Hiện đại",
        lang="vi",
        accent="Song Ngữ US-VN",
        gender="male",
        description="Hiện đại, tự nhiên, phong cách song ngữ",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=80,
        category="Podcast",
        accent_color_hex=0xFF06B6D4,
        icon_name="translate",
        sample_text="Xin chào các bạn, chào mừng các bạn đến với công nghệ tổng hợp giọng nói thế hệ mới.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="manh_dung",
        name="Mạnh Dũng",
        tag="Cloud AI",
        spec="48kHz • Tự nhiên",
        lang="vi",
        accent="Nam Miền Bắc",
        gender="male",
        description="Tự nhiên, đời thường, chân thực",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=84,
        category="Thuyết minh",
        accent_color_hex=0xFF4B5563,
        icon_name="record_voice_over",
        sample_text="Cuộc sống thường ngày luôn chứa đựng những điều giản dị nhưng vô cùng ý nghĩa.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="minh_quan",
        name="Minh Quân",
        tag="Cloud AI",
        spec="48kHz • Chững chạc",
        lang="vi",
        accent="Nam Miền Bắc",
        gender="male",
        description="Tự nhiên, chững chạc, uy tín",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=85,
        category="Thuyết minh",
        accent_color_hex=0xFF374151,
        icon_name="record_voice_over",
        sample_text="Để đạt được thành công bền vững, sự kiên trì và kỷ luật là hai yếu tố then chốt.",
        is_builtin=True,
    ),
    VoiceProfileModel(
        id="anh_khoi",
        name="Anh Khôi",
        tag="Cloud AI",
        spec="48kHz • Lôi cuốn",
        lang="vi",
        accent="Nam Miền Bắc",
        gender="male",
        description="Kể chuyện lôi cuốn, truyền cảm hứng",
        is_default=False,
        speed=1.0,
        pitch=0,
        emotion=87,
        category="Kể chuyện",
        accent_color_hex=0xFF1F2937,
        icon_name="auto_stories",
        sample_text="Mỗi thử thách trong cuộc sống là một cơ hội tuyệt vời để chúng ta trưởng thành hơn.",
        is_builtin=True,
    ),
]


class VoiceService:
    def __init__(self):
        self._lock = threading.RLock()
        self._fallback_presets: Dict[str, VoiceProfileModel] = {v.id: v for v in FALLBACK_PRESET_VOICES}
        self._discovered_presets: Dict[str, VoiceProfileModel] = {}
        self._is_discovered: bool = False

    def _enrich_worker_voice(self, v_item: Dict[str, Any]) -> VoiceProfileModel:
        """Enrich raw worker voice metadata with UI styling attributes."""
        voice_id = str(v_item.get("id") or "").strip().lower()
        name = str(v_item.get("name") or voice_id).strip()

        # If voice matches known curated fallback, inherit rich visuals
        if voice_id in self._fallback_presets:
            curated = self._fallback_presets[voice_id]
            return curated.model_copy(update={
                "name": name,
                "description": v_item.get("description") or curated.description,
                "gender": v_item.get("gender") or curated.gender,
            })

        # Otherwise synthesize sensible generic UI defaults
        gender = str(v_item.get("gender", "male")).lower()
        desc = str(v_item.get("description", ""))
        style = str(v_item.get("style", "tu_nhien"))
        sample_rate = int(v_item.get("sample_rate", 48000))
        color_hex = 0xFF4648D4 if gender == "male" else 0xFFEC4899
        icon = "graphic_eq" if gender == "male" else "record_voice_over"

        return VoiceProfileModel(
            id=voice_id,
            name=name,
            tag="AI Voice",
            spec=f"{sample_rate // 1000}kHz • HQ",
            lang="vi",
            accent="Chuẩn Nam Bắc" if gender == "male" else "Chuẩn Nữ Bắc",
            gender=gender,
            description=desc or f"Giọng đọc AI tiêu chuẩn ({style})",
            is_default=False,
            speed=1.0,
            pitch=0,
            emotion=85,
            category="Thuyết minh",
            accent_color_hex=color_hex,
            icon_name=icon,
            sample_text=f"Chào bạn! Tôi là {name}, đây là bản nghe thử được kết xuất trực tiếp bằng công nghệ AI Neural {sample_rate // 1000}kHz.",
            is_builtin=True,
        )

    def update_discovered_voices(self, dynamic_catalog: Dict[str, VoiceProfileModel]) -> None:
        """Atomically update the discovered preset voices catalog."""
        with self._lock:
            self._discovered_presets = dict(dynamic_catalog)
            self._is_discovered = bool(dynamic_catalog)
            logger.info(f"Updated dynamic voice catalog with {len(self._discovered_presets)} preset voices.")

    async def discover_all_workers(self, http_client: Optional[httpx.AsyncClient] = None) -> int:
        """
        Dynamically query all active upstream model workers via GET /internal/v1/voices,
        aggregate & deduplicate voices, and register capabilities in ModelDispatcher.
        """
        from VoiceClonerAppServer.core.settings_manager import settings_manager
        from VoiceClonerAppServer.services.model_dispatcher import model_dispatcher

        settings = settings_manager.get_settings()
        workers = [w for w in settings.model_workers if w.is_active]
        if not workers:
            return len(self.list_builtin_voices())

        close_client = False
        if http_client is None:
            http_client = httpx.AsyncClient(timeout=5.0)
            close_client = True

        aggregated_catalog: Dict[str, VoiceProfileModel] = {}
        try:
            for w in workers:
                clean_url = w.url.rstrip("/")
                headers = {"X-API-Key": w.api_key} if w.api_key else {}
                try:
                    # Try /internal/v1/voices first, fallback to /v1/voices
                    res = await http_client.get(f"{clean_url}/internal/v1/voices", headers=headers)
                    if res.status_code != 200:
                        res = await http_client.get(f"{clean_url}/v1/voices", headers=headers)

                    if res.status_code == 200:
                        data = res.json()
                        raw_voices = data.get("voices") or data.get("data") or (data if isinstance(data, list) else [])
                        supported_ids: Set[str] = set()

                        for item in raw_voices:
                            if isinstance(item, dict):
                                profile = self._enrich_worker_voice(item)
                                aggregated_catalog[profile.id] = profile
                                supported_ids.add(profile.id.lower())
                                supported_ids.add(profile.name.lower())

                        # Register capabilities in model_dispatcher
                        model_dispatcher.register_worker_capabilities(
                            worker_url=w.url,
                            supported_voices=supported_ids,
                            supports_cloning=True,
                            worker_id=w.id,
                        )
                        logger.info(f"Discovered {len(supported_ids)} voice IDs from worker '{w.id}' ({w.url})")
                except Exception as e:
                    logger.warning(f"Failed to discover voices from worker '{w.id}' ({w.url}): {e}")

            if aggregated_catalog:
                self.update_discovered_voices(aggregated_catalog)
        finally:
            if close_client:
                await http_client.aclose()

        return len(self.list_builtin_voices())

    def list_builtin_voices(self) -> List[VoiceProfileModel]:
        """Return dynamically discovered voices, or fallback presets if no workers discovered yet."""
        with self._lock:
            if self._is_discovered and self._discovered_presets:
                return list(self._discovered_presets.values())
            return list(self._fallback_presets.values())

    def get_voice_by_id(self, voice_id: str) -> Optional[VoiceProfileModel]:
        clean_id = (voice_id or "").strip().lower()
        with self._lock:
            if self._is_discovered and self._discovered_presets:
                if clean_id in self._discovered_presets:
                    return self._discovered_presets[clean_id]
                for v in self._discovered_presets.values():
                    if v.name.lower() == clean_id:
                        return v

            if clean_id in self._fallback_presets:
                return self._fallback_presets[clean_id]
            for v in self._fallback_presets.values():
                if v.name.lower() == clean_id:
                    return v
            return None


voice_service = VoiceService()
