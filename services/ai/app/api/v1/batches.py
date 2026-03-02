from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_async_session
from app.api.deps import get_current_user, require_admin
from app.core.config import settings
from app.core.logger import logger
from app.services.uploads import save_upload, ALLOWED_CT
from app.services.titles import make_chat_title
from app.clients.billing_client import BillingClient, BillingNoFunds

from app.models.batch import BatchJob, BatchItem
from app.models.chat import Chat
from app.services.arq_queue import get_arq_pool, start_batch_processing

router = APIRouter(tags=["batches"])

billing = BillingClient(settings.BILLING_BASE_URL, settings.BILLING_INTERNAL_TOKEN)


async def validate_images(
        image_urls: Optional[List[str]] = None,
        images: Optional[List[UploadFile]] = None
) -> List[Dict[str, Any]]:
    """
    Валидация и сохранение изображений.
    Возвращает список элементов пакета:
      [{ "image_url": str, "local_file": Optional[str] }]
    """
    items: List[Dict[str, Any]] = []

    # Обрабатываем URL
    if image_urls:
        for url in image_urls:
            u = (url or "").strip()
            if u:
                items.append({"image_url": u, "local_file": None})

    # Обрабатываем загруженные файлы
    if images:
        max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        for img in images:
            if img.content_type not in ALLOWED_CT:
                raise HTTPException(400, f"Unsupported file type: {img.content_type}")

            try:
                # Сохраняем в подпапку batches для лучшей организации
                path = await save_upload(img, max_bytes=max_bytes, subdir="batches")
                public_url = f"{settings.PUBLIC_BASE_URL}/media/{path}"
                items.append({"image_url": public_url, "local_file": path})
            except Exception as e:
                logger.error(f"Error saving upload: {e}")
                raise HTTPException(500, f"Failed to save file: {img.filename}")

    return items


@router.post("/generate-batch")
async def generate_batch(
        prompt: str = Form(..., description="Промт для генерации"),
        resolution: str = Form("1K", description="Разрешение: 1K, 2K, 4K"),
        aspectRatio: str = Form("1:1", description="Соотношение сторон"),
        image_urls: Optional[List[str]] = Form(None, description="URL изображений"),
        images: Optional[List[UploadFile]] = File(None, description="Файлы изображений"),
        chat_id: Optional[str] = Form(None, description="ID существующего чата"),
        session: AsyncSession = Depends(get_async_session),
        user_ctx: dict = Depends(get_current_user),
):
    """
    Создать пакетную задачу на генерацию.
    Можно отправить до 100 изображений (URL + файлы).
    """
    user_id = user_ctx.get("user_id")
    if not user_id:
        raise HTTPException(401, "Unauthorized")

    # Валидация промпта
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Prompt is required")
    if len(prompt) > settings.MAX_PROMPT_LEN:
        raise HTTPException(400, f"Prompt too long (max {settings.MAX_PROMPT_LEN})")

    # Валидация resolution
    if resolution not in ("1K", "2K", "4K"):
        raise HTTPException(400, "Invalid resolution")

    # Валидация aspect ratio
    allowed_ar = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "auto"}
    if aspectRatio not in allowed_ar:
        raise HTTPException(400, "Invalid aspectRatio")

    # Сохраняем и валидируем изображения
    try:
        items = await validate_images(image_urls, images)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating images: {e}")
        raise HTTPException(500, "Error processing images")

    # Проверяем количество
    total_images = len(items)
    if total_images == 0:
        raise HTTPException(400, "At least one image required")
    if total_images > 100:
        raise HTTPException(400, "Maximum 100 images per batch")

    # Работа с чатом
    if chat_id:
        chat = await session.get(Chat, chat_id)
        if not chat or chat.deleted_at is not None:
            raise HTTPException(404, "Chat not found")

        if chat.user_id is None:
            chat.user_id = user_id
        elif chat.user_id != user_id:
            raise HTTPException(403, "Forbidden")

        if chat.status == "closed":
            raise HTTPException(400, "Chat is closed")
    else:
        chat = Chat(
            title=make_chat_title(prompt),
            user_id=user_id,
            status="active"
        )
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id

    # TODO: Это не нужно, но тут нужна проверка, хватит ли на счете токенов что бы обработать cost.
    # Проверяем средства в биллинге
    # cost = total_images  # стоимость = количество изображений
    # try:
    #     await billing.reserve(user_id=user_id, request_id=request_id, cost=cost)
    # except BillingNoFunds:
    #     raise HTTPException(402, f"Not enough requests (need {cost})")
    # except Exception as e:
    #     logger.error(f"Billing reserve failed: {e}")
    #     raise HTTPException(503, "Billing unavailable")

    # Создаем BatchJob
    batch = BatchJob(
        user_id=user_id,
        chat_id=chat_id,
        prompt=prompt,
        resolution=resolution,
        aspect_ratio=aspectRatio,
        total_count=total_images,
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)

    # Создаем BatchItem для каждого изображения
    for idx, it in enumerate(items):

        item = BatchItem(
            batch_id=batch.id,
            index=idx,
            image_url=it["image_url"],
            local_file=it.get("local_file"),
        )
        session.add(item)

    await session.commit()

    # Запускаем фоновую обработку через ARQ
    pool = await get_arq_pool()
    await pool.enqueue_job("start_batch_processing", batch.id)

    # Добавляем первое сообщение в чат о начале пакета
    from app.services.history import add_user_message
    meta = {
        "batch": True,
        "batch_id": batch.id,
        "total_images": total_images
    }
    await add_user_message(
        session=session,
        chat_id=chat_id,
        task_id="",  # нет единого task_id для всего пакета
        prompt=f"[ПАКЕТ] {prompt}",
        meta=meta
    )
    await session.commit()

    return {
        "batch_id": batch.id,
        "chat_id": chat_id,
        "total_images": total_images,
        "status": "pending"
    }


@router.get("/batches/{batch_id}")
async def get_batch_status(
        batch_id: str,
        session: AsyncSession = Depends(get_async_session),
        user_ctx: dict = Depends(get_current_user),
):
    """Получить статус пакетной задачи"""
    user_id = user_ctx.get("user_id")

    batch = await session.get(BatchJob, batch_id)
    if not batch or batch.user_id != user_id:
        raise HTTPException(404, "Batch not found")

    # Получаем последние 20 элементов для отображения
    items = await session.execute(
        select(BatchItem)
        .where(BatchItem.batch_id == batch_id)
        .order_by(BatchItem.index.desc())
        .limit(20)
    )
    recent = list(items.scalars().all())
    recent.reverse()

    return {
        "batch_id": batch.id,
        "chat_id": batch.chat_id,
        "status": batch.status,
        "progress": {
            "total": batch.total_count,
            "processed": batch.processed_count,
            "success": batch.success_count,
            "failed": batch.failed_count,
            "current_index": batch.current_item_index,
            "percent": round(batch.processed_count / batch.total_count * 100, 1) if batch.total_count > 0 else 0
        },
        "prompt": batch.prompt,
        "resolution": batch.resolution,
        "aspect_ratio": batch.aspect_ratio,
        "created_at": batch.created_at,
        "started_at": batch.started_at,
        "completed_at": batch.completed_at,
        "recent_items": [
            {
                "index": item.index,
                "status": item.status,
                "result_url": item.result_image_url,
                "error": item.error_message,
                "completed_at": item.completed_at
            }
            for item in recent
        ]
    }


@router.get("/batches")
async def list_batches(
        session: AsyncSession = Depends(get_async_session),
        user_ctx: dict = Depends(get_current_user),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        status: Optional[str] = Query(None, description="Фильтр по статусу")
):
    """Список пакетных задач пользователя"""
    user_id = user_ctx.get("user_id")

    query = select(BatchJob).where(BatchJob.user_id == user_id)

    if status:
        query = query.where(BatchJob.status == status)

    query = query.order_by(BatchJob.created_at.desc()).offset(offset).limit(limit)

    result = await session.execute(query)
    batches = result.scalars().all()

    # Общее количество
    count_query = select(func.count()).where(BatchJob.user_id == user_id)
    if status:
        count_query = count_query.where(BatchJob.status == status)
    total = await session.scalar(count_query)

    return {
        "items": [
            {
                "batch_id": b.id,
                "chat_id": b.chat_id,
                "status": b.status,
                "total": b.total_count,
                "processed": b.processed_count,
                "success": b.success_count,
                "failed": b.failed_count,
                "created_at": b.created_at,
                "completed_at": b.completed_at
            }
            for b in batches
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(
        batch_id: str,
        session: AsyncSession = Depends(get_async_session),
        user_ctx: dict = Depends(get_current_user),
):
    """Отменить пакетную обработку"""
    user_id = user_ctx.get("user_id")

    batch = await session.get(BatchJob, batch_id)
    if not batch or batch.user_id != user_id:
        raise HTTPException(404, "Batch not found")

    if batch.status not in ("pending", "processing"):
        raise HTTPException(400, f"Cannot cancel batch with status {batch.status}")

    # Если пакет ещё не стартовал — отменяем сразу
    if batch.status == "pending":
        batch.status = "cancelled"
        batch.completed_at = datetime.now(timezone.utc)
        await session.commit()
        return {
            "status": "cancelled",
            "message": "Batch cancelled before processing started."
        }

    # processing -> просим остановить ПОСЛЕ текущего элемента
    batch.status = "cancelling"
    await session.commit()

    return {
        "status": "cancelling",
        "message": "Batch cancellation requested. Current item will complete before stopping."
    }


# Админские эндпоинты для мониторинга
@router.get("/admin/batches")
async def admin_list_all_batches(
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        status: Optional[str] = Query(None)
):
    """Админ: список всех пакетных задач"""
    query = select(BatchJob)

    if status:
        query = query.where(BatchJob.status == status)

    query = query.order_by(BatchJob.created_at.desc()).offset(offset).limit(limit)

    result = await session.execute(query)
    batches = result.scalars().all()

    total = await session.scalar(select(func.count()).select_from(BatchJob))

    return {
        "items": [
            {
                "batch_id": b.id,
                "user_id": b.user_id,
                "chat_id": b.chat_id,
                "status": b.status,
                "total": b.total_count,
                "processed": b.processed_count,
                "success": b.success_count,
                "failed": b.failed_count,
                "created_at": b.created_at,
                "started_at": b.started_at,
                "completed_at": b.completed_at
            }
            for b in batches
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/admin/batches/{batch_id}")
async def admin_get_batch_details(
        batch_id: str,
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
        include_items: bool = Query(True)
):
    """Админ: детальная информация о пакете"""
    batch = await session.get(BatchJob, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")

    result = {
        "batch_id": batch.id,
        "user_id": batch.user_id,
        "chat_id": batch.chat_id,
        "status": batch.status,
        "prompt": batch.prompt,
        "resolution": batch.resolution,
        "aspect_ratio": batch.aspect_ratio,
        "total_count": batch.total_count,
        "processed_count": batch.processed_count,
        "success_count": batch.success_count,
        "failed_count": batch.failed_count,
        "current_item_index": batch.current_item_index,
        "created_at": batch.created_at,
        "started_at": batch.started_at,
        "completed_at": batch.completed_at
    }

    if include_items:
        items = await session.execute(
            select(BatchItem)
            .where(BatchItem.batch_id == batch_id)
            .order_by(BatchItem.index)
        )
        result["items"] = [
            {
                "id": i.id,
                "index": i.index,
                "status": i.status,
                "image_url": i.image_url,
                "local_file": i.local_file,
                "task_id": i.task_id,
                "result_image_url": i.result_image_url,
                "error_message": i.error_message,
                "started_at": i.started_at,
                "completed_at": i.completed_at
            }
            for i in items.scalars().all()
        ]

    return result
