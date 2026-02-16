from redis.asyncio import Redis
from app.core.config import settings

_redis: Redis | None = None

def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis
