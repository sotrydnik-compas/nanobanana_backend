from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_async_session
from app.models.task import Task
from app.services.uploads import cleanup_task_files
from app.core.logger import logger

router = APIRouter(tags=["callbacks"])

@router.post("/nanobanana/callback")
async def nanobanana_callback(request: Request, session: AsyncSession = Depends(get_async_session)):
    try:
        payload = await request.json()

        code = payload.get("code")
        data = payload.get("data") or {}
        task_id = data.get("taskId")
        info = (data.get("info") or {})
        result_image_url = info.get("resultImageUrl")

        t = await session.get(Task, task_id) if task_id else None
        if t:
            if code == 200:
                t.status = "success"
                t.result_image_url = result_image_url
                cleanup_task_files(t)
            else:
                t.status = "failed"
                t.error_message = payload.get("msg")
                cleanup_task_files(t)
                logger.error(f"Callback task {task_id} failed with code {code}: {t.error_message}")
            t.updated_at = datetime.now(timezone.utc)
            await session.commit()

        return {"status": "received"}

    except Exception as e:
        logger.exception(f"Unexpected error in callback: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
