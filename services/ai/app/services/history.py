import json
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat
from app.models.message import Message
from app.models.task import Task


async def touch_chat(session: AsyncSession, chat_id: str) -> None:
    await session.execute(
        update(Chat).where(Chat.id == chat_id).values(updated_at=func.now())
    )


async def add_user_message(
    session: AsyncSession,
    chat_id: str,
    task_id: str,
    prompt: str,
    meta: dict,
) -> None:
    session.add(
        Message(
            chat_id=chat_id,
            role="user",
            content=prompt,
            meta_json=json.dumps(meta, ensure_ascii=False),
            task_id=task_id,
        )
    )
    await touch_chat(session, chat_id)


async def upsert_assistant_message(
    session: AsyncSession,
    chat_id: str,
    task_id: str,
    meta: dict,
) -> None:
    q = (
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.task_id == task_id,
            Message.role == "assistant",
        )
        .limit(1)
    )
    m = (await session.execute(q)).scalars().first()

    payload = json.dumps(meta, ensure_ascii=False)

    if m:
        m.meta_json = payload
        if m.content is None:
            m.content = ""
    else:
        session.add(
            Message(
                chat_id=chat_id,
                role="assistant",
                content="",
                meta_json=payload,
                task_id=task_id,
            )
        )
    await touch_chat(session, chat_id)


async def get_last_success_result_url(session: AsyncSession, chat_id: str) -> str | None:
    q = (
        select(Task)
        .where(
            Task.chat_id == chat_id,
            Task.status == "success",
            Task.result_image_url.is_not(None),
        )
        .order_by(Task.updated_at.desc())
        .limit(1)
    )
    t = (await session.execute(q)).scalars().first()
    return t.result_image_url if t else None
