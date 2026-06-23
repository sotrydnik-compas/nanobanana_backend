from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select

from app.api.deps import get_current_user
from app.core.security import verify_password, hash_password, validate_password_strength
from app.database.session import get_async_session
from app.models.user import User
from app.models.refresh_session import RefreshSession
from app.services.codes import generate_code6, store_code, verify_code, in_cooldown, start_cooldown
from app.services.mailer import send_code_email

router = APIRouter(tags=["account"])

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
        raise HTTPException(400, "Неверный текущий пароль")

    is_valid, msg = validate_password_strength(new_password)
    if not is_valid:
        raise HTTPException(400, msg)

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
        raise HTTPException(400, "Некорректный email")

    if not verify_password(password, user.password_hash):
        raise HTTPException(400, "Неверный пароль")

    exists = (await session.execute(select(User.id).where(User.email == new_email_n))).first()
    if exists:
        raise HTTPException(409, "Адрес электронной почты уже используется")

    if await in_cooldown("change", new_email_n):
        return {"status": "ok", "sent": False}

    # код привяжем к новому email (ключ kind=change)
    code = generate_code6()
    await store_code("change", new_email_n, code)
    await start_cooldown("change", new_email_n)
    await send_code_email(new_email_n, "Код подтверждения смены email", code)

    # временно запомним new_email на пользователя в redis через отдельный ключ:
    # чтобы confirm мог понять "какой email менять"
    from app.services.redis_client import get_redis
    r = get_redis()
    await r.set(f"pending_email:{user.id}", new_email_n, ex=900)

    return {"status": "ok", "confirm_required": True}


@router.post("/email/confirm")
async def confirm_email_change(
    code: str = Form(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    from app.services.redis_client import get_redis
    r = get_redis()
    new_email_n = await r.get(f"pending_email:{user.id}")
    if not new_email_n:
        raise HTTPException(400, "Нет ожидающего подтверждения изменения адреса электронной почты")

    ok = await verify_code("change", new_email_n, code)
    if not ok:
        raise HTTPException(400, "Некорректный или просроченный код")

    exists = (await session.execute(select(User.id).where(User.email == new_email_n))).first()
    if exists:
        raise HTTPException(409, "Адрес электронной почты уже используется")

    user.email = new_email_n
    user.email_verified = True

    await r.delete(f"pending_email:{user.id}")

    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
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
