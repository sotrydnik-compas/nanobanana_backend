from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.core.logger import setup_logging

setup_logging()

app = FastAPI(
    title="NanoBanana Billing Service",
    openapi_url="/api/v1/billing/openapi.json",
    docs_url="/api/v1/billing/docs",
    redoc_url="/api/v1/billing/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router, prefix="/api/v1/billing")
