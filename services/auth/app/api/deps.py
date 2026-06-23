from fastapi import Header, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import safe_decode_token
from app.database.session import get_async_session
from app.models.user import User
from app.services.blacklist import is_blacklisted


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Отсутствует Bearer-токен")

    token = authorization.split(" ", 1)[1].strip()
    payload, err = safe_decode_token(token)
    if err or not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный токен")

    if payload.get("typ") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный тип токена")

    jti = payload.get("jti")
    if jti and await is_blacklisted(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Токен отозван")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный токен")

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")

    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Адрес электронной почты не подтвержден")

    return user


def _is_admin_role(role: str | None) -> bool:
    if not role:
        return False
    r = str(role).lower()
    return r in ("admin", "superadmin")


async def require_admin(user=Depends(get_current_user)):
    if not _is_admin_role(getattr(user, "role", None)):
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return user
