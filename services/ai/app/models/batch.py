from uuid import uuid4
from datetime import datetime
from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Index, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class BatchJob(Base, TimestampMixin):
    """Пакетная задача"""
    __tablename__ = "batch_jobs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    chat_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        comment="pending|processing|completed|partial|cancelling|cancelled|failed"
    )

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(String(8), nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False)
    output_format: Mapped[str] = mapped_column(String(8), nullable=True, default="png")
    google_search: Mapped[bool] = mapped_column(Boolean, nullable=True, default=True)
    common_refs_json: Mapped[str] = mapped_column(Text, nullable=True, default="[]")
    expected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_chunk_received: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)

    current_item_index: Mapped[int] = mapped_column(Integer, default=0)

    # Для отслеживания прогресса
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index('ix_batch_jobs_user_status', 'user_id', 'status'),
    )


class BatchItem(Base, TimestampMixin):
    """Элемент пакета"""
    __tablename__ = "batch_items"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    batch_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("batch_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)

    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chunk_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        comment="pending|processing|success|failed"
    )
    result_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index('ix_batch_items_batch_status', 'batch_id', 'status'),
        Index('ix_batch_items_task_id', 'task_id'),
        Index('ix_batch_items_batch_index', 'batch_id', 'index'),
    )
