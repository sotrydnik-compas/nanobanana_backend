from datetime import datetime, timezone
from fastapi import APIRouter, Form, HTTPException, Header, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    safe_decode_token,
)
from app.database.session import get_async_session
from app.models.user import User
from app.models.refresh_session import RefreshSession
from app.services.blacklist import blacklist_jti
from app.services.one_time_tokens import (
    new_token,
    store_email_verify,
    pop_email_verify,
    store_password_reset,
    pop_password_reset,
)
from app.services.mailer import send_verify_email, send_reset_email

router = APIRouter()


def _now() -> datetime:
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

    q = select(User.id).where(User.email == email_n)
    exists = (await session.execute(q)).first()
    if exists:
        raise HTTPException(409, "Email already registered")

    u = User(email=email_n, password_hash=hash_password(password), role="user", email_verified=False)
    session.add(u)
    await session.commit()
    await session.refresh(u)

    token = new_token()
    await store_email_verify(token, u.id)
    send_verify_email(u.email, token)

    return {"status": "ok", "verify_required": True}


@router.post("/email/resend")
async def resend_verify(
    email: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    email_n = (email or "").strip().lower()
    q = select(User).where(User.email == email_n)
    u = (await session.execute(q)).scalars().first()

    # всегда ok (не палим существование), но если юзер есть и не verified — шлём
    if u and not u.email_verified:
        token = new_token()
        await store_email_verify(token, u.id)
        send_verify_email(u.email, token)

    return {"status": "ok"}


@router.post("/email/verify")
async def verify_email(
    token: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    user_id = await pop_email_verify(token)
    if not user_id:
        raise HTTPException(400, "Invalid or expired token")

    u = await session.get(User, user_id)
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
    q = select(User).where(User.email == email_n)
    u = (await session.execute(q)).scalars().first()

    if not u or not verify_password(password, u.password_hash):
        raise HTTPException(401, "Invalid credentials")
    if not u.is_active:
        raise HTTPException(403, "User inactive")
    if not u.email_verified:
        raise HTTPException(403, "Email not verified")

    access = create_access_token(u.id, u.email, u.role)
    refresh = create_refresh_token(u.id)

    rs = RefreshSession(user_id=u.id, jti=refresh["jti"], expires_at=refresh["exp"], revoked_at=None)
    session.add(rs)
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

    q = select(RefreshSession).where(RefreshSession.jti == jti)
    rs = (await session.execute(q)).scalars().first()
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
    # revoke refresh session (если токен валиден)
    payload, err = safe_decode_token(refresh_token)
    if not err and payload and payload.get("typ") == "refresh":
        jti = payload.get("jti")
        if jti:
            q = select(RefreshSession).where(RefreshSession.jti == jti)
            rs = (await session.execute(q)).scalars().first()
            if rs and rs.revoked_at is None:
                rs.revoked_at = _now()

    # blacklist access token if provided
    if authorization and authorization.startswith("Bearer "):
        access_token = authorization.split(" ", 1)[1].strip()
        p2, err2 = safe_decode_token(access_token)
        if not err2 and p2 and p2.get("typ") == "access":
            jti2 = p2.get("jti")
            exp2 = p2.get("exp")
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
    q = select(User).where(User.email == email_n)
    u = (await session.execute(q)).scalars().first()

    # always ok (no enumeration)
    if u and u.is_active:
        token = new_token()
        await store_password_reset(token, u.id)
        send_reset_email(u.email, token)

    return {"status": "ok"}


@router.post("/password/reset/confirm")
async def reset_confirm(
    token: str = Form(...),
    new_password: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    if not new_password or len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 chars")

    user_id = await pop_password_reset(token)
    if not user_id:
        raise HTTPException(400, "Invalid or expired token")

    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")

    u.password_hash = hash_password(new_password)

    # revoke all refresh sessions
    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == u.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )

    await session.commit()
    return {"status": "ok"}
