from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Integer,
    BigInteger,
    Float,
    Boolean,
    Text,
    DateTime,
    Date,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utc_now() -> datetime:
    """Return naive UTC datetime for standard TIMESTAMP WITHOUT TIME ZONE column compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ApiUsageLog(Base):
    __tablename__ = "api_usage_logs"

    id = Column(Integer().with_variant(BigInteger, "postgresql"), primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=utc_now, nullable=False, index=True)
    client_key_id = Column(String(64), nullable=False, index=True)
    client_key_name = Column(String(255), nullable=False)
    endpoint = Column(String(128), nullable=False, index=True)
    model_worker_id = Column(String(64), nullable=True, index=True)
    char_count = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Float, default=0.0, nullable=False)
    status_code = Column(Integer, default=200, nullable=False)

    __table_args__ = (
        Index("ix_api_usage_timestamp_key", "timestamp", "client_key_id"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=True)
    auth_provider = Column(String(32), default="local", nullable=False)
    full_name = Column(String(255), nullable=False)
    avatar_url = Column(String(512), nullable=True)
    max_cloned_slots = Column(Integer, default=3, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    voices = relationship("Voice", back_populates="owner", cascade="all, delete-orphan", lazy="selectin")
    daily_usages = relationship("DailyUsage", back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class Voice(Base):
    __tablename__ = "voices"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    tag = Column(String(64), default="Cloud AI")
    spec = Column(String(64), default="48kHz • HQ")
    lang = Column(String(16), default="vi")
    accent = Column(String(64), default="Chuẩn Nam Bắc")
    gender = Column(String(16), default="male")
    description = Column(Text, default="")
    is_default = Column(Boolean, default=False)
    speed = Column(Float, default=1.0)
    pitch = Column(Integer, default=0)
    emotion = Column(Integer, default=85)
    denoise = Column(Boolean, default=True)
    smart_pause = Column(Boolean, default=True)
    category = Column(String(64), default="Thuyết minh")
    accent_color_hex = Column(BigInteger, default=0xFF4648D4)
    icon_name = Column(String(64), default="graphic_eq")
    sample_text = Column(Text, nullable=False)
    is_builtin = Column(Boolean, default=False, index=True)
    ref_audio_path = Column(String(512), nullable=True)
    status = Column(String(32), default="ready", index=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    owner = relationship("User", back_populates="voices", lazy="selectin")


class DailyUsage(Base):
    __tablename__ = "daily_usages"

    id = Column(Integer().with_variant(BigInteger, "postgresql"), primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    usage_date = Column(Date, nullable=False, index=True)
    tts_characters_used = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    user = relationship("User", back_populates="daily_usages")
