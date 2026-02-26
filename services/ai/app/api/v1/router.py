from fastapi import APIRouter
from .health import router as health_router
from .callbacks import router as callbacks_router
from .tasks import router as tasks_router
from .chats import router as chats_router
from .samples import router as samples_router
from .prompts import router as prompts_router
from .admin import  router as admin_router
from .batches import router as batches_router

router = APIRouter()

router.include_router(tasks_router)
router.include_router(batches_router)
router.include_router(chats_router)
router.include_router(samples_router)
router.include_router(prompts_router)
router.include_router(admin_router)
router.include_router(callbacks_router)
router.include_router(health_router)
