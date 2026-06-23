from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_async_session
from app.api.deps import get_current_user

from app.models.chat import Chat
from app.models.message import Message
from app.services.history import touch_chat
from app.services.file_lifecycle import cleanup_chat_media

router = APIRouter(tags=["chats"])


async def _get_chat_owned(session: AsyncSession, chat_id: str, user_id: str) -> Chat:
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

    return chat


@router.get("/chats")
async def list_chats(
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0,
):
    user_id = user_ctx.get("user_id")

    q = (
        select(Chat)
        .where(Chat.user_id == user_id, Chat.deleted_at.is_(None))
        .order_by(desc(Chat.updated_at))
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(q)).scalars().all()

    return {
        "chats": [
            {
                "chatId": c.id,
                "title": c.title,
                "status": c.status,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
            }
            for c in rows
        ]
    }


@router.get("/chats/{chat_id}")
async def get_chat(
    chat_id: str,
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
):
    user_id = user_ctx.get("user_id")
    c = await _get_chat_owned(session, chat_id, user_id)

    return {
        "chatId": c.id,
        "title": c.title,
        "status": c.status,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


@router.get("/chats/{chat_id}/messages")
async def get_messages(
    chat_id: str,
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
):
    user_id = user_ctx.get("user_id")
    await _get_chat_owned(session, chat_id, user_id)

    q = (
        select(Message)
        .where(Message.chat_id == chat_id, Message.user_id == user_id)
        .order_by(Message.created_at.asc())
    )
    msgs = (await session.execute(q)).scalars().all()

    return {
        "messages": [
            {
                "messageId": m.id,
                "role": m.role,
                "content": m.content,
                "meta_json": m.meta_json,
                "taskId": m.task_id,
                "created_at": m.created_at,
            }
            for m in msgs
        ]
    }


@router.post("/chats/{chat_id}/close")
async def close_chat(
    chat_id: str,
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
):
    user_id = user_ctx.get("user_id")
    c = await _get_chat_owned(session, chat_id, user_id)

    if c.status != "closed":
        c.status = "closed"
        await touch_chat(session, chat_id)
        await session.commit()

    return {"status": "ok"}


@router.delete("/chats/{chat_id}")
async def delete_chat(
    chat_id: str,
    session: AsyncSession = Depends(get_async_session),
    user_ctx: dict = Depends(get_current_user),
):
    user_id = user_ctx.get("user_id")
    c = await _get_chat_owned(session, chat_id, user_id)

    if c.deleted_at is None:
        from datetime import datetime, timezone
        c.deleted_at = datetime.now(timezone.utc)

        await touch_chat(session, chat_id)
        deleted_files = await cleanup_chat_media(session, chat_id)
        await session.commit()
        return {"status": "ok", "deleted_files": deleted_files}

    return {"status": "ok", "deleted_files": 0}
