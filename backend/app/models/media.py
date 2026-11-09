"""媒体素材模型：图表、图解、配音、字幕、视频片段。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import LicenseStatus, MediaAssetType
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.script import Script


class MediaAsset(Base, TimestampMixin):
    """媒体素材登记表。

    第一阶段只登记元数据（路径、尺寸、时长、校验和），不实现生成；
    generator 字段如实记录来源工具（manual / matplotlib / ffmpeg / tts 等）。
    """

    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0", name="size_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int | None] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    asset_type: Mapped[MediaAssetType] = enum_column(
        MediaAssetType, name="asset_type", default=MediaAssetType.OTHER, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(64))
    format: Mapped[str | None] = mapped_column(String(16))

    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(64), index=True)

    generator: Mapped[str | None] = mapped_column(String(64))
    license_status: Mapped[LicenseStatus] = enum_column(
        LicenseStatus, name="license_status", default=LicenseStatus.UNCLEAR
    )
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)

    script: Mapped[Script | None] = relationship(back_populates="media_assets")
