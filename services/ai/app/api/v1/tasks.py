import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import anyio
from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import logger
from app.core.rate_limit import limit_generate
from app.database.session import get_async_session
from app.api.deps import get_current_user

from app.models.task import Task
from app.models.chat import Chat
from app.models.message import Message

from app.services.titles import make_chat_title
from app.services.history import get_last_success_result_url, touch_chat
from app.services.uploads import save_upload, cleanup_task_files
from app.services.file_lifecycle import cleanup_saved_upload_paths
from app.services.arq_queue import enqueue_job_once
from app.clients.gemini_client import SUPPORTED_ASPECT_RATIOS, SUPPORTED_RESOLUTIONS

from app.clients.nanobanana_client import NanoBananaClient
from app.clients.billing_client import BillingClient, BillingNoFunds


router = APIRouter(tags=["tasks"])

client = NanoBananaClient()
billing = BillingClient(settings.BILLING_BASE_URL, settings.BILLING_INTERNAL_TOKEN)


def _translate_upload_error(detail: str) -> str:
    if detail.startswith("Unsupported content-type:"):
        return "Неподдерживаемый тип файла."
    if detail == "File too large":
        return f"Файл слишком большой. Максимум {settings.MAX_UPLOAD_MB} МБ."
    return detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_task_response(task: Task) -> dict:
    success_flag = 0
    if task.status == "success":
        success_flag = 1
    elif task.status == "failed":
        success_flag = 2

    return {
        "code": 200,
        "msg": "success",
        "data": {
            "taskId": task.task_id,
            "successFlag": success_flag,
            "response": {"resultImageUrl": task.result_image_url} if task.result_image_url else None,
            "errorMessage": task.error_message if success_flag == 2 else None,
        },
    }


async def _reconcile_success(t: Task) -> None:
    # confirm могли не успеть сделать в generate-pro (или сеть легла)
    if not t.billing_request_id:
        return
    if t.billing_state == "confirmed":
        return
    try:
        await billing.confirm(user_id=str(t.user_id), request_id=t.billing_request_id, task_id=t.task_id)
        t.billing_state = "confirmed"
    except Exception as e:
        t.billing_state = "confirm_pending"
        logger.error(f"[billing] reconcile confirm failed task={t.task_id}: {e}")


async def _reconcile_failed(t: Task) -> None:
    # если генерация упала — вернуть запрос (cancel если не подтверждали / refund если подтверждали)
    if not t.billing_request_id:
        return
    if t.billing_state == "refunded":
        return
    try:
        await billing.fail(
            user_id=str(t.user_id),
            request_id=t.billing_request_id,
            task_id=t.task_id,
            error=t.error_message,
        )
        t.billing_state = "refunded"
    except Exception as e:
        t.billing_state = "refund_pending"
        logger.error(f"[billing] reconcile fail/refund failed task={t.task_id}: {e}")


def _prepend_chat_result_reference(
    image_urls: list[str],
    last_url: Optional[str],
    limit: int,
) -> list[str]:
    """
    Ставит последнее успешное изображение из чата в начало списка референсов.
    Если лимит превышен, обрезает хвост пользовательских референсов.
    Дубликат last_url повторно не добавляет.
    """
    if limit <= 0:
        return []

    result: list[str] = []

    if last_url:
        last_url = last_url.strip()
        if last_url:
            result.append(last_url)

    for url in image_urls:
        u = (url or "").strip()
        if not u:
            continue
        if last_url and u == last_url:
            continue
        result.append(u)
        if len(result) >= limit:
            break

    return result


async def _get_task_status(task: Task, session: AsyncSession) -> dict:
    """
    Внутренняя функция для получения статуса задачи.
    Вынесена из get_task для переиспользования в batch processing.
    """
    if task.provider == "gemini":
        if task.status == "success":
            await _reconcile_success(task)
            await session.commit()
        elif task.status == "failed":
            await _reconcile_failed(task)
            await session.commit()
        return _build_task_response(task)

    # если финал уже был — reconcile и вернуть
    if task.status in ("success", "failed"):
        if task.status == "success":
            await _reconcile_success(task)
        else:
            await _reconcile_failed(task)
        await session.commit()
        return _build_task_response(task)

    now = _now()
    last = task.last_polled_at
    should_poll = last is None or (now - last) >= timedelta(seconds=settings.POLL_INTERVAL_SECONDS)

    if not should_poll:
        return {
            "code": 200,
            "msg": "success",
            "data": {"taskId": task.task_id, "successFlag": 0, "response": None, "errorMessage": None},
        }

    res = await anyio.to_thread.run_sync(client.record_info, task.task_id)
    task.last_polled_at = now

    if res.get("code") != 200:
        logger.warning(f"NanoBanana record_info failed for task {task.task_id}")
        await session.commit()
        return {
            "code": 200,
            "msg": "success",
            "data": {"taskId": task.task_id, "successFlag": 0, "response": None, "errorMessage": None},
        }

    data = res.get("data") or {}
    success_flag = data.get("successFlag", 0)
    response = data.get("response")

    if success_flag == 1 and response:
        task.status = "success"
        task.result_image_url = (response or {}).get("resultImageUrl")
        cleanup_task_files(task)
        await _reconcile_success(task)

    elif success_flag in (2, 3):
        task.status = "failed"
        task.error_message = data.get("errorMessage")
        cleanup_task_files(task)
        logger.error(f"Task {task.task_id} failed: {task.error_message}")
        await _reconcile_failed(task)

    # если задача финализировалась — кладём assistant-message в историю (1 раз)
    if task.chat_id and task.status in ("success", "failed"):
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
                    content=task.error_message or "",
                    meta_json=json.dumps(meta, ensure_ascii=False),
                    task_id=task.task_id,
                )
            )
            await touch_chat(session, task.chat_id)

    await session.commit()
    return res


@router.post("/generate-pro")
async def generate_pro(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
    prompt: str = Form(...),
    resolution: str = Form("1K"),
    aspectRatio: str = Form("1:1"),
    imageUrls: Optional[List[str]] = Form(default=None),
    images: Optional[List[UploadFile]] = File(default=None),
    chat_id: Optional[str] = Form(default=None),
    googleSearch: bool = Form(default=True),
    outputFormat: str = Form(default="png"),
):
    user_id = user_ctx.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Не авторизован")

    await limit_generate(request, user_id=str(user_id))

    if not settings.GEMINI_API_ENABLED:
        raise HTTPException(status_code=503, detail="Генерация изображений временно недоступна")

    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, detail="Промпт обязателен")

    if len(prompt) > settings.MAX_PROMPT_LEN:
        raise HTTPException(400, detail=f"Промпт слишком длинный. Максимум {settings.MAX_PROMPT_LEN} символов.")

    if resolution not in SUPPORTED_RESOLUTIONS:
        raise HTTPException(400, detail="Некорректное разрешение")

    if aspectRatio not in SUPPORTED_ASPECT_RATIOS:
        raise HTTPException(400, detail="Некорректное соотношение сторон")

    output_format = (outputFormat or "png").strip().lower()
    if output_format == "jpeg":
        output_format = "jpg"
    if output_format not in {"png", "jpg"}:
        raise HTTPException(400, detail="Некорректный формат результата")

    uploads = images or []
    if len(uploads) > settings.MAX_IMAGE_URLS:
        raise HTTPException(400, detail=f"Слишком много изображений. Максимум {settings.MAX_IMAGE_URLS}.")

    # чат: либо используем существующий, либо создаём новый
    if chat_id:
        chat = await session.get(Chat, chat_id)
        if not chat or chat.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Чат не найден")

        # “claim” старых чатов после добавления user_id
        if chat.user_id is None:
            chat.user_id = user_id
            await session.commit()
            await session.refresh(chat)

        if chat.user_id != user_id:
            raise HTTPException(status_code=403, detail="Доступ запрещен")

        if chat.status == "closed":
            raise HTTPException(status_code=400, detail="Чат закрыт")

    else:
        chat = Chat(title=make_chat_title(prompt), user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id

    url_list = imageUrls or []
    image_urls = [u for u in url_list if isinstance(u, str) and u.strip()]
    local_files: list[str] = []
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    refs_subdir = f"{chat_id}/refs"

    # сохраняем загруженные файлы в media/<chat_id>/refs и добавляем их как url
    for up in uploads:
        try:
            name = await save_upload(up, max_bytes=max_bytes, subdir=refs_subdir)
            local_files.append(name)
            image_urls.append(f"{settings.PUBLIC_BASE_URL}/media/{name}")
        except HTTPException as e:
            cleanup_saved_upload_paths(local_files)
            raise HTTPException(status_code=e.status_code, detail=_translate_upload_error(str(e.detail)))
        except Exception as e:
            cleanup_saved_upload_paths(local_files)
            logger.error(f"Error saving upload {up.filename}: {e}")
            raise HTTPException(status_code=500, detail="Не удалось сохранить файл")

    # auto-reference
    last_url = await get_last_success_result_url(session, chat_id)
    image_urls = _prepend_chat_result_reference(
        image_urls=image_urls,
        last_url=last_url,
        limit=settings.MAX_IMAGE_URLS,
    )

    request_id = str(uuid.uuid4())
    task_id = uuid.uuid4().hex

    t = Task(
        task_id=task_id,
        status="waiting_billing",
        prompt=prompt,
        image_urls=json.dumps(image_urls, ensure_ascii=False),
        local_files=json.dumps(local_files, ensure_ascii=False),
        resolution=resolution,
        aspect_ratio=aspectRatio,
        output_format=output_format,
        google_search=googleSearch,
        provider="gemini",
        last_polled_at=None,
        chat_id=chat_id,
        user_id=user_id,
        billing_request_id=request_id,
        billing_state="none",
    )
    session.add(t)
    await session.commit()
    logger.info(
        f"[generate-pro] created task={task_id} status=waiting_billing user={user_id} "
        f"chat={chat_id} images={len(image_urls)} resolution={resolution} aspect={aspectRatio} "
        f"output={output_format} google_search={googleSearch}"
    )

    try:
        logger.info(f"[generate-pro] reserving billing for task={task_id} request_id={request_id}")
        await billing.reserve(user_id=str(user_id), request_id=request_id, cost=1)
        logger.info(f"[generate-pro] billing reserved for task={task_id} request_id={request_id}")
    except BillingNoFunds:
        cleanup_saved_upload_paths(local_files)
        await session.delete(t)
        await session.commit()
        raise HTTPException(status_code=402, detail="Недостаточно запросов")
    except Exception as e:
        cleanup_saved_upload_paths(local_files)
        await session.delete(t)
        await session.commit()
        logger.error(f"[billing] reserve failed: {e}")
        raise HTTPException(status_code=503, detail="Сервис оплаты недоступен")

    t.status = "queued"
    t.billing_state = "reserved"
    logger.info(f"[generate-pro] task={task_id} moved to status=queued billing_state=reserved")

    user_meta = {
        "resolution": resolution,
        "aspectRatio": aspectRatio,
        "imageUrls": image_urls,
        "localFiles": local_files,
        "googleSearch": googleSearch,
        "outputFormat": output_format,
    }
    session.add(
        Message(
            chat_id=chat_id,
            user_id=user_id,
            role="user",
            content=prompt,
            meta_json=json.dumps(user_meta, ensure_ascii=False),
            task_id=task_id,
        )
    )

    await touch_chat(session, chat_id)
    await session.commit()
    logger.info(f"[generate-pro] persisted task={task_id} and user message, enqueueing ARQ job")

    try:
        await enqueue_job_once("process_gemini_task", task_id, _job_id=f"gemini-task:{task_id}")
        logger.info(f"[generate-pro] enqueued ARQ job for task={task_id}")
    except Exception as e:
        logger.error(f"Failed to enqueue Gemini task {task_id}: {e}")
        t.status = "failed"
        t.error_message = "Очередь временно недоступна"
        try:
            await billing.cancel(user_id=str(user_id), request_id=request_id)
            t.billing_state = "refunded"
        except Exception as ce:
            logger.error(f"[billing] cancel failed after queue error: {ce}")
        cleanup_saved_upload_paths(local_files)
        await session.commit()
        raise HTTPException(status_code=503, detail="Очередь временно недоступна")

    return {"taskId": task_id, "chatId": chat_id}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
):
    user_id = user_ctx.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Не авторизован")

    t = await session.get(Task, task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    # “claim” старых задач после добавления user_id
    if t.user_id is None:
        t.user_id = user_id
        await session.commit()
        await session.refresh(t)

    if t.user_id != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    # Используем вынесенную функцию
    return await _get_task_status(t, session)
