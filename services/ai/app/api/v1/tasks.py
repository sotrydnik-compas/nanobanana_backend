import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import anyio
from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import logger
from app.core.rate_limit import limit_generate
from app.database.session import get_async_session
from app.models.task import Task
from app.services.uploads import save_upload, cleanup_task_files
from app.clients.nanobanana_client import NanoBananaClient

router = APIRouter(tags=["tasks"])
client = NanoBananaClient()

@router.post("/generate-pro")
@router.post("/tasks")  # новый основной путь, но оставили совместимость
async def generate_pro(
    request: Request,
    session: AsyncSession = Depends(get_async_session),

    prompt: str = Form(...),
    resolution: str = Form("1K"),
    aspectRatio: str = Form("1:1"),

    imageUrls: Optional[List[str]] = Form(default=None),
    images: Optional[List[UploadFile]] = File(default=None),
):
    limit_generate(request)

    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, detail="Prompt is required")
    if len(prompt) > settings.MAX_PROMPT_LEN:
        raise HTTPException(400, detail="Prompt too long")
    if resolution not in ("1K", "2K", "4K"):
        raise HTTPException(400, detail="Invalid resolution")

    allowed_ar = {"1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9","auto"}
    if aspectRatio not in allowed_ar:
        raise HTTPException(400, detail="Invalid aspectRatio")

    uploads = images or []
    if len(uploads) > settings.MAX_IMAGE_URLS:
        raise HTTPException(400, detail="Too many images")

    url_list = imageUrls or []
    image_urls = [u for u in url_list if isinstance(u, str) and u.strip()]
    local_files: list[str] = []
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024

    for up in uploads:
        try:
            name = await save_upload(up, max_bytes=max_bytes)
            local_files.append(name)
            image_urls.append(f"{settings.PUBLIC_BASE_URL}/media/{name}")
        except Exception as e:
            logger.error(f"Error saving upload {up.filename}: {e}")
            raise

    data = {
        "prompt": prompt,
        "imageUrls": image_urls,
        "resolution": resolution,
        "aspectRatio": aspectRatio,
        "callBackUrl": f"{settings.PUBLIC_BASE_URL}/api/v1/ai/nanobanana/callback",
    }

    # requests() блокирующий → уводим в thread
    res = await anyio.to_thread.run_sync(client.generate_pro, data)
    if res.get("code") != 200:
        logger.error(f"NanoBanana generate_pro error: {res.get('msg')}")
        raise HTTPException(status_code=502, detail=res.get("msg", "NanoBanana error"))

    task_id = res["data"]["taskId"]

    t = Task(
        task_id=task_id,
        status="running",
        prompt=prompt,
        image_urls=json.dumps(image_urls, ensure_ascii=False),
        local_files=json.dumps(local_files, ensure_ascii=False),
        resolution=resolution,
        aspect_ratio=aspectRatio,
        last_polled_at=None,
    )

    await session.merge(t)
    await session.commit()

    return {"taskId": task_id}

@router.get("/tasks/{task_id}")
async def get_task(task_id: str, session: AsyncSession = Depends(get_async_session)):
    t = await session.get(Task, task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    if t.status in ("success", "failed"):
        return {
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": t.task_id,
                "successFlag": 1 if t.status == "success" else 2,
                "response": {"resultImageUrl": t.result_image_url} if t.result_image_url else None,
                "errorMessage": t.error_message,
            },
        }

    now = datetime.now(timezone.utc)

    last = t.last_polled_at
    should_poll = last is None or (now - last) >= timedelta(seconds=settings.POLL_INTERVAL_SECONDS)
    if not should_poll:
        return {
            "code": 200,
            "msg": "success",
            "data": {"taskId": t.task_id, "successFlag": 0, "response": None, "errorMessage": None},
        }

    res = await anyio.to_thread.run_sync(client.record_info, task_id)
    if res.get("code") != 200:
        logger.warning(f"NanoBanana record_info failed for task {task_id}")
        t.last_polled_at = now
        await session.commit()
        return {
            "code": 200,
            "msg": "success",
            "data": {"taskId": t.task_id, "successFlag": 0, "response": None, "errorMessage": None},
        }

    data = res.get("data") or {}
    success_flag = data.get("successFlag", 0)
    response = data.get("response")

    t.last_polled_at = now

    if success_flag == 1 and response:
        t.status = "success"
        t.result_image_url = (response or {}).get("resultImageUrl")
        cleanup_task_files(t)
    elif success_flag in (2, 3):
        t.status = "failed"
        t.error_message = data.get("errorMessage")
        cleanup_task_files(t)
        logger.error(f"Task {t.task_id} failed: {t.error_message}")

    await session.commit()
    return res
