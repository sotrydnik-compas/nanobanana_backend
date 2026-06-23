from fastapi import Header, HTTPException, status, Depends
from app.core.security import safe_decode_token
from app.services.blacklist import is_blacklisted
from app.core.config import settings

async def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        if settings.AUTH_REQUIRED:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Отсутствует Bearer-токен")
        return {"user_id": None, "email": None, "role": None}

    token = authorization.split(" ", 1)[1].strip()
    payload, err = safe_decode_token(token)
    if err or not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный токен")

    if payload.get("typ") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный тип токена")

    user_id = payload.get("sub")
    jti = payload.get("jti")

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный токен")

    if jti and await is_blacklisted(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Токен отозван")

    return {"user_id": user_id, "email": payload.get("email"), "role": payload.get("role")}


def _is_admin_role(role: str | None) -> bool:
    if not role:
        return False
    r = str(role).lower()
    return r in ("admin", "superadmin")


async def require_admin(user=Depends(get_current_user)):
    if not _is_admin_role(user.get("role")):
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return user
