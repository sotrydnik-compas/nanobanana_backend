import json
from uuid import uuid4
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
from app.services.file_lifecycle import cleanup_batch_storage, cleanup_saved_upload_paths
from app.services.history import add_user_message

router = APIRouter(tags=["batches"])

billing = BillingClient(settings.BILLING_BASE_URL, settings.BILLING_INTERNAL_TOKEN)


def _translate_upload_error(detail: str) -> str:
    if detail.startswith("Unsupported content-type:"):
        return "Неподдерживаемый тип файла."
    if detail == "File too large":
        return f"Файл слишком большой. Максимум {settings.MAX_UPLOAD_MB} МБ."
    return detail


async def _serialize_batch_progress(session: AsyncSession, batch: BatchJob) -> tuple[int, bool]:
    received_count = await _count_batch_items(session, batch.id)
    expected_count = batch.expected_count or batch.total_count
    upload_complete = bool(batch.last_chunk_received) and received_count == expected_count
    return received_count, upload_complete


async def validate_images(
    image_urls: Optional[List[str]] = None,
    images: Optional[List[UploadFile]] = None,
    *,
    max_items: int,
    kind: str,
    upload_subdir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Валидация и сохранение изображений.
    Возвращает список элементов пакета:
      [{ "image_url": str, "local_file": Optional[str] }]
    """
    items: List[Dict[str, Any]] = []

    def ensure_can_add() -> None:
        if len(items) >= max_items:
            raise HTTPException(400, f"Превышено допустимое количество изображений. Максимум: {max_items}.")

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
        saved_paths: List[str] = []
        for img in images:
            if img.content_type not in ALLOWED_CT:
                cleanup_saved_upload_paths(saved_paths)
                raise HTTPException(400, f"Неподдерживаемый тип файла: {img.content_type}")

            ensure_can_add()
            try:
                path = await save_upload(img, max_bytes=max_bytes, subdir=upload_subdir or "batches")
                saved_paths.append(path)
                public_url = f"{settings.PUBLIC_BASE_URL}/media/{path}"
                items.append({"image_url": public_url, "local_file": path})
            except HTTPException as e:
                cleanup_saved_upload_paths(saved_paths)
                raise HTTPException(status_code=e.status_code, detail=_translate_upload_error(str(e.detail)))
            except Exception as e:
                cleanup_saved_upload_paths(saved_paths)
                logger.error(f"Error saving upload: {e}")
                raise HTTPException(500, f"Не удалось сохранить файл: {img.filename}")

    return items


async def _get_or_create_batch_chat(
    session: AsyncSession,
    *,
    user_id: str,
    prompt: str,
    chat_id: Optional[str],
) -> tuple[Chat, str]:
    if chat_id:
        chat = await session.get(Chat, chat_id)
        if not chat or chat.deleted_at is not None:
            raise HTTPException(404, "Чат не найден")

        if chat.user_id is None:
            chat.user_id = user_id
        elif chat.user_id != user_id:
            raise HTTPException(403, "Доступ запрещен")

        if chat.status == "closed":
            raise HTTPException(400, "Чат закрыт")

        return chat, chat_id

    chat = Chat(title=make_chat_title(prompt), user_id=user_id, status="active")
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return chat, chat.id


async def _get_owned_pending_batch(session: AsyncSession, *, batch_id: str, user_id: str) -> BatchJob:
    batch = await session.get(BatchJob, batch_id)
    if not batch or batch.user_id != user_id:
        raise HTTPException(404, "Пакет не найден")
    if batch.status != "pending":
        raise HTTPException(400, f"Нельзя изменить пакет в статусе {batch.status}")
    return batch


async def _count_batch_items(session: AsyncSession, batch_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(BatchItem).where(BatchItem.batch_id == batch_id)
        )
        or 0
    )


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
        raise HTTPException(401, "Не авторизован")

    await limit_batch_create(request, user_id=str(user_id))

    if not settings.GEMINI_API_ENABLED:
        raise HTTPException(503, "Генерация изображений временно недоступна")

    # Валидация промпта
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Промпт обязателен")
    if len(prompt) > settings.MAX_PROMPT_LEN:
        raise HTTPException(400, f"Промпт слишком длинный. Максимум {settings.MAX_PROMPT_LEN} символов.")

    # Валидация resolution
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise HTTPException(400, "Некорректное разрешение")

    # Валидация aspect ratio
    if aspectRatio not in SUPPORTED_ASPECT_RATIOS:
        raise HTTPException(400, "Некорректное соотношение сторон")

    output_format = (outputFormat or "png").strip().lower()
    if output_format == "jpeg":
        output_format = "jpg"
    if output_format not in {"png", "jpg"}:
        raise HTTPException(400, "Некорректный формат результата")

    _, chat_id = await _get_or_create_batch_chat(
        session,
        user_id=user_id,
        prompt=prompt,
        chat_id=chat_id,
    )

    batch_id = str(uuid4())
    items: List[Dict[str, Any]] = []
    common_ref_items: List[Dict[str, Any]] = []

    # Сохраняем и валидируем изображения
    try:
        items = await validate_images(
            image_urls,
            images,
            max_items=100,
            kind="images",
            upload_subdir=f"{batch_id}/refs/items",
        )
        common_ref_items = await validate_images(
            reference_urls,
            reference_images,
            max_items=5,
            kind="reference images",
            upload_subdir=f"{batch_id}/refs/common",
        )
    except HTTPException:
        cleanup_saved_upload_paths([
            *(it.get("local_file") for it in items if it.get("local_file")),
            *(it.get("local_file") for it in common_ref_items if it.get("local_file")),
        ])
        raise
    except Exception as e:
        cleanup_saved_upload_paths([
            *(it.get("local_file") for it in items if it.get("local_file")),
            *(it.get("local_file") for it in common_ref_items if it.get("local_file")),
        ])
        logger.error(f"Error validating images: {e}")
        raise HTTPException(500, "Не удалось обработать изображения")

    # Проверяем количество
    total_images = len(items)
    if total_images == 0:
        cleanup_saved_upload_paths([
            *(it.get("local_file") for it in common_ref_items if it.get("local_file")),
        ])
        raise HTTPException(400, "Нужно передать хотя бы одно изображение")

    common_refs = [it["image_url"] for it in common_ref_items if it.get("image_url")]

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
        id=batch_id,
        user_id=user_id,
        chat_id=chat_id,
        prompt=prompt,
        resolution=resolution,
        aspect_ratio=aspectRatio,
        output_format=output_format,
        google_search=googleSearch,
        common_refs_json=json.dumps(common_refs, ensure_ascii=False),
        expected_count=total_images,
        last_chunk_received=True,
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
        raise HTTPException(503, "Очередь временно недоступна")

    # Добавляем первое сообщение в чат о начале пакета
    meta = {
        "batch": True,
        "batch_id": batch.id,
        "total_images": total_images,
        "common_refs_count": len(common_refs),
        "imageUrls": common_refs,
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


@router.post("/generate-batch/init")
async def generate_batch_init(
    request: Request,
    prompt: str = Form(..., description="Промт для генерации"),
    expected_count: int = Form(..., ge=1, le=100, description="Ожидаемое количество изображений"),
    resolution: str = Form("1K", description="Разрешение: 1K, 2K, 4K"),
    aspectRatio: str = Form("1:1", description="Соотношение сторон"),
    googleSearch: bool = Form(True, description="Использовать Google Search tool"),
    outputFormat: str = Form("png", description="Формат результата: png|jpg"),
    reference_urls: Optional[List[str]] = Form(None, description="Общие URL-референсы"),
    reference_images: Optional[List[UploadFile]] = File(None, description="Общие файлы-референсы"),
    chat_id: Optional[str] = Form(None, description="ID существующего чата"),
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
):
    user_id = user_ctx.get("user_id")
    if not user_id:
        raise HTTPException(401, "Не авторизован")

    await limit_batch_create(request, user_id=str(user_id))

    if not settings.GEMINI_API_ENABLED:
        raise HTTPException(503, "Генерация изображений временно недоступна")

    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Промпт обязателен")
    if len(prompt) > settings.MAX_PROMPT_LEN:
        raise HTTPException(400, f"Промпт слишком длинный. Максимум {settings.MAX_PROMPT_LEN} символов.")

    if resolution not in SUPPORTED_RESOLUTIONS:
        raise HTTPException(400, "Некорректное разрешение")
    if aspectRatio not in SUPPORTED_ASPECT_RATIOS:
        raise HTTPException(400, "Некорректное соотношение сторон")

    output_format = (outputFormat or "png").strip().lower()
    if output_format == "jpeg":
        output_format = "jpg"
    if output_format not in {"png", "jpg"}:
        raise HTTPException(400, "Некорректный формат результата")

    _, chat_id = await _get_or_create_batch_chat(
        session,
        user_id=user_id,
        prompt=prompt,
        chat_id=chat_id,
    )

    batch_id = str(uuid4())
    common_ref_items: List[Dict[str, Any]] = []

    try:
        common_ref_items = await validate_images(
            reference_urls,
            reference_images,
            max_items=5,
            kind="reference images",
            upload_subdir=f"{batch_id}/refs/common",
        )
    except HTTPException:
        cleanup_saved_upload_paths([
            *(it.get("local_file") for it in common_ref_items if it.get("local_file")),
        ])
        raise
    except Exception as e:
        cleanup_saved_upload_paths([
            *(it.get("local_file") for it in common_ref_items if it.get("local_file")),
        ])
        logger.error(f"Error validating batch common refs: {e}")
        raise HTTPException(500, "Не удалось обработать референсы")

    common_refs = [it["image_url"] for it in common_ref_items if it.get("image_url")]

    batch = BatchJob(
        id=batch_id,
        user_id=user_id,
        chat_id=chat_id,
        prompt=prompt,
        resolution=resolution,
        aspect_ratio=aspectRatio,
        output_format=output_format,
        google_search=googleSearch,
        common_refs_json=json.dumps(common_refs, ensure_ascii=False),
        expected_count=expected_count,
        last_chunk_received=False,
        total_count=0,
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)

    meta = {
        "batch": True,
        "batch_id": batch.id,
        "expected_count": expected_count,
        "common_refs_count": len(common_refs),
        "imageUrls": common_refs,
        "upload_state": "pending",
    }
    await add_user_message(
        session=session,
        chat_id=chat_id,
        task_id="",
        prompt=f"[ПАКЕТ] {prompt}",
        meta=meta,
    )
    await session.commit()

    return {
        "batch_id": batch.id,
        "chat_id": chat_id,
        "expected_count": expected_count,
        "common_refs_count": len(common_refs),
        "status": "pending",
        "upload_state": "pending",
        "google_search": googleSearch,
        "output_format": output_format,
    }


@router.post("/batches/{batch_id}/chunks")
async def upload_batch_chunk(
    batch_id: str,
    chunkHash: str = Form(..., description="Хеш чанка для дедупликации"),
    isLastChunk: bool = Form(False, description="Флаг последнего чанка"),
    image_urls: Optional[List[str]] = Form(None, description="URL изображений"),
    images: Optional[List[UploadFile]] = File(None, description="Файлы изображений"),
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
):
    user_id = user_ctx.get("user_id")
    if not user_id:
        raise HTTPException(401, "Не авторизован")

    chunk_hash = (chunkHash or "").strip()
    if not chunk_hash:
        raise HTTPException(400, "Параметр chunkHash обязателен")

    batch = await _get_owned_pending_batch(session, batch_id=batch_id, user_id=user_id)

    existing_chunk = await session.execute(
        select(BatchItem.id).where(
            BatchItem.batch_id == batch_id,
            BatchItem.chunk_hash == chunk_hash,
        ).limit(1)
    )
    if existing_chunk.first():
        received_count = await _count_batch_items(session, batch_id)
        return {
            "batch_id": batch_id,
            "status": "duplicate",
            "chunk_hash": chunk_hash,
            "received_count": received_count,
            "expected_count": batch.expected_count,
            "last_chunk_received": bool(batch.last_chunk_received),
        }

    current_count = await _count_batch_items(session, batch_id)
    remaining = max((batch.expected_count or 0) - current_count, 0)
    if remaining <= 0:
        raise HTTPException(409, "Пакет уже получил все ожидаемые изображения")

    items: List[Dict[str, Any]] = []
    try:
        items = await validate_images(
            image_urls,
            images,
            max_items=remaining,
            kind="images",
            upload_subdir=f"{batch_id}/refs/items",
        )
    except HTTPException:
        cleanup_saved_upload_paths([
            *(it.get("local_file") for it in items if it.get("local_file")),
        ])
        raise
    except Exception as e:
        cleanup_saved_upload_paths([
            *(it.get("local_file") for it in items if it.get("local_file")),
        ])
        logger.error(f"Error validating batch chunk for batch {batch_id}: {e}")
        raise HTTPException(500, "Не удалось обработать чанк")

    if not items:
        raise HTTPException(400, "Чанк не содержит изображений")

    for it in items:
        index = current_count
        current_count += 1
        session.add(
            BatchItem(
                batch_id=batch.id,
                index=index,
                image_url=it["image_url"],
                local_file=it.get("local_file"),
                chunk_hash=chunk_hash,
            )
        )

    batch.total_count = current_count
    if isLastChunk:
        batch.last_chunk_received = True

    await session.commit()

    upload_complete = bool(batch.last_chunk_received) and current_count == (batch.expected_count or 0)
    if upload_complete:
        try:
            await enqueue_job_once("start_batch_processing", batch.id, _job_id=f"batch:{batch.id}")
        except Exception as e:
            logger.error(f"Failed to enqueue finalized batch {batch.id}: {e}")
            deleted_files = await cleanup_batch_storage(
                session,
                batch,
                include_item_uploads=True,
                include_results=False,
                include_common_refs=True,
            )
            await session.delete(batch)
            await session.commit()
            raise HTTPException(503, "Очередь временно недоступна")

    if batch.last_chunk_received and not upload_complete:
        logger.warning(
            f"[batch-upload] batch {batch.id} marked last chunk but received_count={current_count} "
            f"expected_count={batch.expected_count}"
        )

    return {
        "batch_id": batch.id,
        "status": "ready" if upload_complete else "pending",
        "chunk_hash": chunk_hash,
        "accepted_items": len(items),
        "received_count": current_count,
        "expected_count": batch.expected_count,
        "last_chunk_received": bool(batch.last_chunk_received),
        "upload_complete": upload_complete,
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
        raise HTTPException(404, "Пакет не найден")
    received_count, upload_complete = await _serialize_batch_progress(session, batch)

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
            "total": batch.expected_count or batch.total_count,
            "processed": batch.processed_count,
            "success": batch.success_count,
            "failed": batch.failed_count,
            "current_index": batch.current_item_index,
            "percent": round(batch.processed_count / (batch.expected_count or batch.total_count) * 100, 1)
            if (batch.expected_count or batch.total_count) > 0
            else 0,
        },
        "prompt": batch.prompt,
        "resolution": batch.resolution,
        "aspect_ratio": batch.aspect_ratio,
        "google_search": batch.google_search,
        "output_format": batch.output_format,
        "expected_count": batch.expected_count or batch.total_count,
        "received_count": received_count,
        "last_chunk_received": batch.last_chunk_received,
        "upload_complete": upload_complete,
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

    items_payload = []
    for b in batches:
        received_count, upload_complete = await _serialize_batch_progress(session, b)
        items_payload.append(
            {
                "batch_id": b.id,
                "chat_id": b.chat_id,
                "status": b.status,
                "total": b.total_count,
                "expected_count": b.expected_count or b.total_count,
                "received_count": received_count,
                "last_chunk_received": b.last_chunk_received,
                "upload_complete": upload_complete,
                "processed": b.processed_count,
                "success": b.success_count,
                "failed": b.failed_count,
                "common_refs_count": len(json.loads(b.common_refs_json or "[]")),
                "google_search": b.google_search,
                "output_format": b.output_format,
                "created_at": b.created_at,
                "completed_at": b.completed_at,
            }
        )

    return {
        "items": items_payload,
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
        raise HTTPException(404, "Пакет не найден")

    if batch.status not in ("pending", "processing"):
        raise HTTPException(400, f"Нельзя отменить пакет в статусе {batch.status}")

    # Если пакет ещё не стартовал — отменяем сразу
    if batch.status == "pending":
        batch.status = "cancelled"
        batch.completed_at = datetime.now(timezone.utc)
        deleted_files = await cleanup_batch_storage(session, batch, include_item_uploads=True, include_results=False)
        await session.commit()
        return {"status": "cancelled", "message": "Пакет отменен до начала обработки.", "deleted_files": deleted_files}

    # processing -> просим остановить ПОСЛЕ текущего элемента
    batch.status = "cancelling"
    await session.commit()

    return {
        "status": "cancelling",
        "message": "Запрошена отмена пакета. Текущий элемент завершится, после чего обработка остановится.",
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

    items_payload = []
    for b in batches:
        received_count, upload_complete = await _serialize_batch_progress(session, b)
        items_payload.append(
            {
                "batch_id": b.id,
                "user_id": b.user_id,
                "chat_id": b.chat_id,
                "status": b.status,
                "total": b.total_count,
                "expected_count": b.expected_count or b.total_count,
                "received_count": received_count,
                "last_chunk_received": b.last_chunk_received,
                "upload_complete": upload_complete,
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
        )

    return {
        "items": items_payload,
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
        raise HTTPException(404, "Пакетная задача не найдена")

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
        "expected_count": batch.expected_count or batch.total_count,
        "received_count": await _count_batch_items(session, batch_id),
        "last_chunk_received": batch.last_chunk_received,
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
