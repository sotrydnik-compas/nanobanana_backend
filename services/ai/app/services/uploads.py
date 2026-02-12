import os
import json
import uuid
from typing import Optional
from fastapi import UploadFile, HTTPException
from app.core.config import settings
from app.models.task import Task

ALLOWED_CT = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

def guess_ext(upload: UploadFile) -> str:
    name = (upload.filename or "").lower()
    _, ext = os.path.splitext(name)
    if ext in ALLOWED_EXT:
        return ext
    if upload.content_type == "image/jpeg":
        return ".jpg"
    if upload.content_type == "image/png":
        return ".png"
    if upload.content_type == "image/webp":
        return ".webp"
    return ".jpg"

async def save_upload(upload: UploadFile, max_bytes: int) -> str:
    if upload.content_type not in ALLOWED_CT:
        raise HTTPException(400, detail=f"Unsupported content-type: {upload.content_type}")

    os.makedirs(settings.MEDIA_DIR, exist_ok=True)

    ext = guess_ext(upload)
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(settings.MEDIA_DIR, filename)

    total = 0
    with open(path, "wb") as f:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                f.close()
                try:
                    os.remove(path)
                except Exception:
                    pass
                raise HTTPException(413, detail="File too large")
            f.write(chunk)

    return filename

def cleanup_task_files(t: Optional[Task]):
    if not t or not t.local_files:
        return
    try:
        files = json.loads(t.local_files)
    except Exception:
        files = []
    for name in files:
        path = os.path.join(settings.MEDIA_DIR, name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    t.local_files = json.dumps([])
