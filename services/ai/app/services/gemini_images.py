import io
import os
import uuid
from urllib.parse import urlparse

import httpx
from PIL import Image

from app.core.config import settings
from app.core.logger import logger


ALLOWED_OUTPUT_FORMATS = {"png", "jpg"}
OUTPUT_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
}
GEMINI_ALLOWED_INPUT_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _media_url_prefixes() -> list[str]:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return [
        "/media/",
        f"{base}/media/",
    ]


def _local_media_path_from_url(url: str) -> str | None:
    normalized = (url or "").strip()
    for prefix in _media_url_prefixes():
        if normalized.startswith(prefix):
            rel_path = normalized[len(prefix):].lstrip("/")
            if rel_path:
                return os.path.join(settings.MEDIA_DIR, rel_path)
    parsed = urlparse(normalized)
    if parsed.path.startswith("/media/"):
        rel_path = parsed.path[len("/media/"):].lstrip("/")
        if rel_path:
            return os.path.join(settings.MEDIA_DIR, rel_path)
    return None


def _normalize_reference_image(data: bytes, mime_type: str | None, source: str) -> dict[str, bytes | str]:
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError("Reference image is too large")

    image = Image.open(io.BytesIO(data))
    detected_mime = image.get_format_mimetype() or mime_type or "image/png"

    if detected_mime in GEMINI_ALLOWED_INPUT_MIME_TYPES:
        return {"data": data, "mime_type": detected_mime}

    logger.info(
        f"[gemini-image] normalizing unsupported reference mime={detected_mime} source={source} to image/png"
    )

    converted = io.BytesIO()
    image = image.convert("RGBA")
    image.save(converted, format="PNG")
    return {"data": converted.getvalue(), "mime_type": "image/png"}


async def load_reference_image(url: str) -> dict[str, bytes | str]:
    local_path = _local_media_path_from_url(url)
    if local_path and os.path.exists(local_path):
        with open(local_path, "rb") as f:
            data = f.read()
        return _normalize_reference_image(data, None, local_path)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.GEMINI_CONNECT_TIMEOUT_SECONDS,
            read=settings.GEMINI_READ_TIMEOUT_SECONDS,
            write=settings.GEMINI_CONNECT_TIMEOUT_SECONDS,
            pool=settings.GEMINI_CONNECT_TIMEOUT_SECONDS,
        ),
        proxy=settings.GEMINI_PROXY_URL,
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
    response.raise_for_status()
    data = response.content
    mime_type = response.headers.get("Content-Type", "").split(";")[0].strip() or None
    return _normalize_reference_image(data, mime_type, url)


def save_generated_image(image_bytes: bytes, mime_type: str, output_format: str) -> str:
    fmt = (output_format or "png").lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in ALLOWED_OUTPUT_FORMATS:
        raise ValueError(f"Unsupported output format: {output_format}")

    os.makedirs(os.path.join(settings.MEDIA_DIR, "generated"), exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{fmt}"
    rel_path = os.path.join("generated", filename).replace("\\", "/")
    abs_path = os.path.join(settings.MEDIA_DIR, rel_path)

    image = Image.open(io.BytesIO(image_bytes))
    if fmt == "jpg":
        image = image.convert("RGB")
        image.save(abs_path, format="JPEG", quality=95)
    else:
        image.save(abs_path, format="PNG")

    return rel_path
