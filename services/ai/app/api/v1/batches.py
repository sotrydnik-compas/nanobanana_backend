import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_async_session
from app.api.deps import get_current_user, require_admin
from app.core.config import settings
from app.core.logger import logger
from app.core.rate_limit import limit_batch_create
from app.services.uploads import save_upload, ALLOWED_CT
from app.services.titles import make_chat_title
from app.clients.billing_client import BillingClient
from app.clients.gemini_client import SUPPORTED_ASPECT_RATIOS, SUPPORTED_RESOLUTIONS

from app.models.batch import BatchJob, BatchItem
from app.models.chat import Chat
from app.services.arq_queue import enqueue_job_once
from app.services.file_lifecycle import cleanup_batch_storage

router = APIRouter(tags=["batches"])

billing = BillingClient(settings.BILLING_BASE_URL, settings.BILLING_INTERNAL_TOKEN)


async def validate_images(
    image_urls: Optional[List[str]] = None,
    images: Optional[List[UploadFile]] = None,
    *,
    max_items: int,
    kind: str,
) -> List[Dict[str, Any]]:
    """
    Валидация и сохранение изображений.
    Возвращает список элементов пакета:
      [{ "image_url": str, "local_file": Optional[str] }]
    """
    items: List[Dict[str, Any]] = []

    def ensure_can_add() -> None:
        if len(items) >= max_items:
            raise HTTPException(400, f"Maximum {max_items} {kind} allowed")

    # Обрабатываем URL
    if image_urls:
        for url in image_urls:
            u = (url or "").strip()
            if not u:
                continue

            ensure_can_add()
            items.append({"image_url": u, "local_file": None})

    # Обрабатываем загруженные файлы
    if images:
        max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        for img in images:
            if img.content_type not in ALLOWED_CT:
                raise HTTPException(400, f"Unsupported file type: {img.content_type}")

            ensure_can_add()
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
    request: Request,
    prompt: str = Form(..., description="Промт для генерации"),
    resolution: str = Form("1K", description="Разрешение: 1K, 2K, 4K"),
    aspectRatio: str = Form("1:1", description="Соотношение сторон"),
    googleSearch: bool = Form(True, description="Использовать Google Search tool"),
    outputFormat: str = Form("png", description="Формат результата: png|jpg"),
    image_urls: Optional[List[str]] = Form(None, description="URL изображений"),
    images: Optional[List[UploadFile]] = File(None, description="Файлы изображений"),
    reference_urls: Optional[List[str]] = Form(None, description="Общие URL-референсы"),
    reference_images: Optional[List[UploadFile]] = File(None, description="Общие файлы-референсы"),
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

    await limit_batch_create(request, user_id=str(user_id))

    if not settings.GEMINI_API_ENABLED:
        raise HTTPException(503, "Image generation is temporarily unavailable")

    # Валидация промпта
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Prompt is required")
    if len(prompt) > settings.MAX_PROMPT_LEN:
        raise HTTPException(400, f"Prompt too long (max {settings.MAX_PROMPT_LEN})")

    # Валидация resolution
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise HTTPException(400, "Invalid resolution")

    # Валидация aspect ratio
    if aspectRatio not in SUPPORTED_ASPECT_RATIOS:
        raise HTTPException(400, "Invalid aspectRatio")

    output_format = (outputFormat or "png").strip().lower()
    if output_format == "jpeg":
        output_format = "jpg"
    if output_format not in {"png", "jpg"}:
        raise HTTPException(400, "Invalid outputFormat")

    # Сохраняем и валидируем изображения
    try:
        items = await validate_images(image_urls, images, max_items=100, kind="images")
        common_ref_items = await validate_images(
            reference_urls, reference_images, max_items=5, kind="reference images"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating images: {e}")
        raise HTTPException(500, "Error processing images")

    # Проверяем количество
    total_images = len(items)
    if total_images == 0:
        raise HTTPException(400, "At least one image required")

    common_refs = [it["image_url"] for it in common_ref_items if it.get("image_url")]

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
        chat = Chat(title=make_chat_title(prompt), user_id=user_id, status="active")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id

    # Это не нужно, но тут возможно нужна проверка, хватит ли на счете токенов что бы обработать cost.
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
        output_format=output_format,
        google_search=googleSearch,
        common_refs_json=json.dumps(common_refs, ensure_ascii=False),
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
    try:
        await enqueue_job_once("start_batch_processing", batch.id, _job_id=f"batch:{batch.id}")
    except Exception as e:
        logger.error(f"Failed to enqueue batch {batch.id}: {e}")
        await cleanup_batch_storage(session, batch, include_item_uploads=True, include_results=False)
        await session.delete(batch)
        await session.commit()
        raise HTTPException(503, "Queue unavailable")

    # Добавляем первое сообщение в чат о начале пакета
    from app.services.history import add_user_message

    meta = {
        "batch": True,
        "batch_id": batch.id,
        "total_images": total_images,
        "common_refs_count": len(common_refs),
    }
    await add_user_message(
        session=session,
        chat_id=chat_id,
        task_id="",  # нет единого task_id для всего пакета
        prompt=f"[ПАКЕТ] {prompt}",
        meta=meta,
    )
    await session.commit()

    return {
        "batch_id": batch.id,
        "chat_id": chat_id,
        "total_images": total_images,
        "common_refs_count": len(common_refs),
        "status": "pending",
        "google_search": googleSearch,
        "output_format": output_format,
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
        select(BatchItem).where(BatchItem.batch_id == batch_id).order_by(BatchItem.index.desc()).limit(20)
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
            "percent": round(batch.processed_count / batch.total_count * 100, 1)
            if batch.total_count > 0
            else 0,
        },
        "prompt": batch.prompt,
        "resolution": batch.resolution,
        "aspect_ratio": batch.aspect_ratio,
        "google_search": batch.google_search,
        "output_format": batch.output_format,
        "common_refs_count": len(json.loads(batch.common_refs_json or "[]")),
        "created_at": batch.created_at,
        "started_at": batch.started_at,
        "completed_at": batch.completed_at,
        "recent_items": [
            {
                "index": item.index,
                "status": item.status,
                "result_url": item.result_image_url,
                "error": item.error_message,
                "completed_at": item.completed_at,
            }
            for item in recent
        ],
    }


@router.get("/batches")
async def list_batches(
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Фильтр по статусу"),
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
                "common_refs_count": len(json.loads(b.common_refs_json or "[]")),
                "google_search": b.google_search,
                "output_format": b.output_format,
                "created_at": b.created_at,
                "completed_at": b.completed_at,
            }
            for b in batches
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
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
        deleted_files = await cleanup_batch_storage(session, batch, include_item_uploads=True, include_results=False)
        await session.commit()
        return {"status": "cancelled", "message": "Batch cancelled before processing started.", "deleted_files": deleted_files}

    # processing -> просим остановить ПОСЛЕ текущего элемента
    batch.status = "cancelling"
    await session.commit()

    return {
        "status": "cancelling",
        "message": "Batch cancellation requested. Current item will complete before stopping.",
    }


# Админские эндпоинты для мониторинга
@router.get("/admin/batches")
async def admin_list_all_batches(
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
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
                "common_refs_count": len(json.loads(b.common_refs_json or "[]")),
                "google_search": b.google_search,
                "output_format": b.output_format,
                "created_at": b.created_at,
                "started_at": b.started_at,
                "completed_at": b.completed_at,
            }
            for b in batches
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/admin/batches/{batch_id}")
async def admin_get_batch_details(
    batch_id: str,
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
    include_items: bool = Query(True),
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
        "google_search": batch.google_search,
        "output_format": batch.output_format,
        "common_refs": json.loads(batch.common_refs_json or "[]"),
        "common_refs_count": len(json.loads(batch.common_refs_json or "[]")),
        "total_count": batch.total_count,
        "processed_count": batch.processed_count,
        "success_count": batch.success_count,
        "failed_count": batch.failed_count,
        "current_item_index": batch.current_item_index,
        "created_at": batch.created_at,
        "started_at": batch.started_at,
        "completed_at": batch.completed_at,
    }

    if include_items:
        items = await session.execute(
            select(BatchItem).where(BatchItem.batch_id == batch_id).order_by(BatchItem.index)
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
                "completed_at": i.completed_at,
            }
            for i in items.scalars().all()
        ]

    return result
