import hashlib
import secrets

from app.core.config import settings
from app.services.redis_client import get_redis


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_code6() -> str:
    # 000000–999999
    return f"{secrets.randbelow(1_000_000):06d}"


def _key(kind: str, email: str) -> str:
    return f"code:{kind}:{email.strip().lower()}"


def _cooldown_key(kind: str, email: str) -> str:
    return f"cd:{kind}:{email.strip().lower()}"


def _attempts_key(kind: str, email: str) -> str:
    return f"try:{kind}:{email.strip().lower()}"


async def start_cooldown(kind: str, email: str) -> None:
    r = get_redis()
    await r.set(_cooldown_key(kind, email), "1", ex=settings.CODE_RESEND_COOLDOWN_SECONDS)


async def in_cooldown(kind: str, email: str) -> bool:
    r = get_redis()
    return (await r.get(_cooldown_key(kind, email))) is not None


async def store_code(kind: str, email: str, code: str) -> None:
    r = get_redis()
    await r.set(_key(kind, email), _hash(code), ex=settings.CODE_TTL_SECONDS)
    # обнулим счётчик попыток
    await r.delete(_attempts_key(kind, email))


async def clear_code(kind: str, email: str) -> None:
    r = get_redis()
    await r.delete(_key(kind, email))
    await r.delete(_attempts_key(kind, email))


async def verify_code(kind: str, email: str, code: str, *, consume_on_success: bool = True) -> bool:
    r = get_redis()
    k = _key(kind, email)
    stored = await r.get(k)
    if not stored:
        return False

    # ограничение попыток
    ak = _attempts_key(kind, email)
    tries = await r.incr(ak)
    # TTL на попытки = TTL кода
    await r.expire(ak, settings.CODE_TTL_SECONDS)

    if tries > settings.CODE_MAX_ATTEMPTS:
        return False

    ok = secrets.compare_digest(stored, _hash(code))
    if ok and consume_on_success:
        await clear_code(kind, email)
    return ok
