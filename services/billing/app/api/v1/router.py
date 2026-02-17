from fastapi import APIRouter
from .health import router as health_router
from .plans import router as plans_router
from .me import router as me_router
from .payments import router as payments_router
from .webhooks import router as webhooks_router
from .internal_usage import router as internal_usage_router
from .admin_plans import router as admin_plans_router

router = APIRouter()

router.include_router(health_router)
router.include_router(plans_router)
router.include_router(me_router)
router.include_router(payments_router)
router.include_router(webhooks_router)
router.include_router(internal_usage_router)
router.include_router(admin_plans_router)
