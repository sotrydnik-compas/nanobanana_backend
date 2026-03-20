import uuid

from sqlalchemy import String, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class SystemPlanGrant(Base, TimestampMixin):
    __tablename__ = "system_plan_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "grant_type", name="uix_system_plan_grants_user_grant_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False, index=True)
    grant_type: Mapped[str] = mapped_column(String(32), nullable=False, default="signup_bonus")
