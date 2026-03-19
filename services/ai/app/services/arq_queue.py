import uuid
import json

from datetime import datetime, timezone
from typing import Optional

from arq import Retry, create_pool
from arq.connections import RedisSettings
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import logger
from app.database.session import AsyncSessionLocal
from app.models.batch import BatchJob, BatchItem
from app.models.task import Task
from app.models.message import Message
from app.clients.billing_client import BillingClient, BillingNoFunds
from app.clients.gemini_client import GeminiClient, GeminiError
from app.services.history import touch_chat
from app.services.gemini_images import load_reference_image, save_generated_image

# Настройки Redis для ARQ
REDIS_SETTINGS = RedisSettings.from_dsn(
    settings.REDIS_URL.replace("/0", "/1") if settings.REDIS_URL else "redis://localhost:6379/1"
)

_pool = None
billing = BillingClient(settings.BILLING_BASE_URL, settings.BILLING_INTERNAL_TOKEN)
gemini = GeminiClient()


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


async def enqueue_job_once(job_name: str, *args, _job_id: str | None = None) -> None:
    pool = await get_arq_pool()
    await pool.enqueue_job(job_name, *args, _job_id=_job_id)


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


def _load_batch_common_refs(batch: BatchJob) -> list[str]:
    """Загрузить общие референсы пакета из JSON"""
    raw = batch.common_refs_json or "[]"

    try:
        data = json.loads(raw)
    except Exception:
        logger.warning(f"Failed to parse common refs for batch {batch.id}")
        return []

    if not isinstance(data, list):
        return []

    refs: list[str] = []
    seen: set[str] = set()

    for value in data:
        ref = str(value or "").strip()
        if not ref or ref in seen:
            continue
        refs.append(ref)
        seen.add(ref)

    return refs


def _retry_defer_seconds(job_try: int) -> int:
    return max(1, job_try) * settings.ARQ_RETRY_BASE_SECONDS


async def _claim_gemini_task(session: AsyncSession, task_id: str) -> Task | None:
    result = await session.execute(
        update(Task)
        .where(
            Task.task_id == task_id,
            Task.provider == "gemini",
            Task.status == "queued",
        )
        .values(status="running")
    )
    await session.commit()
    if result.rowcount == 0:
        return None
    return await session.get(Task, task_id)


async def _sync_batch_progress(session: AsyncSession, batch: BatchJob) -> None:
    items = (
        await session.execute(
            select(BatchItem).where(BatchItem.batch_id == batch.id).order_by(BatchItem.index)
        )
    ).scalars().all()

    batch.total_count = len(items)
    batch.success_count = sum(1 for item in items if item.status == "success")
    batch.failed_count = sum(1 for item in items if item.status == "failed")
    batch.processed_count = batch.success_count + batch.failed_count

    next_pending = next((item.index for item in items if item.status in ("pending", "processing")), batch.total_count)
    batch.current_item_index = next_pending


def _compose_batch_image_urls(batch: BatchJob, item: BatchItem) -> tuple[list[str], list[str]]:
    image_url = item.image_url
    local_files: list[str] = []
    if item.local_file:
        image_url = f"{settings.PUBLIC_BASE_URL}/media/{item.local_file}"
        local_files.append(item.local_file)

    common_refs = _load_batch_common_refs(batch)
    image_urls: list[str] = []
    seen: set[str] = set()

    for ref in common_refs:
        ref = (ref or "").strip()
        if not ref or ref == image_url or ref in seen:
            continue
        image_urls.append(ref)
        seen.add(ref)

    if image_url:
        image_urls.append(image_url)

    return image_urls, local_files


async def _enqueue_billing_reconcile(task_id: str) -> None:
    await enqueue_job_once("reconcile_gemini_task_billing", task_id, _job_id=f"gemini-billing:{task_id}")


async def _mark_task_failed_without_billing(
    session: AsyncSession,
    task: Task,
    *,
    error_message: str,
    assistant_content: str = "",
) -> None:
    task.status = "failed"
    task.error_message = error_message

    if task.chat_id:
        exists_q = select(Message.id).where(
            Message.chat_id == task.chat_id,
            Message.task_id == task.task_id,
            Message.role == "assistant",
        )
        exists = (await session.execute(exists_q)).first()
        if not exists:
            session.add(
                Message(
                    chat_id=task.chat_id,
                    user_id=task.user_id,
                    role="assistant",
                    content=assistant_content,
                    meta_json=json.dumps(
                        {
                            "successFlag": 2,
                            "resultImageUrl": None,
                            "errorMessage": error_message,
                        },
                        ensure_ascii=False,
                    ),
                    task_id=task.task_id,
                )
            )
            await touch_chat(session, task.chat_id)

    if task.batch_item_id:
        batch_item = await session.get(BatchItem, task.batch_item_id)
        if batch_item:
            batch_item.status = "failed"
            batch_item.error_message = error_message
            batch_item.completed_at = datetime.now(timezone.utc)

    await session.commit()


async def _finalize_gemini_task(
    session: AsyncSession,
    task: Task,
    *,
    result_image_url: str | None = None,
    error_message: str | None = None,
) -> None:
    logger.info(
        f"[gemini-task] finalizing task={task.task_id} provider={task.provider} "
        f"result={'success' if result_image_url else 'failed'} billing_state={task.billing_state}"
    )
    if result_image_url:
        task.status = "success"
        task.result_image_url = result_image_url
        task.error_message = None
    else:
        task.status = "failed"
        task.error_message = error_message or "Generation failed"

    cleanup_needed = task.status in ("success", "failed")
    if cleanup_needed:
        from app.services.uploads import cleanup_task_files

        cleanup_task_files(task)

    if task.status == "success":
        try:
            logger.info(f"[gemini-task] confirming billing for task={task.task_id} request_id={task.billing_request_id}")
            await billing.confirm(user_id=str(task.user_id), request_id=task.billing_request_id, task_id=task.task_id)
            task.billing_state = "confirmed"
            logger.info(f"[gemini-task] billing confirmed for task={task.task_id}")
        except Exception as e:
            task.billing_state = "confirm_pending"
            logger.error(f"[billing] gemini confirm failed task={task.task_id}: {e}")
            await _enqueue_billing_reconcile(task.task_id)
    else:
        try:
            logger.info(f"[gemini-task] refunding billing for task={task.task_id} request_id={task.billing_request_id}")
            await billing.fail(
                user_id=str(task.user_id),
                request_id=task.billing_request_id,
                task_id=task.task_id,
                error=task.error_message,
            )
            task.billing_state = "refunded"
            logger.info(f"[gemini-task] billing refunded for task={task.task_id}")
        except Exception as e:
            task.billing_state = "refund_pending"
            logger.error(f"[billing] gemini refund failed task={task.task_id}: {e}")
            await _enqueue_billing_reconcile(task.task_id)

    if task.chat_id:
        exists_q = select(Message.id).where(
            Message.chat_id == task.chat_id,
            Message.task_id == task.task_id,
            Message.role == "assistant",
        )
        exists = (await session.execute(exists_q)).first()
        if not exists:
            meta = {
                "successFlag": 1 if task.status == "success" else 2,
                "resultImageUrl": task.result_image_url,
                "errorMessage": task.error_message,
            }
            session.add(
                Message(
                    chat_id=task.chat_id,
                    user_id=task.user_id,
                    role="assistant",
                    content="",
                    meta_json=json.dumps(meta, ensure_ascii=False),
                    task_id=task.task_id,
                )
            )
            await touch_chat(session, task.chat_id)

    if task.batch_item_id:
        batch_item = await session.get(BatchItem, task.batch_item_id)
        if batch_item:
            batch_item.status = task.status
            batch_item.result_image_url = task.result_image_url
            batch_item.error_message = task.error_message
            batch_item.completed_at = datetime.now(timezone.utc)

    await session.commit()


async def _execute_gemini_task(session: AsyncSession, task: Task, ctx: dict) -> None:
    try:
        if not settings.GEMINI_API_ENABLED:
            logger.warning(f"[gemini-task] task={task.task_id} blocked: GEMINI_API_ENABLED is false")
            await _finalize_gemini_task(session, task, error_message="Image generation is temporarily unavailable")
            return

        image_urls = json.loads(task.image_urls or "[]")
        logger.info(
            f"[gemini-task] task={task.task_id} preparing request images={len(image_urls)} "
            f"resolution={task.resolution} aspect={task.aspect_ratio} "
            f"output={task.output_format} google_search={task.google_search}"
        )
        inputs = []
        for image_url in image_urls:
            inputs.append(await load_reference_image(str(image_url)))

        logger.info(f"[gemini-task] task={task.task_id} sending request to Gemini model={gemini.model}")
        result = await gemini.generate_image(
            prompt=task.prompt,
            images=inputs,
            resolution=task.resolution,
            aspect_ratio=task.aspect_ratio,
            google_search=task.google_search,
        )
        logger.info(
            f"[gemini-task] task={task.task_id} received Gemini response "
            f"mime={result['mime_type']} bytes={len(result['image_bytes'])}"
        )
        rel_path = save_generated_image(
            result["image_bytes"],
            mime_type=result["mime_type"],
            output_format=task.output_format,
        )
        logger.info(f"[gemini-task] task={task.task_id} saved image to media/{rel_path}")
        result_url = f"{settings.PUBLIC_BASE_URL}/media/{rel_path}"
        await _finalize_gemini_task(session, task, result_image_url=result_url)
    except GeminiError as e:
        job_try = int(ctx.get("job_try", 1))
        if e.retryable and job_try < settings.ARQ_MAX_TRIES:
            task.status = "queued"
            task.error_message = f"Temporary provider error, retry {job_try}/{settings.ARQ_MAX_TRIES - 1}"
            await session.commit()
            logger.warning(
                f"[gemini-task] task={task.task_id} scheduling retry "
                f"job_try={job_try} defer={_retry_defer_seconds(job_try)}s error={e}"
            )
            raise Retry(defer=_retry_defer_seconds(job_try))

        logger.error(f"Gemini generation failed for task {task.task_id}: {e}")
        await _finalize_gemini_task(session, task, error_message=str(e))
    except Exception as e:
        logger.exception(f"Unexpected Gemini task error for {task.task_id}: {e}")
        await _finalize_gemini_task(session, task, error_message=str(e))


async def reconcile_gemini_task_billing(ctx, task_id: str) -> None:
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task or task.provider != "gemini":
            return

        try:
            if task.status == "success" and task.billing_state != "confirmed":
                logger.info(f"[gemini-billing] retry confirm for task={task_id}")
                await billing.confirm(user_id=str(task.user_id), request_id=task.billing_request_id, task_id=task.task_id)
                task.billing_state = "confirmed"
            elif task.status == "failed" and task.billing_state != "refunded":
                logger.info(f"[gemini-billing] retry refund for task={task_id}")
                await billing.fail(
                    user_id=str(task.user_id),
                    request_id=task.billing_request_id,
                    task_id=task.task_id,
                    error=task.error_message,
                )
                task.billing_state = "refunded"
            await session.commit()
        except Exception as e:
            job_try = int(ctx.get("job_try", 1))
            logger.error(f"Gemini billing reconcile failed for task {task_id}: {e}")
            if job_try < settings.ARQ_MAX_TRIES:
                raise Retry(defer=_retry_defer_seconds(job_try))


async def process_gemini_task(ctx, task_id: str) -> None:
    logger.info(f"Processing Gemini task {task_id}")

    async with AsyncSessionLocal() as session:
        task = await _claim_gemini_task(session, task_id)
        if not task:
            current = await session.get(Task, task_id)
            if current:
                logger.info(f"Task {task_id} skip: provider={current.provider} status={current.status}")
            else:
                logger.error(f"Gemini task not found: {task_id}")
            return

        logger.info(
            f"[gemini-task] claimed task={task.task_id} status={task.status} "
            f"billing_state={task.billing_state} user={task.user_id} batch_item={task.batch_item_id}"
        )

        await _execute_gemini_task(session, task, ctx)

        if task.batch_item_id:
            batch_item = await session.get(BatchItem, task.batch_item_id)
            if batch_item:
                await enqueue_job_once("start_batch_processing", batch_item.batch_id, _job_id=f"batch:{batch_item.batch_id}")


async def recover_gemini_tasks(ctx) -> None:
    logger.info("Starting Gemini task recovery scan")

    async with AsyncSessionLocal() as session:
        waiting_rows = await session.execute(
            select(Task.task_id)
            .where(
                Task.provider == "gemini",
                Task.status == "waiting_billing",
            )
        )
        waiting_task_ids = [row[0] for row in waiting_rows.all()]

        await session.execute(
            update(Task)
            .where(
                Task.provider == "gemini",
                Task.status == "running",
            )
            .values(status="queued")
        )
        await session.commit()

        rows = await session.execute(
            select(Task.task_id)
            .where(
                Task.provider == "gemini",
                Task.status == "queued",
            )
        )
        task_ids = [row[0] for row in rows.all()]

    if waiting_task_ids:
        logger.info(f"Gemini recovery found {len(waiting_task_ids)} waiting_billing task(s)")
        async with AsyncSessionLocal() as session:
            for task_id in waiting_task_ids:
                task = await session.get(Task, task_id)
                if not task:
                    continue
                try:
                    await billing.cancel(user_id=str(task.user_id), request_id=task.billing_request_id)
                except Exception:
                    pass
                await _mark_task_failed_without_billing(
                    session,
                    task,
                    error_message="Task interrupted before reservation was finalized",
                )
                if task.batch_item_id:
                    batch_item = await session.get(BatchItem, task.batch_item_id)
                    if batch_item:
                        await enqueue_job_once(
                            "start_batch_processing",
                            batch_item.batch_id,
                            _job_id=f"batch:{batch_item.batch_id}",
                        )

    if not task_ids:
        logger.info("Gemini task recovery scan finished: nothing to recover")
        return

    logger.info(f"Gemini recovery found {len(task_ids)} task(s)")
    for task_id in task_ids:
        try:
            await enqueue_job_once("process_gemini_task", task_id, _job_id=f"gemini-task:{task_id}")
        except Exception as e:
            logger.error(f"Failed to re-enqueue Gemini task {task_id}: {e}")


async def startup_recover_gemini_tasks() -> None:
    try:
        await recover_gemini_tasks({})
        await recover_batches({})
        await recover_gemini_billing({})
    except Exception as e:
        logger.error(f"Startup Gemini recovery failed: {e}")


async def process_batch_item(
    ctx,
    batch_id: str,
    item_id: str,
    item_index: int
):
    logger.info(f"Processing batch item {item_id} (batch {batch_id}, index {item_index})")

    async with AsyncSessionLocal() as session:
        batch = await session.get(BatchJob, batch_id)
        item = await session.get(BatchItem, item_id)
        if not batch or not item:
            logger.error(f"Batch or item not found: {batch_id}/{item_id}")
            return

        if batch.status == "cancelling":
            logger.info(f"Batch {batch_id} is cancelling before item start, skip item={item_index}")
            return

        claim = await session.execute(
            update(BatchItem)
            .where(BatchItem.id == item_id, BatchItem.status == "pending")
            .values(status="processing", started_at=datetime.now(timezone.utc))
        )
        await session.commit()
        if claim.rowcount == 0:
            logger.info(f"Batch item {item_id} already claimed or finished")
            return

        await session.refresh(item)

        request_id = str(uuid.uuid4())
        try:
            image_urls, local_files = _compose_batch_image_urls(batch, item)
            task_id = uuid.uuid4().hex
            task = Task(
                task_id=task_id,
                status="waiting_billing",
                prompt=batch.prompt,
                image_urls=json.dumps(image_urls, ensure_ascii=False),
                local_files=json.dumps(local_files, ensure_ascii=False),
                resolution=batch.resolution,
                aspect_ratio=batch.aspect_ratio,
                output_format=batch.output_format,
                google_search=batch.google_search,
                provider="gemini",
                chat_id=batch.chat_id,
                user_id=batch.user_id,
                batch_item_id=item.id,
                billing_request_id=request_id,
                billing_state="none",
            )
            session.add(task)
            item.task_id = task_id
            await session.flush()
            logger.info(
                f"[batch] created local gemini task={task_id} item={item_id} batch={batch_id} status=waiting_billing"
            )

            meta = {
                "batch": True,
                "batch_item_index": item_index,
                "image_url": item.image_url,
                "common_refs_count": len(_load_batch_common_refs(batch)),
                "googleSearch": batch.google_search,
                "outputFormat": batch.output_format,
            }
            await _add_message_to_chat(
                session=session,
                chat_id=batch.chat_id,
                user_id=batch.user_id,
                role="user",
                content=f"[Пакетная обработка {item_index + 1}/{batch.total_count}] {batch.prompt}",
                meta=meta,
                task_id=task_id,
            )
            await session.commit()

            try:
                logger.info(f"[batch] reserving billing for task={task_id} request_id={request_id}")
                await billing.reserve(user_id=str(batch.user_id), request_id=request_id, cost=1)
                logger.info(f"Reserved 1 token for Gemini task {task_id}, request_id={request_id}")
            except BillingNoFunds:
                logger.warning(f"Not enough funds for item {item_id}")
                await _mark_task_failed_without_billing(
                    session,
                    task,
                    error_message="Not enough requests to process this item",
                    assistant_content=f"Недостаточно средств для обработки элемента {item_index + 1}/{batch.total_count}",
                )
                await session.refresh(batch)
                await _sync_batch_progress(session, batch)
                batch.current_item_index = item_index + 1
                await session.commit()
                return
            except Exception as e:
                logger.error(f"Billing reserve failed for item {item_id}: {e}")
                await _mark_task_failed_without_billing(
                    session,
                    task,
                    error_message=f"Billing error: {str(e)}",
                    assistant_content=f"Ошибка биллинга при обработке элемента {item_index + 1}/{batch.total_count}: {str(e)}",
                )
                await session.refresh(batch)
                await _sync_batch_progress(session, batch)
                batch.current_item_index = item_index + 1
                await session.commit()
                return

            task.status = "queued"
            task.billing_state = "reserved"
            await session.commit()
            logger.info(f"[batch] task={task_id} moved to status=queued billing_state=reserved")

            claimed_task = await _claim_gemini_task(session, task_id)
            if not claimed_task:
                raise RuntimeError(f"Failed to claim Gemini task {task_id} for batch item {item_id}")
            try:
                await _execute_gemini_task(session, claimed_task, ctx)
            except Retry:
                logger.info(f"Retry requested for batch task {task_id}, marking item failed to keep batch moving")
                await _finalize_gemini_task(session, claimed_task, error_message="Temporary provider error")

            await session.refresh(item)
            await session.refresh(batch)
            await _sync_batch_progress(session, batch)
            batch.current_item_index = item_index + 1
            await session.commit()

        except Exception as e:
            logger.exception(f"Error processing batch item {item_id}: {e}")

            try:
                await billing.cancel(
                    user_id=str(batch.user_id),
                    request_id=request_id,
                )
                logger.info(f"Billing cancel called for failed item {item_id}")
            except Exception as billing_error:
                logger.error(f"Failed to cancel billing after error: {billing_error}")

            item.status = "failed"
            item.error_message = str(e)
            item.completed_at = datetime.now(timezone.utc)
            await _sync_batch_progress(session, batch)
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
            await session.commit()


async def recover_batches(ctx) -> None:
    logger.info("Starting batch recovery scan")

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(BatchJob.id).where(BatchJob.status.in_(("processing", "cancelling")))
        )
        batch_ids = [row[0] for row in rows.all()]

    if not batch_ids:
        logger.info("Batch recovery scan finished: nothing to recover")
        return

    logger.info(f"Batch recovery found {len(batch_ids)} batch job(s)")
    for batch_id in batch_ids:
        try:
            await enqueue_job_once("start_batch_processing", batch_id, _job_id=f"batch:{batch_id}")
        except Exception as e:
            logger.error(f"Failed to re-enqueue batch {batch_id}: {e}")


async def recover_gemini_billing(ctx) -> None:
    logger.info("Starting Gemini billing recovery scan")

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Task.task_id).where(
                Task.provider == "gemini",
                Task.status.in_(("success", "failed")),
                Task.billing_state.in_(("confirm_pending", "refund_pending")),
            )
        )
        task_ids = [row[0] for row in rows.all()]

    for task_id in task_ids:
        try:
            await _enqueue_billing_reconcile(task_id)
        except Exception as e:
            logger.error(f"Failed to schedule Gemini billing recovery for {task_id}: {e}")


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

        if batch.status == "pending":
            res = await session.execute(
                update(BatchJob)
                .where(BatchJob.id == batch_id, BatchJob.status == "pending")
                .values(status="processing", started_at=datetime.now(timezone.utc))
            )
            await session.commit()
            if res.rowcount == 0:
                logger.warning(f"Batch {batch_id} could not be claimed from pending")
                return
        elif batch.status not in ("processing", "cancelling"):
            logger.warning(f"Batch {batch_id} has incompatible status for processing: {batch.status}")
            return

        await session.refresh(batch)
        await _sync_batch_progress(session, batch)
        await session.commit()

        # Получаем все элементы
        items = await session.execute(
            select(BatchItem)
            .where(BatchItem.batch_id == batch_id)
            .order_by(BatchItem.index)
        )
        items = items.scalars().all()

        # Последовательно обрабатываем каждый элемент
        blocked_by_processing_item = False
        for item in items:
            await session.refresh(batch)
            if batch.status == "cancelling":
                logger.info(f"Batch {batch_id} cancelled, stopping")
                break

            if item.status in ("success", "failed"):
                continue

            if item.status == "processing":
                logger.info(f"Batch {batch_id} waiting for in-flight item {item.id}")
                blocked_by_processing_item = True
                break

            await process_batch_item(ctx, batch_id, item.id, item.index)
            await session.refresh(item)
            if item.status == "processing":
                blocked_by_processing_item = True
                await session.refresh(batch)
                await _sync_batch_progress(session, batch)
                await session.commit()
                logger.info(f"Batch {batch_id} paused after handing off item {item.id} to async retry flow")
                break
            await session.refresh(batch)
            await _sync_batch_progress(session, batch)
            await session.commit()

        await session.refresh(batch)
        await _sync_batch_progress(session, batch)
        if blocked_by_processing_item:
            await session.commit()
            logger.info(f"Batch {batch_id} paused until in-flight item completes")
            return

        if batch.status == "cancelling":
            batch.status = "cancelled"
        elif batch.failed_count == batch.total_count:
            batch.status = "failed"
        elif batch.success_count > 0 and batch.failed_count > 0:
            batch.status = "partial"
        else:
            batch.status = "completed"

        batch.completed_at = datetime.now(timezone.utc)

        await session.commit()
        logger.info(f"Batch {batch_id} finished with status {batch.status}")


# Регистрируем функции для ARQ
WORKER_FUNCTIONS = [
    process_gemini_task,
    reconcile_gemini_task_billing,
    start_batch_processing,
    process_batch_item,
]
