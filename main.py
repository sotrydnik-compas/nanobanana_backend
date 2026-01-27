import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import FastAPI, Depends, Request, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from core.config import settings
from core.middleware import AllowIframeMiddleware
from core.services import save_upload, cleanup_task_files
from db import Base, engine, SessionLocal
from models import Task
from schemas import GenerateRequest, GenerateResponse
from nanobanana_client import NanoBananaClient
from rate_limit import limit_generate
from core.logger import logger


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Public NanoBanana Backend")

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AllowIframeMiddleware)

os.makedirs("media", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")

# FRONT_DIST = "/home/compas/VSCodeProjects/nanobanana/nanobanana/dist"
# app.mount("/widget", StaticFiles(directory=FRONT_DIST, html=True), name="widget")



def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

client = NanoBananaClient()

@app.post(f"{settings.API_V1_PREFIX}/generate-pro", response_model=GenerateResponse)
async def generate_pro(
    request: Request,
    db: Session = Depends(get_db),

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
    local_files = []
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024

    for up in uploads:
        try:
            name = await save_upload(up, max_bytes=max_bytes)
            local_files.append(name)
            image_urls.append(f"{settings.PUBLIC_BASE_URL}/media/{name}")
        except Exception as e:
            logger.error(f"Error saving upload {up.filename}: {e}")
            raise e


    data = {
        "prompt": prompt,
        "imageUrls": image_urls,
        "resolution": resolution,
        "aspectRatio": aspectRatio,
        "callBackUrl": f"{settings.PUBLIC_BASE_URL}{settings.API_V1_PREFIX}/nanobanana/callback",
    }

    res = client.generate_pro(data)
    if res.get("code") != 200:
        logger.error(f"NanoBanana generate_pro error: {res.get('msg')}")
        raise HTTPException(status_code=502, detail=res.get("msg", "NanoBanana error"))

    task_id = res["data"]["taskId"]

    # сохраним задачу
    t = Task(
        task_id=task_id,
        status="running",
        prompt=prompt,
        image_urls=json.dumps(image_urls, ensure_ascii=False),
        local_files=json.dumps(local_files, ensure_ascii=False),
        resolution=resolution,
        aspect_ratio=aspectRatio,
        updated_at=datetime.now(timezone.utc),
    )
    db.merge(t)
    db.commit()

    return GenerateResponse(taskId=task_id)

@app.get(f"{settings.API_V1_PREFIX}/tasks/{{task_id}}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    try:
        t: Task | None = db.get(Task, task_id)
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
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)

        should_poll = (
                last is None
                or (now - last) >= timedelta(seconds=settings.POLL_INTERVAL_SECONDS)
        )

        if not should_poll:
            return {
                "code": 200,
                "msg": "success",
                "data": {
                    "taskId": t.task_id,
                    "successFlag": 0,
                    "response": None,
                    "errorMessage": None,
                },
            }

        res = client.record_info(task_id)
        if res.get("code") != 200:
            logger.warning(f"NanoBanana record_info failed for task {task_id}")
            return {
                "code": 200,
                "msg": "success",
                "data": {
                    "taskId": t.task_id,
                    "successFlag": 0,
                    "response": None,
                    "errorMessage": None,
                },
            }

        data = res.get("data") or {}
        success_flag = data.get("successFlag", 0)
        response = data.get("response")

        t.last_polled_at = now
        t.updated_at = now

        if success_flag == 1 and response:
            t.status = "success"
            t.result_image_url = (response or {}).get("resultImageUrl")
            cleanup_task_files(t)
        elif success_flag in (2, 3):
            t.status = "failed"
            t.error_message = data.get("errorMessage")
            cleanup_task_files(t)
            logger.error(f"Task {t.task_id} failed: {t.error_message}")
            logger.error(f"Task {t.task_id} failed: {t.error_message}")

        db.commit()

        return res

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error in get_task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post(f"{settings.API_V1_PREFIX}/nanobanana/callback")
async def nanobanana_callback(request: Request, db: Session = Depends(get_db)):
    try:
        # ВАЖНО: ответить быстро (в идеале без скачивания файлов здесь)
        payload = await request.json()

        code = payload.get("code")
        data = payload.get("data") or {}
        task_id = data.get("taskId")
        info = (data.get("info") or {})
        result_image_url = info.get("resultImageUrl")

        t = db.get(Task, task_id) if task_id else None
        if t:
            t.updated_at = datetime.now(timezone.utc)
            if code == 200:
                t.status = "success"
                t.result_image_url = result_image_url
                cleanup_task_files(t)
            else:
                t.status = "failed"
                t.error_message = payload.get("msg")
                cleanup_task_files(t)
                logger.error(f"Callback task {task_id} failed with code {code}: {t.error_message}")
            db.commit()

        return {"status": "received"}

    except Exception as e:
        logger.exception(f"Unexpected error in callback: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")