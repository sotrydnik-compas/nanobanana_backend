import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.logger import logger
from app.services.redis_client import get_redis

_fallback_hits: dict[str, deque[float]] = defaultdict(deque)


def _extract_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _fallback_check(key: str, *, limit: int, window: int) -> None:
    now = time.time()
    q = _fallback_hits[key]
    while q and now - q[0] > window:
        q.popleft()

    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте позже.")

    q.append(now)


async def _redis_check(key: str, *, limit: int, window: int) -> None:
    if limit <= 0:
        return

    r = get_redis()
    now = time.time()
    member = f"{now}:{uuid.uuid4().hex}"
    window_start = now - window

    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zcard(key)
    pipe.zadd(key, {member: now})
    pipe.expire(key, window)
    _, count, _, _ = await pipe.execute()

    if int(count) >= limit:
        await r.zrem(key, member)
        raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте позже.")


async def _check_limit(key: str, *, limit: int, window: int) -> None:
    if limit <= 0:
        return

    try:
        if settings.REDIS_URL:
            await _redis_check(key, limit=limit, window=window)
            return
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[rate-limit] redis check failed for key={key}: {e}; falling back to in-memory limiter")

    _fallback_check(key, limit=limit, window=window)


async def limit_generate(request: Request, *, user_id: str | None = None) -> None:
    window = settings.RATE_LIMIT_WINDOW_SECONDS
    ip = _extract_client_ip(request)

    await _check_limit(
        f"rl:generate:ip:{ip}",
        limit=settings.GENERATE_PER_MINUTE_PER_IP,
        window=window,
    )
    if user_id:
        await _check_limit(
            f"rl:generate:user:{user_id}",
            limit=settings.GENERATE_PER_MINUTE_PER_USER,
            window=window,
        )


async def limit_batch_create(request: Request, *, user_id: str | None = None) -> None:
    window = settings.RATE_LIMIT_WINDOW_SECONDS
    ip = _extract_client_ip(request)

    await _check_limit(
        f"rl:batch-create:ip:{ip}",
        limit=settings.BATCH_CREATE_PER_MINUTE_PER_IP,
        window=window,
    )
    if user_id:
        await _check_limit(
            f"rl:batch-create:user:{user_id}",
            limit=settings.BATCH_CREATE_PER_MINUTE_PER_USER,
            window=window,
        )
