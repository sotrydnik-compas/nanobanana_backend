from fastapi import APIRouter
from .health import router as health_router
from .callbacks import router as callbacks_router
from .tasks import router as tasks_router
from .chats import router as chats_router

router = APIRouter()

router.include_router(health_router)
router.include_router(tasks_router)
router.include_router(chats_router)
router.include_router(callbacks_router)
