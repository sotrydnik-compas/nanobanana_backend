from fastapi import APIRouter, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_async_session
from app.models.sample import Sample

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
