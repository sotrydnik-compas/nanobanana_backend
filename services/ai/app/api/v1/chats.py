from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_async_session
from app.models.chat import Chat
from app.models.message import Message

router = APIRouter(tags=["chats"])


@router.post("/chats")
async def create_chat(
    title: str | None = Form(None),
    session: AsyncSession = Depends(get_async_session),
):
    chat = Chat(title=(title or "").strip())
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return {"chat_id": chat.id, "title": chat.title}


@router.get("/chats")
async def list_chats(session: AsyncSession = Depends(get_async_session)):
    q = select(Chat).where(Chat.deleted_at.is_(None)).order_by(Chat.updated_at.desc())
    rows = (await session.execute(q)).scalars().all()
    return {"chats": [{"chat_id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at} for c in rows]}


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: str, session: AsyncSession = Depends(get_async_session)):
    chat = await session.get(Chat, chat_id)
    if not chat or chat.deleted_at is not None:
        raise HTTPException(404, "Chat not found")
    return {"chat_id": chat.id, "title": chat.title, "created_at": chat.created_at, "updated_at": chat.updated_at}


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str, session: AsyncSession = Depends(get_async_session)):
    chat = await session.get(Chat, chat_id)
    if not chat or chat.deleted_at is not None:
        raise HTTPException(404, "Chat not found")

    chat.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    return {"message": "deleted"}


@router.get("/chats/{chat_id}/messages")
async def list_messages(chat_id: str, session: AsyncSession = Depends(get_async_session)):
    chat = await session.get(Chat, chat_id)
    if not chat or chat.deleted_at is not None:
        raise HTTPException(404, "Chat not found")

    q = select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at.asc())
    rows = (await session.execute(q)).scalars().all()
    return {
        "messages": [
            {
                "message_id": m.id,
                "role": m.role,
                "content": m.content,
                "meta_json": m.meta_json,
                "task_id": m.task_id,
                "created_at": m.created_at,
            }
            for m in rows
        ]
    }


@router.post("/chats/{chat_id}/messages")
async def add_message(
    chat_id: str,
    role: str = Form("user"),
    content: str = Form(...),
    meta_json: str | None = Form(None),
    task_id: str | None = Form(None),
    session: AsyncSession = Depends(get_async_session),
):
    chat = await session.get(Chat, chat_id)
    if not chat or chat.deleted_at is not None:
        raise HTTPException(404, "Chat not found")

    msg = Message(
        chat_id=chat_id,
        role=(role or "user").strip(),
        content=(content or "").strip(),
        meta_json=meta_json or "{}",
        task_id=task_id,
    )
    session.add(msg)

    # обновим updated_at чата (простая история “последняя активность”)
    await session.execute(update(Chat).where(Chat.id == chat_id).values(updated_at=datetime.now(timezone.utc)))

    await session.commit()
    await session.refresh(msg)
    return {"message_id": msg.id, "created_at": msg.created_at}
