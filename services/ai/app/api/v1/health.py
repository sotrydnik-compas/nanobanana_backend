from fastapi import APIRouter
from app.core.config import settings
from app.services.redis_client import get_redis

router = APIRouter(tags=["health"])

@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/health/worker")
async def worker_health():
    r = get_redis()
    alive = (await r.get(settings.ARQ_HEALTH_CHECK_KEY)) is not None
    return {"status": "ok" if alive else "degraded", "worker_alive": alive}


@router.get("/ready/worker")
async def worker_ready():
    r = get_redis()
    alive = (await r.get(settings.ARQ_HEALTH_CHECK_KEY)) is not None
    return {"ready": alive}
