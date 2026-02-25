from sqlalchemy import String, Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from uuid import uuid4

from app.database.base import Base, TimestampMixin


class PromptTemplate(Base, TimestampMixin):
    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(
        String(100), unique=True, index=True
    )  # Например: "product_card", "description", etc
    template_text: Mapped[str] = mapped_column(
        String(1000), nullable=False
    )  # "Создай мне {variant_label} для карточки товара. Заголовок: {title}. Преимущество: {advantage}"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Связь с вариантами
    variants: Mapped[list["PromptVariant"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan"
    )


class PromptVariant(Base, TimestampMixin):
    __tablename__ = "prompt_variants"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    template_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("prompt_templates.id", ondelete="CASCADE"),
        index=True
    )
    key: Mapped[str] = mapped_column(String(50), nullable=False)  # Например: "ugc", "image", "studio"
    label: Mapped[str] = mapped_column(String(200), nullable=False)  # Текст для подстановки
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Обратная связь
    template: Mapped["PromptTemplate"] = relationship(back_populates="variants")

    # Уникальность ключа в рамках одного шаблона
    __table_args__ = (
        UniqueConstraint('template_id', 'key', name='uix_template_key'),
    )
