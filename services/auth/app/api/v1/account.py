from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select

from app.api.deps import get_current_user
from app.core.security import verify_password, hash_password
from app.database.session import get_async_session
from app.models.user import User
from app.models.refresh_session import RefreshSession
from app.services.one_time_tokens import new_token, store_email_change, pop_email_change
from app.services.mailer import send_change_email

router = APIRouter()

def _now():
    return datetime.now(timezone.utc)

@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "role": user.role}

@router.post("/password/change")
async def change_password(
    old_password: str = Form(...),
    new_password: str = Form(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    if not verify_password(old_password, user.password_hash):
        raise HTTPException(400, "Invalid old password")
    if not new_password or len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 chars")

    user.password_hash = hash_password(new_password)

    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )

    await session.commit()
    return {"status": "ok"}

@router.post("/email/change")
async def change_email(
    new_email: str = Form(...),
    password: str = Form(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    new_email_n = (new_email or "").strip().lower()
    if not new_email_n or "@" not in new_email_n:
        raise HTTPException(400, "Invalid email")

    if not verify_password(password, user.password_hash):
        raise HTTPException(400, "Invalid password")

    # проверим что email не занят
    q = select(User.id).where(User.email == new_email_n)
    exists = (await session.execute(q)).first()
    if exists:
        raise HTTPException(409, "Email already in use")

    token = new_token()
    await store_email_change(token, user.id, new_email_n)
    send_change_email(new_email_n, token)

    return {"status": "ok", "confirm_required": True}


@router.post("/email/confirm")
async def confirm_email_change(
    token: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    data = await pop_email_change(token)
    if not data:
        raise HTTPException(400, "Invalid or expired token")

    user_id, new_email = data
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")

    # повторно убедимся что новый email не занят
    q = select(User.id).where(User.email == new_email)
    exists = (await session.execute(q)).first()
    if exists:
        raise HTTPException(409, "Email already in use")

    u.email = new_email
    u.email_verified = True

    # по желанию: ревокнуть все refresh-сессии, чтобы перелогинились везде
    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == u.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )

    await session.commit()
    return {"status": "ok"}


@router.post("/sessions/logout-all")
async def logout_all_devices(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await session.commit()
    return {"status": "ok"}
