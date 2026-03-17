from uuid import uuid4
from sqlalchemy import String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class Chat(Base, TimestampMixin):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(64), default="")

    status: Mapped[str] = mapped_column(String(16), default="active")  # active|closed
    deleted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
