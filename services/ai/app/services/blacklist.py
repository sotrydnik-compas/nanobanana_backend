from app.services.redis_client import get_redis

async def is_blacklisted(jti: str) -> bool:
    r = get_redis()
    v = await r.get(f"bl:{jti}")
    return v is not None
