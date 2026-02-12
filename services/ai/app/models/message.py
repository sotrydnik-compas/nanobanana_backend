from uuid import uuid4
from sqlalchemy import Text, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    chat_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("chats.id", ondelete="CASCADE"), index=True)

    role: Mapped[str] = mapped_column(String(16), default="user")  # user/assistant/system
    content: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")

    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # link to tasks.task_id (optional)
