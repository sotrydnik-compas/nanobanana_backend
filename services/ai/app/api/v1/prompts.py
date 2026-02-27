from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.database.session import get_async_session
from app.models.prompt_template import PromptTemplate, PromptVariant

router = APIRouter(tags=["prompts"])


@router.get("/prompts/render")
async def render_prompt(
    template_name: str = Query(..., description="Имя шаблона (например: 'product_card')"),
    variant_key: str = Query(..., description="Ключ варианта (например: 'ugc', 'image', 'studio')"),
    title: str = Query(..., description="Заголовок товара"),
    advantage: str = Query(..., description="Преимущество товара"),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Публичный эндпоинт для получения готового промпта на основе шаблона и варианта
    """
    # Получаем шаблон
    template_result = await session.execute(
        select(PromptTemplate).where(
            PromptTemplate.name == template_name,
            PromptTemplate.is_active == True
        )
    )
    template = template_result.scalar_one_or_none()
    if not template:
        raise HTTPException(404, f"Active template with name '{template_name}' not found")

    # Получаем вариант
    variant_result = await session.execute(
        select(PromptVariant).where(
            PromptVariant.template_id == template.id,
            PromptVariant.key == variant_key,
            PromptVariant.is_active == True
        )
    )
    variant = variant_result.scalar_one_or_none()
    if not variant:
        raise HTTPException(404, f"Active variant with key '{variant_key}' not found for template '{template_name}'")

    # Формируем промпт
    try:
        prompt = template.template_text.format(
            variant_label=variant.label,
            title=title.strip(),
            advantage=advantage.strip()
        )
    except KeyError as e:
        raise HTTPException(500, f"Template contains invalid placeholder: {e}")
    except Exception as e:
        raise HTTPException(500, f"Error formatting prompt: {str(e)}")

    return {
        "prompt": prompt,
        "template_name": template.name,
        "variant_key": variant.key,
        "variant_label": variant.label
    }


@router.get("/prompts/templates/{template_name}/variants")
async def get_template_variants(
    template_name: str,
    session: AsyncSession = Depends(get_async_session),
):
    """
    Получить список активных вариантов для шаблона (для фронта)
    """
    # Получаем шаблон
    template_result = await session.execute(
        select(PromptTemplate).where(
            PromptTemplate.name == template_name,
            PromptTemplate.is_active == True
        )
    )
    template = template_result.scalar_one_or_none()
    if not template:
        raise HTTPException(404, f"Active template with name '{template_name}' not found")

    # Получаем активные варианты
    variants_result = await session.execute(
        select(PromptVariant)
        .where(
            PromptVariant.template_id == template.id,
            PromptVariant.is_active == True
        )
        .order_by(PromptVariant.sort_order)
    )
    variants = variants_result.scalars().all()

    return {
        "template_name": template.name,
        "variants": [
            {
                "key": v.key,
                "label": v.label,
                "sort_order": v.sort_order
            }
            for v in variants
        ]
    }


@router.get("/prompts/render/template")
async def get_template_with_variants(
    template_name: str = Query(..., description="Имя шаблона (например: 'product_card')"),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Получить информацию о шаблоне (template_text) и список его активных вариантов.
    Используется фронтом для отображения редактора промптов.
    """
    template_result = await session.execute(
        select(PromptTemplate)
        .options(selectinload(PromptTemplate.variants))
        .where(
            PromptTemplate.name == template_name,
            PromptTemplate.is_active == True
        )
    )
    template = template_result.scalar_one_or_none()
    if not template:
        raise HTTPException(404, f"Active template with name '{template_name}' not found")

    # Фильтруем только активные варианты и сортируем
    active_variants = [
        v for v in template.variants
        if v.is_active
    ]
    active_variants.sort(key=lambda x: x.sort_order)

    return {
        "template_name": template.name,
        "template_text": template.template_text,
        "variants": [
            {
                "key": v.key,
                "label": v.label,
                "sort_order": v.sort_order
            }
            for v in active_variants
        ]
    }
