import asyncio
import json

from datetime import datetime, timezone
from typing import Optional

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import logger
from app.database.session import AsyncSessionLocal
from app.models.batch import BatchJob, BatchItem
from app.models.task import Task
from app.models.message import Message
from app.clients.nanobanana_client import NanoBananaClient
from app.services.history import touch_chat

# Настройки Redis для ARQ
REDIS_SETTINGS = RedisSettings.from_dsn(
    settings.REDIS_URL.replace("/0", "/1") if settings.REDIS_URL else "redis://localhost:6379/1"
)

_pool = None


async def get_arq_pool():
    """Получить или создать пул соединений для ARQ"""
    global _pool
    if _pool is None:
        _pool = await create_pool(REDIS_SETTINGS)
    return _pool


async def close_arq_pool():
    """Закрыть пул при завершении"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def _get_db_session() -> AsyncSession:
    """Получить сессию БД для воркера"""
    async with AsyncSessionLocal() as session:
        return session


async def _create_nanobanana_task(
        prompt: str,
        image_url: Optional[str],
        resolution: str,
        aspect_ratio: str,
        callback_url: str
) -> str:
    """Создать задачу в NanoBanana для одного изображения"""
    client = NanoBananaClient()

    # Формируем payload как в generate-pro, но с одним URL
    image_urls = [image_url] if image_url else []

    payload = {
        "prompt": prompt,
        "imageUrls": image_urls,
        "resolution": resolution,
        "aspectRatio": aspect_ratio,
        "callBackUrl": callback_url,
    }

    # Запускаем в thread, т.к. клиент синхронный
    import anyio
    res = await anyio.to_thread.run_sync(client.generate_pro, payload)

    if res.get("code") != 200 or not (res.get("data") or {}).get("taskId"):
        raise Exception(res.get("msg", "NanoBanana error"))

    return res["data"]["taskId"]


async def _update_task_status_from_callback(
        task_id: str,
        session: AsyncSession
) -> Optional[BatchItem]:
    """
    Обновить статус задачи через опрос NanoBanana и вернуть связанный BatchItem если задача завершена.
    Использует существующую логику get_task из tasks.py
    """
    from app.api.v1.tasks import _get_task_status  # импортируем существующую функцию

    task = await session.get(Task, task_id)
    if not task:
        logger.warning(f"Task {task_id} not found")
        return None

    # Используем существующую логику опроса
    old_status = task.status
    await _get_task_status(task, session)

    # Если статус изменился, логируем
    if old_status != task.status:
        logger.info(f"Task {task_id} status changed: {old_status} -> {task.status}")

    # Если задача финализировалась, ищем связанный BatchItem
    if task.status in ("success", "failed") and task.batch_item_id:
        batch_item = await session.get(BatchItem, task.batch_item_id)
        if batch_item:
            # Обновляем BatchItem в соответствии со статусом задачи
            batch_item.status = task.status
            batch_item.result_image_url = task.result_image_url
            batch_item.error_message = task.error_message
            batch_item.completed_at = datetime.now(timezone.utc)
            await session.commit()
            return batch_item

    return None


async def _add_message_to_chat(
        session: AsyncSession,
        chat_id: str,
        user_id: str,
        role: str,
        content: str,
        meta: dict,
        task_id: Optional[str] = None
):
    """Добавить сообщение в чат"""
    message = Message(
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        content=content,
        meta_json=json.dumps(meta, ensure_ascii=False),
        task_id=task_id
    )
    session.add(message)
    await touch_chat(session, chat_id)


async def process_batch_item(
        ctx,
        batch_id: str,
        item_id: str,
        item_index: int
):
    """
    Обработать один элемент пакета.
    Эта функция запускается ARQ и ждет завершения задачи.
    """
    logger.info(f"Processing batch item {item_id} (batch {batch_id}, index {item_index})")

    async with AsyncSessionLocal() as session:
        # Получаем BatchJob и BatchItem
        batch = await session.get(BatchJob, batch_id)
        item = await session.get(BatchItem, item_id)

        if not batch or not item:
            logger.error(f"Batch or item not found: {batch_id}/{item_id}")
            return

        # Проверяем, не отменен ли пакет
        if batch.status == "cancelling":
            logger.info(f"Batch {batch_id} is cancelling, skipping item {item_index}")
            return

        # Помечаем элемент как обрабатываемый
        item.status = "processing"
        item.started_at = datetime.now(timezone.utc)
        await session.commit()

        try:
            # Создаем задачу в NanoBanana
            callback_url = f"{settings.PUBLIC_BASE_URL}/api/v1/ai/nanobanana/callback"
            image_url = item.image_url
            if item.local_file:
                image_url = f"{settings.PUBLIC_BASE_URL}/media/{item.local_file}"

            task_id = await _create_nanobanana_task(
                prompt=batch.prompt,
                image_url=image_url,
                resolution=batch.resolution,
                aspect_ratio=batch.aspect_ratio,
                callback_url=callback_url
            )

            # Создаем Task в БД
            task = Task(
                task_id=task_id,
                status="running",
                prompt=batch.prompt,
                image_urls=json.dumps([image_url] if image_url else [], ensure_ascii=False),
                resolution=batch.resolution,
                aspect_ratio=batch.aspect_ratio,
                chat_id=batch.chat_id,
                user_id=batch.user_id,
                batch_item_id=item.id,
                billing_state="none"  # биллинг уже зарезервирован на уровне пакета
            )
            await session.merge(task)

            item.task_id = task_id
            await session.commit()

            # Добавляем user message в чат
            meta = {
                "batch": True,
                "batch_item_index": item_index,
                "image_url": image_url
            }
            await _add_message_to_chat(
                session=session,
                chat_id=batch.chat_id,
                user_id=batch.user_id,
                role="user",
                content=f"[Пакетная обработка {item_index + 1}/{batch.total_count}] {batch.prompt}",
                meta=meta,
                task_id=task_id
            )

            # Ждем завершения задачи с активным опросом
            max_wait = 300  # 5 минут
            poll_interval = settings.POLL_INTERVAL_SECONDS
            waited = 0
            task_completed = False

            while waited < max_wait and not task_completed:
                await asyncio.sleep(poll_interval)
                waited += poll_interval

                # Вызываем функцию обновления статуса - она опросит NanoBanana и обновит БД
                completed_item = await _update_task_status_from_callback(task_id, session)

                # Если задача завершена, выходим из цикла
                if completed_item:
                    logger.info(f"Item {item_id} completed with status {completed_item.status}")
                    task_completed = True
                    break

                # Проверяем, не отменен ли пакет
                await session.refresh(batch)
                if batch.status == "cancelling":
                    logger.info(f"Batch {batch_id} cancelled during item {item_index} processing")
                    return

            # Если вышли по таймауту
            if not task_completed:
                logger.error(f"Item {item_id} timed out after {max_wait} seconds")
                item.status = "failed"
                item.error_message = "Timeout waiting for task completion"
                item.completed_at = datetime.now(timezone.utc)
                batch.failed_count += 1

                await _add_message_to_chat(
                    session=session,
                    chat_id=batch.chat_id,
                    user_id=batch.user_id,
                    role="assistant",
                    content=f"Таймаут при обработке элемента {item_index + 1}/{batch.total_count}",
                    meta={"batch_item_error": True, "timeout": True}
                )

            # Обновляем счетчики (если еще не обновлено через completed_item)
            await session.refresh(item)
            await session.refresh(batch)

            if item.status == "success":
                batch.success_count = batch.success_count
            elif item.status == "failed":
                batch.failed_count = batch.failed_count

            batch.processed_count += 1
            batch.current_item_index = item_index + 1
            await session.commit()

        except Exception as e:
            logger.exception(f"Error processing batch item {item_id}: {e}")

            item.status = "failed"
            item.error_message = str(e)
            item.completed_at = datetime.now(timezone.utc)
            batch.failed_count += 1
            batch.processed_count += 1
            batch.current_item_index = item_index + 1
            await session.commit()

            await _add_message_to_chat(
                session=session,
                chat_id=batch.chat_id,
                user_id=batch.user_id,
                role="assistant",
                content=f"Системная ошибка при обработке элемента {item_index + 1}/{batch.total_count}: {str(e)}",
                meta={"batch_item_error": True}
            )


async def start_batch_processing(ctx, batch_id: str):
    """
    Запустить обработку пакета.
    Эта функция последовательно ставит элементы в очередь.
    """
    logger.info(f"Starting batch processing: {batch_id}")

    async with AsyncSessionLocal() as session:
        batch = await session.get(BatchJob, batch_id)
        if not batch:
            logger.error(f"Batch {batch_id} not found")
            return

        if batch.status != "pending":
            logger.warning(f"Batch {batch_id} already started (status={batch.status})")
            return

        # Меняем статус на processing
        batch.status = "processing"
        batch.started_at = datetime.now(timezone.utc)
        await session.commit()

        # Получаем все элементы
        items = await session.execute(
            select(BatchItem)
            .where(BatchItem.batch_id == batch_id)
            .order_by(BatchItem.index)
        )
        items = items.scalars().all()

        # Последовательно обрабатываем каждый элемент
        for item in items:
            # Проверяем, не отменен ли пакет
            await session.refresh(batch)
            if batch.status == "cancelling":
                logger.info(f"Batch {batch_id} cancelled, stopping")
                break

            # Пропускаем уже обработанные
            if item.status in ("success", "failed"):
                continue

            # Ставим элемент на обработку и ждем его завершения
            await process_batch_item(ctx, batch_id, item.id, item.index)

        # После завершения всех элементов определяем финальный статус
        await session.refresh(batch)

        if batch.status == "cancelling":
            batch.status = "cancelled"
        elif batch.failed_count == batch.total_count:
            batch.status = "failed"
        elif batch.success_count > 0 and batch.failed_count > 0:
            batch.status = "partial"
        else:
            batch.status = "completed"

        batch.completed_at = datetime.now(timezone.utc)

        # Добавляем финальное сообщение
        summary = (
            f"Пакетная обработка завершена.\n"
            f"Всего: {batch.total_count}\n"
            f"Успешно: {batch.success_count}\n"
            f"Ошибок: {batch.failed_count}"
        )

        await _add_message_to_chat(
            session=session,
            chat_id=batch.chat_id,
            user_id=batch.user_id,
            role="assistant",
            content=summary,
            meta={"batch_complete": True, "batch_status": batch.status}
        )

        await session.commit()
        logger.info(f"Batch {batch_id} finished with status {batch.status}")


# Регистрируем функции для ARQ
WORKER_FUNCTIONS = [
    start_batch_processing,
    process_batch_item,
]
