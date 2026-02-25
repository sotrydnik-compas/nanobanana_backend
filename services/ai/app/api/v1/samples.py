import os

from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_async_session
from app.api.deps import get_current_user

from app.models.sample import Sample
from app.services.uploads import save_sample_images
from app.core.config import settings

router = APIRouter(tags=["samples"])


@router.get("/samples")
async def get_samples(
        page: int = Query(1, ge=1, description="Номер страницы"),
        page_size: int = Query(10, ge=1, le=100, description="Количество элементов на странице"),
        session: AsyncSession = Depends(get_async_session),
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
    query = select(Sample).where(Sample.is_active == True).order_by(Sample.created_at.desc()).offset(offset).limit(page_size)
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


@router.post("/samples")
async def upload_samples(
        files: List[UploadFile] = File(..., description="Изображения для загрузки (JPEG, PNG, WEBP)"),
        session: AsyncSession = Depends(get_async_session),
        user_ctx: dict = Depends(get_current_user),
):
    """
    Загружает одно или несколько изображений в папку media/samples
    и создает записи в БД.
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
