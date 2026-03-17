from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class UserBalance(Base, TimestampMixin):
    __tablename__ = "user_balances"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requests_left: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
