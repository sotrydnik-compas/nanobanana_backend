from app.services.redis_client import get_redis


async def is_blacklisted(jti: str) -> bool:
    r = get_redis()
    return (await r.get(f"bl:{jti}")) is not None
