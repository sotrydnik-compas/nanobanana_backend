from datetime import datetime, timezone

from app.services.redis_client import get_redis

def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

async def blacklist_jti(jti: str, exp_ts: int) -> None:
    ttl = max(0, exp_ts - _now_ts())
    if ttl <= 0:
        return
    r = get_redis()
    await r.set(f"bl:{jti}", "1", ex=ttl)

async def is_blacklisted(jti: str) -> bool:
    r = get_redis()
    v = await r.get(f"bl:{jti}")
    return v is not None
