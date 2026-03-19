from sqlalchemy import String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from app.database.base import Base, TimestampMixin


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")

    prompt: Mapped[str] = mapped_column(Text, default="")
    image_urls: Mapped[str] = mapped_column(Text, default="[]")
    local_files: Mapped[str] = mapped_column(Text, default="[]")

    resolution: Mapped[str] = mapped_column(String(8), default="1K")
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="1:1")
    output_format: Mapped[str] = mapped_column(String(8), default="png", nullable=True)
    google_search: Mapped[bool] = mapped_column(Boolean, default=True, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="legacy_nanobanana", nullable=True)

    result_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    chat_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)

    billing_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    billing_state: Mapped[str] = mapped_column(String(16), default="none")
    # значения: none | reserved | confirm_pending | confirmed | refunded

    batch_item_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("batch_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
