from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.database.session import get_async_session
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    safe_decode_token,
)
from app.models.user import User
from app.models.refresh_session import RefreshSession
from app.services.blacklist import blacklist_jti
from app.services.codes import (
    generate_code6,
    store_code,
    verify_code,
    in_cooldown,
    start_cooldown,
)
from app.services.mailer import send_code_email

router = APIRouter(tags=["auth"])


def _now():
    return datetime.now(timezone.utc)


@router.post("/register")
async def register(
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    email_n = (email or "").strip().lower()
    if not email_n or "@" not in email_n:
        raise HTTPException(400, "Invalid email")
    if not password or len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 chars")

    exists = (await session.execute(select(User.id).where(User.email == email_n))).first()
    if exists:
        raise HTTPException(409, "Email already registered")

    u = User(email=email_n, password_hash=hash_password(password), role="user", email_verified=False)
    session.add(u)
    await session.commit()
    await session.refresh(u)

    code = generate_code6()
    await store_code("verify", email_n, code)
    await start_cooldown("verify", email_n)
    await send_code_email(email_n, "Код подтверждения регистрации", code)

    return {"status": "ok", "verify_required": True}


@router.post("/email/resend")
async def resend_verify(
    email: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    email_n = (email or "").strip().lower()

    # cooldown — возвращаем ok, но не шлём повторно
    if await in_cooldown("verify", email_n):
        return {"status": "ok", "sent": False}

    u = (await session.execute(select(User).where(User.email == email_n))).scalars().first()
    # anti-enum: всегда ok
    if u and not u.email_verified:
        code = generate_code6()
        await store_code("verify", email_n, code)
        await start_cooldown("verify", email_n)
        await send_code_email(email_n, "Код подтверждения email", code)

    return {"status": "ok", "sent": True}


@router.post("/email/verify")
async def verify_email(
    email: str = Form(...),
    code: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    email_n = (email or "").strip().lower()
    if not email_n or "@" not in email_n:
        raise HTTPException(400, "Invalid email")
    if not code or len(code) != 6:
        raise HTTPException(400, "Invalid code")

    ok = await verify_code("verify", email_n, code)
    if not ok:
        raise HTTPException(400, "Invalid or expired code")

    u = (await session.execute(select(User).where(User.email == email_n))).scalars().first()
    if not u:
        raise HTTPException(404, "User not found")

    u.email_verified = True
    await session.commit()
    return {"status": "ok"}


@router.post("/login")
async def login(
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    email_n = (email or "").strip().lower()
    u = (await session.execute(select(User).where(User.email == email_n))).scalars().first()

    if not u or not verify_password(password, u.password_hash):
        raise HTTPException(401, "Invalid credentials")
    if not u.is_active:
        raise HTTPException(403, "User inactive")
    if not u.email_verified:
        raise HTTPException(403, "Email not verified")

    access = create_access_token(u.id, u.email, u.role)
    refresh = create_refresh_token(u.id)

    session.add(RefreshSession(user_id=u.id, jti=refresh["jti"], expires_at=refresh["exp"], revoked_at=None))
    await session.commit()

    return {
        "access_token": access["token"],
        "refresh_token": refresh["token"],
        "token_type": "bearer",
        "expires_in": int((access["exp"] - _now()).total_seconds()),
    }


@router.post("/refresh")
async def refresh(
    refresh_token: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    payload, err = safe_decode_token(refresh_token)
    if err or not payload or payload.get("typ") != "refresh":
        raise HTTPException(401, "Invalid refresh token")

    user_id = payload.get("sub")
    jti = payload.get("jti")
    if not user_id or not jti:
        raise HTTPException(401, "Invalid refresh token")

    rs = (await session.execute(select(RefreshSession).where(RefreshSession.jti == jti))).scalars().first()
    if not rs or rs.revoked_at is not None:
        raise HTTPException(401, "Refresh token revoked")

    rs.revoked_at = _now()

    u = await session.get(User, user_id)
    if not u or not u.is_active:
        raise HTTPException(401, "User not found")
    if not u.email_verified:
        raise HTTPException(403, "Email not verified")

    access = create_access_token(u.id, u.email, u.role)
    new_refresh = create_refresh_token(u.id)
    session.add(RefreshSession(user_id=u.id, jti=new_refresh["jti"], expires_at=new_refresh["exp"], revoked_at=None))
    await session.commit()

    return {
        "access_token": access["token"],
        "refresh_token": new_refresh["token"],
        "token_type": "bearer",
        "expires_in": int((access["exp"] - _now()).total_seconds()),
    }


@router.post("/logout")
async def logout(
    refresh_token: str = Form(...),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_async_session),
):
    # 1) revoke refresh (если валиден)
    payload, err = safe_decode_token(refresh_token)
    if not err and payload and payload.get("typ") == "refresh":
        jti = payload.get("jti")
        if jti:
            rs = (
                await session.execute(select(RefreshSession).where(RefreshSession.jti == jti))
            ).scalars().first()
            if rs and rs.revoked_at is None:
                rs.revoked_at = _now()

    # 2) blacklist access (если передан Authorization)
    if authorization and authorization.startswith("Bearer "):
        access_token = authorization.split(" ", 1)[1].strip()
        p2, err2 = safe_decode_token(access_token)
        if not err2 and p2 and p2.get("typ") == "access":
            jti2 = p2.get("jti")
            exp2 = p2.get("exp")
            # exp2 в JWT обычно unix timestamp (int)
            if jti2 and exp2:
                await blacklist_jti(jti2, int(exp2))

    await session.commit()
    return {"status": "ok"}


@router.post("/password/reset/request")
async def reset_request(
    email: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    email_n = (email or "").strip().lower()

    if await in_cooldown("reset", email_n):
        return {"status": "ok", "sent": False}

    u = (await session.execute(select(User).where(User.email == email_n))).scalars().first()
    # anti-enum: всегда ok
    if u and u.is_active:
        code = generate_code6()
        await store_code("reset", email_n, code)
        await start_cooldown("reset", email_n)
        await send_code_email(email_n, "Код для сброса пароля", code)

    return {"status": "ok", "sent": True}


@router.post("/password/reset/confirm")
async def reset_confirm(
    email: str = Form(...),
    code: str = Form(...),
    new_password: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    email_n = (email or "").strip().lower()
    if not new_password or len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 chars")

    ok = await verify_code("reset", email_n, code)
    if not ok:
        raise HTTPException(400, "Invalid or expired code")

    u = (await session.execute(select(User).where(User.email == email_n))).scalars().first()
    if not u:
        raise HTTPException(404, "User not found")

    u.password_hash = hash_password(new_password)

    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == u.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await session.commit()
    return {"status": "ok"}
