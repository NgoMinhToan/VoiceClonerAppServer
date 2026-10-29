import json
import logging
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("VoiceClonerAppServer.SettingsManager")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "settings.json"


class ServerConfig(BaseModel):
    host: str = Field("0.0.0.0", description="IP host lắng nghe")
    port: int = Field(8000, ge=1, le=65535, description="Port HTTP lắng nghe")


class SecurityConfig(BaseModel):
    management_key: str = Field("vc_admin_secret_key_123", description="Key xác thực quản trị (Admin/Management API & Dashboard)")
    require_client_api_key: bool = Field(True, description="Bắt buộc client app/web phải có Client API Key")


class ClientApiKeyConfig(BaseModel):
    id: str = Field(...)
    name: str = Field(...)
    key: str = Field(...)
    is_active: bool = Field(True)
    daily_char_limit: int = Field(50000)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_used_at: Optional[str] = Field(None)


class ModelWorkerConfig(BaseModel):
    id: str = Field(...)
    url: str = Field(...)
    api_key: str = Field("", description="Worker API key gửi kèm header X-API-Key")
    weight: int = Field(1, ge=1, le=100)
    is_active: bool = Field(True)


class ConcurrencyConfig(BaseModel):
    max_concurrent_tts: int = Field(2, ge=1, le=64)
    max_queue_waiters: int = Field(8, ge=0, le=256)
    daily_tts_char_limit: Optional[int] = Field(100000, ge=1000)
    rate_limit_requests_per_minute: Optional[int] = Field(60, ge=1)
    max_preview_char_limit: Optional[int] = Field(250, ge=10, le=5000)
    max_speech_char_limit: Optional[int] = Field(2000, ge=100, le=10000)


class DefaultUserLimitsConfig(BaseModel):
    max_cloned_slots: int = Field(3, ge=1, le=100)
    daily_tts_char_limit: int = Field(50000, ge=1000)
    rate_limit_requests_per_minute: int = Field(60, ge=1, le=1000)
    max_speech_char_limit: int = Field(2000, ge=100, le=10000)
    max_preview_char_limit: int = Field(250, ge=10, le=5000)
    max_concurrent_tts: int = Field(1, ge=1, le=64)
    max_queue_waiters: int = Field(1, ge=0, le=256)


class AudioCacheConfig(BaseModel):
    max_size_mb: int = Field(2048, ge=64)
    ttl_days: int = Field(7, ge=1)
    clean_interval_sec: int = Field(3600, ge=60)


class AppSettings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    client_api_keys: List[ClientApiKeyConfig] = Field(default_factory=list)
    model_workers: List[ModelWorkerConfig] = Field(default_factory=list)
    default_user_limits: DefaultUserLimitsConfig = Field(default_factory=DefaultUserLimitsConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    audio_cache: AudioCacheConfig = Field(default_factory=AudioCacheConfig)


class SettingsManager:
    def __init__(self, settings_path: Optional[Path] = None):
        self._path = Path(os.getenv("CONFIG_PATH", str(settings_path or DEFAULT_SETTINGS_PATH))).resolve()
        self._settings: Optional[AppSettings] = None
        self._on_change_hooks: List[Callable[[AppSettings], None]] = []
        self._last_mtime: float = 0.0
        self._load()

    def register_on_change(self, hook: Callable[[AppSettings], None]):
        self._on_change_hooks.append(hook)

    def _trigger_hooks(self):
        for hook in self._on_change_hooks:
            try:
                hook(self._settings)
            except Exception as e:
                logger.error(f"Error executing settings change hook: {e}")

    def _bootstrap_defaults(self) -> AppSettings:
        default_keys = [
            ClientApiKeyConfig(
                id="key_android_app_default",
                name="Android Mobile App (Production)",
                key="vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
                is_active=True,
                daily_char_limit=50000,
            ),
        ]
        default_workers = [
            ModelWorkerConfig(
                id="worker-primary",
                url="http://model-service:8001",
                api_key="vtts_worker_secret_key_123",
                weight=1,
                is_active=True,
            )
        ]
        return AppSettings(client_api_keys=default_keys, model_workers=default_workers)

    def _check_and_reload(self):
        """Check if settings file on disk was modified externally and reload automatically."""
        if self._path.exists():
            try:
                current_mtime = self._path.stat().st_mtime
                if current_mtime > self._last_mtime:
                    logger.info(f"Detected external modification to {self._path} (mtime {current_mtime} > {self._last_mtime}). Hot-reloading...")
                    self._load()
                    self._trigger_hooks()
            except Exception as e:
                logger.warning(f"Error checking settings mtime: {e}")

    def _load(self):
        if not self._path.exists():
            self._settings = self._bootstrap_defaults()
            self._save_to_disk()
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._settings = AppSettings.model_validate(data)
            if self._path.exists():
                self._last_mtime = self._path.stat().st_mtime
        except Exception as e:
            logger.error(f"Failed to parse settings from {self._path} ({e}). Bootstrapping defaults.")
            self._settings = self._bootstrap_defaults()
            self._save_to_disk()

    def _save_to_disk(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = self._settings.model_dump(mode="json")
        # Atomic file write
        temp_file = self._path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, self._path)
        if self._path.exists():
            self._last_mtime = self._path.stat().st_mtime
        logger.info(f"Settings successfully written to {self._path}")

    def get_settings(self) -> AppSettings:
        self._check_and_reload()
        if self._settings is None:
            self._load()
        return self._settings

    def update_settings(self, updates: Dict[str, Any]) -> AppSettings:
        self._check_and_reload()
        current_data = self._settings.model_dump()
        for key, value in updates.items():
            if isinstance(value, dict) and key in current_data and isinstance(current_data[key], dict):
                current_data[key].update(value)
            else:
                current_data[key] = value

        new_settings = AppSettings.model_validate(current_data)
        self._settings = new_settings
        self._save_to_disk()
        self._trigger_hooks()
        return self._settings

    def generate_client_key(self, name: str, daily_char_limit: int = 50000) -> ClientApiKeyConfig:
        self._check_and_reload()
        key_token = f"vc_live_{secrets.token_hex(16)}"
        key_id = f"key_{secrets.token_hex(6)}"
        key_entry = ClientApiKeyConfig(
            id=key_id,
            name=name,
            key=key_token,
            is_active=True,
            daily_char_limit=daily_char_limit,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._settings.client_api_keys.append(key_entry)
        self._save_to_disk()
        self._trigger_hooks()
        return key_entry

    def revoke_client_key(self, key_id: str) -> bool:
        self._check_and_reload()
        original_len = len(self._settings.client_api_keys)
        self._settings.client_api_keys = [k for k in self._settings.client_api_keys if k.id != key_id]
        if len(self._settings.client_api_keys) < original_len:
            self._save_to_disk()
            self._trigger_hooks()
            return True
        return False

    def toggle_client_key(self, key_id: str, is_active: bool) -> bool:
        self._check_and_reload()
        for k in self._settings.client_api_keys:
            if k.id == key_id:
                k.is_active = is_active
                self._save_to_disk()
                self._trigger_hooks()
                return True
        return False

    def find_client_key(self, key_value: str) -> Optional[ClientApiKeyConfig]:
        if not key_value:
            return None
        self._check_and_reload()
        stripped = key_value.strip()
        for k in self._settings.client_api_keys:
            if k.key == stripped and k.is_active:
                return k
        return None


settings_manager = SettingsManager()
