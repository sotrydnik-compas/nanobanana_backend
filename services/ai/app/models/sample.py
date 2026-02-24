from uuid import uuid4
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class Sample(Base, TimestampMixin):
    __tablename__ = "samples"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    path: Mapped[str] = mapped_column(String(255), nullable=True)
