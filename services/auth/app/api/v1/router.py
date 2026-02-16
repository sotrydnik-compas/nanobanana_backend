from fastapi import APIRouter
from .auth import router as auth_router
from .account import router as account_router
from .health import router as health_router

router = APIRouter()

router.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
router.include_router(account_router, prefix="/api/v1/account", tags=["account"])
router.include_router(health_router)
