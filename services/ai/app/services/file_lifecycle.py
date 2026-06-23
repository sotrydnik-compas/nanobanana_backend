import json
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import logger
from app.models.batch import BatchItem, BatchJob
from app.models.chat import Chat
from app.models.task import Task


def _media_url_prefixes() -> list[str]:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return [
        "/media/",
        f"{base}/media/",
    ]


def get_local_media_rel_path_from_url(url: str | None) -> str | None:
    normalized = (url or "").strip()
    if not normalized:
        return None

    for prefix in _media_url_prefixes():
        if normalized.startswith(prefix):
            rel_path = normalized[len(prefix):].lstrip("/")
            return rel_path or None

    return None


def is_generated_media_rel_path(rel_path: str | None) -> bool:
    normalized = (rel_path or "").strip().lstrip("/")
    if not normalized:
        return False
    if normalized.startswith("generated/"):
        return True
    parts = normalized.split("/")
    return len(parts) >= 2 and parts[1] == "generated"


def _safe_abs_media_path(rel_path: str) -> str | None:
    normalized = (rel_path or "").strip().lstrip("/")
    if not normalized:
        return None

    abs_path = os.path.abspath(os.path.join(settings.MEDIA_DIR, normalized))
    media_root = os.path.abspath(settings.MEDIA_DIR)
    if not abs_path.startswith(f"{media_root}{os.sep}") and abs_path != media_root:
        logger.warning(f"[file-lifecycle] refused to delete path outside media dir: {rel_path}")
        return None
    return abs_path


def _prune_empty_media_dirs(from_abs_path: str) -> None:
    media_root = os.path.abspath(settings.MEDIA_DIR)
    current = os.path.dirname(from_abs_path)

    while current.startswith(media_root) and current != media_root:
        try:
            if os.listdir(current):
                break
            os.rmdir(current)
        except Exception:
            break
        current = os.path.dirname(current)


def delete_media_rel_path(rel_path: str | None) -> bool:
    abs_path = _safe_abs_media_path(rel_path or "")
    if not abs_path:
        return False

    try:
        if os.path.exists(abs_path):
            os.remove(abs_path)
            _prune_empty_media_dirs(abs_path)
            return True
    except Exception as e:
        logger.error(f"[file-lifecycle] failed to delete media file {abs_path}: {e}")
    return False


def delete_media_rel_paths(rel_paths: list[str]) -> int:
    deleted = 0
    for rel_path in rel_paths:
        if delete_media_rel_path(rel_path):
            deleted += 1
    return deleted


def cleanup_saved_upload_paths(rel_paths: list[str]) -> int:
    return delete_media_rel_paths(rel_paths)


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        raw = json.loads(value)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item or "").strip()]


def cleanup_task_local_files(task: Task) -> int:
    files = _parse_json_list(task.local_files)
    deleted = delete_media_rel_paths(files)
    task.local_files = json.dumps([])
    return deleted


def cleanup_local_result_url(url: str | None) -> bool:
    rel_path = get_local_media_rel_path_from_url(url)
    if not rel_path:
        return False
    return delete_media_rel_path(rel_path)


def _remove_local_urls(urls: list[str]) -> tuple[list[str], int]:
    kept: list[str] = []
    deleted = 0
    for url in urls:
        rel_path = get_local_media_rel_path_from_url(url)
        if rel_path:
            if delete_media_rel_path(rel_path):
                deleted += 1
            continue
        kept.append(url)
    return kept, deleted


async def cleanup_batch_common_refs(batch: BatchJob) -> int:
    refs = _parse_json_list(batch.common_refs_json)
    kept_refs, deleted = _remove_local_urls(refs)
    batch.common_refs_json = json.dumps(kept_refs, ensure_ascii=False)
    return deleted


async def cleanup_batch_storage(
    session: AsyncSession,
    batch: BatchJob,
    *,
    include_item_uploads: bool = False,
    include_results: bool = False,
    include_common_refs: bool = True,
) -> int:
    deleted = await cleanup_batch_common_refs(batch) if include_common_refs else 0

    items = (
        await session.execute(select(BatchItem).where(BatchItem.batch_id == batch.id))
    ).scalars().all()

    for item in items:
        if include_item_uploads and item.local_file:
            if delete_media_rel_path(item.local_file):
                deleted += 1
            item.local_file = None

        if include_results and item.result_image_url:
            if cleanup_local_result_url(item.result_image_url):
                deleted += 1
            item.result_image_url = None

    return deleted


async def cleanup_chat_media(session: AsyncSession, chat_id: str) -> int:
    deleted = 0

    tasks = (
        await session.execute(select(Task).where(Task.chat_id == chat_id))
    ).scalars().all()
    for task in tasks:
        if task.result_image_url:
            if cleanup_local_result_url(task.result_image_url):
                deleted += 1
            task.result_image_url = None

        if task.status in ("success", "failed") and task.local_files:
            deleted += cleanup_task_local_files(task)

    batches = (
        await session.execute(select(BatchJob).where(BatchJob.chat_id == chat_id))
    ).scalars().all()
    for batch in batches:
        deleted += await cleanup_batch_storage(
            session,
            batch,
            include_item_uploads=batch.status not in ("processing", "cancelling"),
            include_results=True,
            include_common_refs=batch.status not in ("processing", "cancelling"),
        )

    return deleted


async def cleanup_deleted_chat_media(session: AsyncSession) -> int:
    chats = (
        await session.execute(select(Chat).where(Chat.deleted_at.is_not(None)))
    ).scalars().all()

    deleted = 0
    for chat in chats:
        deleted += await cleanup_chat_media(session, chat.id)
    return deleted


async def cleanup_expired_generated_media(session: AsyncSession, *, retention_days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    tasks = (
        await session.execute(
            select(Task).where(
                Task.updated_at < cutoff,
            )
        )
    ).scalars().all()

    deleted = 0
    for task in tasks:
        if task.result_image_url:
            rel_path = get_local_media_rel_path_from_url(task.result_image_url)
            if is_generated_media_rel_path(rel_path):
                if cleanup_local_result_url(task.result_image_url):
                    deleted += 1
                task.result_image_url = None

                if task.batch_item_id:
                    batch_item = await session.get(BatchItem, task.batch_item_id)
                    if batch_item:
                        batch_item.result_image_url = None

        if task.local_files:
            deleted += cleanup_task_local_files(task)

    return deleted


async def cleanup_stale_pending_batches(session: AsyncSession, *, retention_hours: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    batches = (
        await session.execute(
            select(BatchJob).where(
                BatchJob.status == "pending",
                BatchJob.updated_at < cutoff,
            )
        )
    ).scalars().all()

    deleted = 0
    for batch in batches:
        deleted += await cleanup_batch_storage(
            session,
            batch,
            include_item_uploads=True,
            include_results=False,
            include_common_refs=True,
        )
        batch.status = "cancelled"
        batch.completed_at = datetime.now(timezone.utc)

    return deleted


async def cleanup_chat_deleted_result_if_needed(session: AsyncSession, task: Task) -> bool:
    if not task.chat_id or not task.result_image_url:
        return False

    chat = await session.get(Chat, task.chat_id)
    if not chat or chat.deleted_at is None:
        return False

    deleted = cleanup_local_result_url(task.result_image_url)
    task.result_image_url = None

    if task.batch_item_id:
        batch_item = await session.get(BatchItem, task.batch_item_id)
        if batch_item:
            batch_item.result_image_url = None

    return deleted


async def run_media_cleanup(session: AsyncSession) -> int:
    deleted = 0
    deleted += await cleanup_deleted_chat_media(session)
    deleted += await cleanup_expired_generated_media(
        session,
        retention_days=settings.GENERATED_IMAGE_RETENTION_DAYS,
    )
    deleted += await cleanup_stale_pending_batches(
        session,
        retention_hours=settings.BATCH_PENDING_RETENTION_HOURS,
    )
    return deleted
