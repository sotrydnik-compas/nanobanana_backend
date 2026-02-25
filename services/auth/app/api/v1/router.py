from fastapi import APIRouter
from .auth import router as auth_router
from .account import router as account_router
from .health import router as health_router
from .admin_user import router as admin_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(account_router)
router.include_router(admin_router)
router.include_router(health_router)
