import os

from typing import Optional, List
from fastapi import APIRouter, Form, HTTPException, Depends, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from app.database.session import get_async_session
from app.api.deps import require_admin
from app.core.config import settings
from app.core.logger import logger

from app.models.prompt_template import PromptTemplate, PromptVariant
from app.models.sample import Sample
from app.services.uploads import save_sample_images

router = APIRouter(tags=["admin"])


@router.get("/admin/prompts/templates")
async def admin_list_templates(
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        is_active: Optional[bool] = Query(None),
):
    """
    Получить список всех шаблонов промптов
    """
    query = select(PromptTemplate).options(selectinload(PromptTemplate.variants))

    # Фильтры
    if is_active is not None:
        query = query.where(PromptTemplate.is_active == is_active)

    # Пагинация и сортировка
    query = query.order_by(PromptTemplate.created_at.desc()).offset(offset).limit(limit)

    # Получение результатов
    result = await session.execute(query)
    templates = result.scalars().all()

    # Общее количество
    count_query = select(func.count()).select_from(PromptTemplate)
    if is_active is not None:
        count_query = count_query.where(PromptTemplate.is_active == is_active)
    total = await session.scalar(count_query)

    return {
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "template_text": t.template_text,
                "is_active": t.is_active,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
                "variants_count": len(t.variants)
            }
            for t in templates
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.post("/admin/prompts/templates")
async def admin_create_template(
        name: str = Form(..., description="Уникальное имя шаблона"),
        template_text: str = Form(..., description="Текст шаблона с плейсхолдерами в {фигурных_скобках}"),
        is_active: bool = Form(True),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Создать новый шаблон промпта
    """
    # Проверка уникальности имени
    existing = await session.execute(
        select(PromptTemplate).where(PromptTemplate.name == name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Template with name '{name}' already exists")

    template = PromptTemplate(
        name=name,
        template_text=template_text,
        is_active=is_active
    )
    session.add(template)

    try:
        await session.commit()
        await session.refresh(template)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(400, f"Template with name '{name}' already exists")

    return {
        "id": template.id,
        "name": template.name,
        "template_text": template.template_text,
        "is_active": template.is_active,
        "created_at": template.created_at,
        "updated_at": template.updated_at
    }


@router.get("/admin/prompts/templates/{template_id}")
async def admin_get_template(
        template_id: str,
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Получить шаблон по ID с вариантами
    """
    template = await session.get(PromptTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template not found")

    # Получаем варианты
    variants_result = await session.execute(
        select(PromptVariant)
        .where(PromptVariant.template_id == template_id)
        .order_by(PromptVariant.sort_order)
    )
    variants = variants_result.scalars().all()

    return {
        "id": template.id,
        "name": template.name,
        "template_text": template.template_text,
        "is_active": template.is_active,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
        "variants": [
            {
                "id": v.id,
                "key": v.key,
                "label": v.label,
                "sort_order": v.sort_order,
                "is_active": v.is_active,
                "created_at": v.created_at,
                "updated_at": v.updated_at
            }
            for v in variants
        ]
    }


@router.patch("/admin/prompts/templates/{template_id}")
async def admin_update_template(
        template_id: str,
        name: Optional[str] = Form(None),
        template_text: Optional[str] = Form(None),
        is_active: Optional[bool] = Form(None),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Обновить шаблон промпта
    """
    template = await session.get(PromptTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template not found")

    if name is not None:
        # Проверка уникальности нового имени
        if name != template.name:
            existing = await session.execute(
                select(PromptTemplate).where(
                    PromptTemplate.name == name,
                    PromptTemplate.id != template_id
                )
            )
            if existing.scalar_one_or_none():
                raise HTTPException(400, f"Template with name '{name}' already exists")
        template.name = name

    if template_text is not None:
        template.template_text = template_text

    if is_active is not None:
        template.is_active = is_active

    try:
        await session.commit()
        await session.refresh(template)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(400, "Template name must be unique")

    return {
        "id": template.id,
        "name": template.name,
        "template_text": template.template_text,
        "is_active": template.is_active,
        "created_at": template.created_at,
        "updated_at": template.updated_at
    }


@router.delete("/admin/prompts/templates/{template_id}")
async def admin_delete_template(
        template_id: str,
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Удалить шаблон промпта (каскадно удалит и все варианты)
    """
    template = await session.get(PromptTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template not found")

    await session.delete(template)
    await session.commit()

    return {"status": "ok", "message": f"Template {template_id} deleted"}


@router.get("/admin/prompts/templates/{template_id}/variants")
async def admin_list_variants(
        template_id: str,
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
        is_active: Optional[bool] = Query(None),
):
    """
    Получить список вариантов для шаблона
    """
    # Проверка существования шаблона
    template = await session.get(PromptTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template not found")

    query = select(PromptVariant).where(PromptVariant.template_id == template_id)

    if is_active is not None:
        query = query.where(PromptVariant.is_active == is_active)

    query = query.order_by(PromptVariant.sort_order)

    result = await session.execute(query)
    variants = result.scalars().all()

    return {
        "items": [
            {
                "id": v.id,
                "key": v.key,
                "label": v.label,
                "sort_order": v.sort_order,
                "is_active": v.is_active,
                "created_at": v.created_at,
                "updated_at": v.updated_at
            }
            for v in variants
        ]
    }


@router.post("/admin/prompts/templates/{template_id}/variants")
async def admin_create_variant(
        template_id: str,
        key: str = Form(..., description="Уникальный ключ варианта (например: 'ugc', 'image', 'studio')"),
        label: str = Form(..., description="Текст для подстановки"),
        sort_order: int = Form(0),
        is_active: bool = Form(True),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Создать новый вариант для шаблона
    """
    # Проверка существования шаблона
    template = await session.get(PromptTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template not found")

    variant = PromptVariant(
        template_id=template_id,
        key=key,
        label=label,
        sort_order=sort_order,
        is_active=is_active
    )
    session.add(variant)

    try:
        await session.commit()
        await session.refresh(variant)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(400, f"Variant with key '{key}' already exists for this template")

    return {
        "id": variant.id,
        "key": variant.key,
        "label": variant.label,
        "sort_order": variant.sort_order,
        "is_active": variant.is_active,
        "created_at": variant.created_at,
        "updated_at": variant.updated_at
    }


@router.patch("/admin/prompts/variants/{variant_id}")
async def admin_update_variant(
        variant_id: str,
        key: Optional[str] = Form(None),
        label: Optional[str] = Form(None),
        sort_order: Optional[int] = Form(None),
        is_active: Optional[bool] = Form(None),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Обновить вариант
    """
    variant = await session.get(PromptVariant, variant_id)
    if not variant:
        raise HTTPException(404, "Variant not found")

    if key is not None:
        # Проверка уникальности ключа в рамках шаблона
        if key != variant.key:
            existing = await session.execute(
                select(PromptVariant).where(
                    PromptVariant.template_id == variant.template_id,
                    PromptVariant.key == key,
                    PromptVariant.id != variant_id
                )
            )
            if existing.scalar_one_or_none():
                raise HTTPException(400, f"Variant with key '{key}' already exists for this template")
        variant.key = key

    if label is not None:
        variant.label = label

    if sort_order is not None:
        variant.sort_order = sort_order

    if is_active is not None:
        variant.is_active = is_active

    try:
        await session.commit()
        await session.refresh(variant)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(400, "Variant key must be unique within template")

    return {
        "id": variant.id,
        "key": variant.key,
        "label": variant.label,
        "sort_order": variant.sort_order,
        "is_active": variant.is_active,
        "created_at": variant.created_at,
        "updated_at": variant.updated_at
    }


@router.delete("/admin/prompts/variants/{variant_id}")
async def admin_delete_variant(
        variant_id: str,
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Удалить вариант
    """
    variant = await session.get(PromptVariant, variant_id)
    if not variant:
        raise HTTPException(404, "Variant not found")

    await session.delete(variant)
    await session.commit()

    return {"status": "ok", "message": f"Variant {variant_id} deleted"}


@router.get("/admin/samples")
async def get_samples(
        page: int = Query(1, ge=1, description="Номер страницы"),
        page_size: int = Query(10, ge=1, le=100, description="Количество элементов на странице"),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Возвращает список изображений с пагинацией.
    По умолчанию 10 изображений на странице.
    """
    # Вычисляем offset
    offset = (page - 1) * page_size

    # Получаем общее количество записей
    total_count_query = select(func.count()).select_from(Sample)
    total_count_result = await session.execute(total_count_query)
    total_count = total_count_result.scalar()

    # Получаем записи с пагинацией
    query = select(Sample).order_by(Sample.created_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    samples = result.scalars().all()

    # Формируем полные URL для изображений
    base_url = f"/media"
    samples_data = []
    for sample in samples:
        if sample.path:
            # Формируем полный путь к изображению
            image_url = f"{base_url}/{sample.path}"
            samples_data.append({
                "id": sample.id,
                "path": sample.path,
                "url": image_url,
                "is_active": sample.is_active,
                "created_at": sample.created_at.isoformat() if sample.created_at else None,
                "updated_at": sample.updated_at.isoformat() if sample.updated_at else None
            })

    return {
        "items": samples_data,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_count + page_size - 1) // page_size
    }


@router.post("/admin/samples")
async def admin_upload_samples(
        files: List[UploadFile] = File(..., description="Изображения для загрузки (JPEG, PNG, WEBP)"),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Загружает одно или несколько изображений в папку media/samples
    и создает записи в БД. Только для администраторов.
    """
    if not files:
        raise HTTPException(400, detail="No files uploaded")

    if len(files) > 10:
        raise HTTPException(400, detail="Maximum 10 files per request")

    # Сохраняем файлы
    try:
        saved_paths = await save_sample_images(files, max_bytes=10 * 1024 * 1024)  # 10 MB max per file
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error saving files: {e}")
        raise HTTPException(500, detail=f"Error saving files: {str(e)}")

    # Создаем записи в БД
    created_samples = []
    try:
        for path in saved_paths:
            sample = Sample(path=path, is_active=False)
            session.add(sample)
            created_samples.append(sample)

        await session.commit()

        # Обновляем объекты для получения сгенерированных полей
        for sample in created_samples:
            await session.refresh(sample)

    except Exception as e:
        # В случае ошибки БД удаляем сохраненные файлы
        for path in saved_paths:
            full_path = os.path.join(settings.MEDIA_DIR, path)
            try:
                if os.path.exists(full_path):
                    os.remove(full_path)
            except Exception:
                pass
        await session.rollback()
        logger.error(f"Database error: {e}")
        raise HTTPException(500, detail=f"Database error: {str(e)}")

    # Формируем ответ
    base_url = f"/media"
    result = []
    for sample in created_samples:
        result.append({
            "id": sample.id,
            "path": sample.path,
            "url": f"{base_url}/{sample.path}",
            "created_at": sample.created_at.isoformat() if sample.created_at else None
        })

    return {
        "message": f"Successfully uploaded {len(result)} file(s)",
        "samples": result
    }


@router.patch("/admin/samples/{sample_id}")
async def admin_update_sample(
        sample_id: str,
        is_active: bool = Form(...),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Активировать/деактивировать семпл
    """
    sample = await session.get(Sample, sample_id)
    if not sample:
        raise HTTPException(404, "Sample not found")

    sample.is_active = is_active
    await session.commit()

    return {"status": "ok", "message": f"Sample {sample_id} updated"}


@router.delete("/admin/samples/{sample_id}")
async def admin_delete_sample(
        sample_id: str,
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Удалить семпл (и файл, и запись в БД)
    """
    sample = await session.get(Sample, sample_id)
    if not sample:
        raise HTTPException(404, "Sample not found")

    # Удаляем файл
    if sample.path:
        full_path = os.path.join(settings.MEDIA_DIR, sample.path)
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except Exception as e:
            logger.error(f"Error deleting file {full_path}: {e}")

    # Удаляем запись из БД
    await session.delete(sample)
    await session.commit()

    return {"status": "ok", "message": f"Sample {sample_id} deleted"}
