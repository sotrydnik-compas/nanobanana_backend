import os
from urllib.parse import urlparse
import anyio
import requests

from app.core.config import settings


def _download_sync(url: str, path: str) -> None:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)


def _guess_ext(url: str) -> str:
    p = urlparse(url)
    _, ext = os.path.splitext(p.path)
    return ext if ext else ".jpg"


async def store_result_if_enabled(url: str | None, chat_id: str, task_id: str) -> str | None:
    if not url:
        return None
    if not settings.STORE_RESULTS:
        return url
    if not url.startswith("http"):
        return url

    ext = _guess_ext(url)

    # media/chats/<chat_id>/<task_id>/result.<ext>
    rel_dir = os.path.join("chats", chat_id, task_id)
    abs_dir = os.path.join(settings.MEDIA_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    filename = f"result{ext}"
    abs_path = os.path.join(abs_dir, filename)

    await anyio.to_thread.run_sync(_download_sync, url, abs_path)

    rel_url = f"{settings.PUBLIC_BASE_URL}/media/{rel_dir}/{filename}"
    return rel_url
