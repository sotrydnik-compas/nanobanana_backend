from fastapi import FastAPI
from app.api.v1.router import router as v1_router
from app.core.logger import setup_logging

setup_logging()

app = FastAPI(
    title="NanoBanana Billing Service",
    openapi_url="/api/v1/billing/openapi.json",
    docs_url="/api/v1/billing/docs",
    redoc_url="/api/v1/billing/redoc",
)
app.include_router(v1_router, prefix="/api/v1/billing")
