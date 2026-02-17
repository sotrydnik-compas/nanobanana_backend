from fastapi import Header, HTTPException, status, Depends
from app.core.config import settings
from app.core.security import decode_access_token
from app.services.blacklist import is_blacklisted


async def require_internal_token(x_internal_token: str | None = Header(default=None, alias="X-Internal-Token")):
    if not x_internal_token or x_internal_token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


async def get_current_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Missing jti")

    if await is_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Token revoked")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing sub")

    return {
        "user_id": str(user_id),
        "email": payload.get("email"),
        "role": payload.get("role", "user"),
        "jti": jti,
    }


def _is_admin_role(role: str | None) -> bool:
    if not role:
        return False
    r = str(role).lower()
    return r in ("admin", "superadmin")


async def require_admin(user=Depends(get_current_user)):
    if not _is_admin_role(user.get("role")):
        raise HTTPException(status_code=403, detail="Admin only")
    return user
