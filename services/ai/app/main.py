import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.logger import logger
from app.database.session import async_engine
from app.database.base import Base

app = FastAPI(
    title="NanoBanana AI Service",
    openapi_url="/api/v1/ai/openapi.json",
    docs_url="/api/v1/ai/docs",
    redoc_url="/api/v1/ai/redoc",
)
app.include_router(v1_router, prefix="/api/v1/ai")

# media (можно убрать и оставить только nginx, но так сервис самодостаточный)
os.makedirs(settings.MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.MEDIA_DIR), name="media")

# @app.on_event("startup")
# async def startup():
#     if settings.AUTO_CREATE_TABLES:
#         async with async_engine.begin() as conn:
#             await conn.run_sync(Base.metadata.create_all)
#         logger.info("AUTO_CREATE_TABLES enabled: created tables if missing")
