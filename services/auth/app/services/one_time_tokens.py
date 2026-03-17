import secrets

from app.core.config import settings
from app.services.redis_client import get_redis


def new_token() -> str:
    return secrets.token_urlsafe(32)


async def store_email_verify(token: str, user_id: str) -> None:
    r = get_redis()
    ttl = settings.EMAIL_VERIFY_TTL_HOURS * 3600
    await r.set(f"ev:{token}", user_id, ex=ttl)


async def pop_email_verify(token: str) -> str | None:
    r = get_redis()
    key = f"ev:{token}"
    user_id = await r.get(key)
    if user_id:
        await r.delete(key)
    return user_id


async def store_password_reset(token: str, user_id: str) -> None:
    r = get_redis()
    ttl = settings.PASSWORD_RESET_TTL_MIN * 60
    await r.set(f"rp:{token}", user_id, ex=ttl)


async def pop_password_reset(token: str) -> str | None:
    r = get_redis()
    key = f"rp:{token}"
    user_id = await r.get(key)
    if user_id:
        await r.delete(key)
    return user_id


async def store_email_change(token: str, user_id: str, new_email: str) -> None:
    r = get_redis()
    ttl = settings.EMAIL_VERIFY_TTL_HOURS * 3600
    # храним user_id и новый email
    await r.set(f"ec:{token}", f"{user_id}:{new_email}", ex=ttl)


async def pop_email_change(token: str) -> tuple[str, str] | None:
    r = get_redis()
    key = f"ec:{token}"
    val = await r.get(key)
    if not val:
        return None
    await r.delete(key)
    if ":" not in val:
        return None
    user_id, new_email = val.split(":", 1)
    return user_id, new_email
