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

from app.clients.nanobanana_client import NanoBananaClient
from app.clients.billing_client import BillingClient, BillingNoFunds


router = APIRouter(tags=["tasks"])

client = NanoBananaClient()
billing = BillingClient(settings.BILLING_BASE_URL, settings.BILLING_INTERNAL_TOKEN)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    # если финал уже был — reconcile и вернуть
    if task.status in ("success", "failed"):
        if task.status == "success":
            await _reconcile_success(task)
        else:
            await _reconcile_failed(task)
        await session.commit()

        return {
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": task.task_id,
                "successFlag": 1 if task.status == "success" else 2,
                "response": {"resultImageUrl": task.result_image_url} if task.result_image_url else None,
                "errorMessage": task.error_message,
            },
        }

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
                    content="",
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
):
    limit_generate(request)

    user_id = user_ctx.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, detail="Prompt is required")

    if len(prompt) > settings.MAX_PROMPT_LEN:
        raise HTTPException(400, detail="Prompt too long")

    if resolution not in ("1K", "2K", "4K"):
        raise HTTPException(400, detail="Invalid resolution")

    allowed_ar = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "auto"}
    if aspectRatio not in allowed_ar:
        raise HTTPException(400, detail="Invalid aspectRatio")

    uploads = images or []
    if len(uploads) > settings.MAX_IMAGE_URLS:
        raise HTTPException(400, detail="Too many images")

    url_list = imageUrls or []
    image_urls = [u for u in url_list if isinstance(u, str) and u.strip()]
    local_files: list[str] = []
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024

    # сохраняем загруженные файлы в media и добавляем их как url
    for up in uploads:
        try:
            name = await save_upload(up, max_bytes=max_bytes)
            local_files.append(name)
            image_urls.append(f"{settings.PUBLIC_BASE_URL}/media/{name}")
        except Exception as e:
            logger.error(f"Error saving upload {up.filename}: {e}")
            raise

    # чат: либо используем существующий, либо создаём новый
    if chat_id:
        chat = await session.get(Chat, chat_id)
        if not chat or chat.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Chat not found")

        # “claim” старых чатов после добавления user_id
        if chat.user_id is None:
            chat.user_id = user_id
            await session.commit()
            await session.refresh(chat)

        if chat.user_id != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        if chat.status == "closed":
            raise HTTPException(status_code=400, detail="Chat is closed")

        # auto-reference
        last_url = await get_last_success_result_url(session, chat_id)
        image_urls = _prepend_chat_result_reference(
            image_urls=image_urls,
            last_url=last_url,
            limit=settings.MAX_IMAGE_URLS,
        )

    else:
        chat = Chat(title=make_chat_title(prompt), user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id

    # BILLING: reserve -> (cancel|confirm)
    request_id = str(uuid.uuid4())

    try:
        await billing.reserve(user_id=str(user_id), request_id=request_id, cost=1)
    except BillingNoFunds:
        raise HTTPException(status_code=402, detail="Not enough requests")
    except Exception as e:
        logger.error(f"[billing] reserve failed: {e}")
        raise HTTPException(status_code=503, detail="Billing unavailable")

    data = {
        "prompt": prompt,
        "imageUrls": image_urls,
        "resolution": resolution,
        "aspectRatio": aspectRatio,
        "callBackUrl": f"{settings.PUBLIC_BASE_URL}/api/v1/ai/nanobanana/callback",
    }

    # зовём nanobanana (в thread)
    try:
        res = await anyio.to_thread.run_sync(client.generate_pro, data)
    except Exception as e:
        # обязательный cancel, потому что task_id не получен
        try:
            await billing.cancel(user_id=str(user_id), request_id=request_id)
        except Exception as ce:
            logger.error(f"[billing] cancel failed after nanobanana exception: {ce}")
        raise

    # если nanobanana вернул ошибку ДО task_id -> обязательный cancel
    if res.get("code") != 200 or not (res.get("data") or {}).get("taskId"):
        try:
            await billing.cancel(user_id=str(user_id), request_id=request_id)
        except Exception as ce:
            logger.error(f"[billing] cancel failed after nanobanana error: {ce}")

        logger.error(f"NanoBanana generate_pro error: {res.get('msg')}")
        raise HTTPException(status_code=502, detail=res.get("msg", "NanoBanana error"))

    task_id = res["data"]["taskId"]

    # confirm: если confirm упадёт — не валим запрос пользователю, дотянем reconcile позже
    billing_state = "confirmed"
    try:
        await billing.confirm(user_id=str(user_id), request_id=request_id, task_id=task_id)
    except Exception as e:
        billing_state = "confirm_pending"
        logger.error(f"[billing] confirm failed (request_id={request_id}, task_id={task_id}): {e}")

    # сохраняем task + user-message
    t = Task(
        task_id=task_id,
        status="running",
        prompt=prompt,
        image_urls=json.dumps(image_urls, ensure_ascii=False),
        local_files=json.dumps(local_files, ensure_ascii=False),
        resolution=resolution,
        aspect_ratio=aspectRatio,
        last_polled_at=None,
        chat_id=chat_id,
        user_id=user_id,
        billing_request_id=request_id,
        billing_state=billing_state,
    )
    await session.merge(t)

    user_meta = {
        "resolution": resolution,
        "aspectRatio": aspectRatio,
        "imageUrls": image_urls,
        "localFiles": local_files,
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

    return {"taskId": task_id, "chatId": chat_id}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
):
    user_id = user_ctx.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    t = await session.get(Task, task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    # “claim” старых задач после добавления user_id
    if t.user_id is None:
        t.user_id = user_id
        await session.commit()
        await session.refresh(t)

    if t.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Используем вынесенную функцию
    return await _get_task_status(t, session)
