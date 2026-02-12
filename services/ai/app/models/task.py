from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
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
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="auto")

    result_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chat_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
