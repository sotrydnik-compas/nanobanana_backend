import json
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_async_session
from app.models.task import Task
from app.models.message import Message
from app.models.chat import Chat
from app.models.batch import BatchItem
from app.services.uploads import cleanup_task_files
from app.services.history import touch_chat
from app.core.logger import logger

from app.clients.billing_client import BillingClient

router = APIRouter(tags=["callbacks"])

billing = BillingClient(settings.BILLING_BASE_URL, settings.BILLING_INTERNAL_TOKEN)


@router.post("/nanobanana/callback")
async def nanobanana_callback(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
):
    try:
        payload = await request.json()

        code = payload.get("code")
        data = payload.get("data") or {}
        task_id = data.get("taskId")
        response = (data.get("response") or {})
        result_image_url = response.get("resultImageUrl")
        # info = (data.get("info") or {})
        # result_image_url = info.get("resultImageUrl")

        t = await session.get(Task, task_id) if task_id else None
        if not t:
            return {"status": "received"}

        # Существующая логика для обычных задач
        if code == 200:
            t.status = "success"
            t.result_image_url = result_image_url
            cleanup_task_files(t)

            # reconcile confirm
            if t.billing_request_id and t.billing_state != "confirmed":
                try:
                    await billing.confirm(user_id=str(t.user_id), request_id=t.billing_request_id, task_id=t.task_id)
                    t.billing_state = "confirmed"
                except Exception as e:
                    t.billing_state = "confirm_pending"
                    logger.error(f"[billing] callback confirm failed task={t.task_id}: {e}")

        else:
            t.status = "failed"
            t.error_message = payload.get("msg")
            cleanup_task_files(t)
            logger.error(f"Callback task {task_id} failed with code {code}: {t.error_message}")

            # refund
            if t.billing_request_id and t.billing_state != "refunded":
                try:
                    await billing.fail(
                        user_id=str(t.user_id),
                        request_id=t.billing_request_id,
                        task_id=t.task_id,
                        error=t.error_message,
                    )
                    t.billing_state = "refunded"
                except Exception as e:
                    logger.error(f"[billing] callback refund failed task={t.task_id}: {e}")

        # Обновляем чат
        if t.chat_id:
            chat = await session.get(Chat, t.chat_id)
            if chat and chat.user_id is None and t.user_id:
                chat.user_id = t.user_id

            exists_q = select(Message.id).where(
                Message.chat_id == t.chat_id,
                Message.task_id == t.task_id,
                Message.role == "assistant",
            )
            exists = (await session.execute(exists_q)).first()

            if not exists:
                meta = {
                    "successFlag": 1 if t.status == "success" else 2,
                    "resultImageUrl": t.result_image_url,
                    "errorMessage": t.error_message,
                }
                session.add(
                    Message(
                        chat_id=t.chat_id,
                        user_id=t.user_id,
                        role="assistant",
                        content="",
                        meta_json=json.dumps(meta, ensure_ascii=False),
                        task_id=t.task_id,
                    )
                )
                await touch_chat(session, t.chat_id)

        # Если задача связана с BatchItem, обновляем его
        if t.batch_item_id:
            batch_item = await session.get(BatchItem, t.batch_item_id)
            if batch_item:
                if t.status == "success":
                    batch_item.status = "success"
                    batch_item.result_image_url = t.result_image_url
                else:
                    batch_item.status = "failed"
                    batch_item.error_message = t.error_message

                batch_item.completed_at = datetime.now(timezone.utc)

                # Здесь НЕ запускаем следующий элемент - это делает ARQ worker
                logger.info(f"Batch item {batch_item.id} updated via callback")

        await session.commit()
        return {"status": "received"}

    except Exception as e:
        logger.exception(f"Unexpected error in callback: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
