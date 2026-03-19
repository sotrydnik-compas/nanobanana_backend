import asyncio
import sys

from app.core.config import settings
from app.services.redis_client import get_redis


async def main() -> int:
    r = get_redis()
    value = await r.get(settings.ARQ_HEALTH_CHECK_KEY)
    return 0 if value is not None else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
