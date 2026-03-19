import os
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = os.getenv("ENV_FILE")  # например: /repo/.env.dev (локальный запуск)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,              # None в Docker -> читаем только env vars
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DEBUG: bool = False
    DATABASE_URL: str
    REDIS_URL: str | None = None

    PUBLIC_BASE_URL: str = "http://localhost"

    NANOBANANA_BASE_URL: str = "https://api.nanobananaapi.ai/api/v1/nanobanana"
    NANOBANANA_API_KEY: str = ""

    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_API_KEY: str = "SECRET"
    GEMINI_MODEL: str = "gemini-3.1-flash-image-preview"
    GEMINI_PROXY_URL: str | None = "http://ip:port"
    GEMINI_API_ENABLED: bool = True
    GEMINI_CONNECT_TIMEOUT_SECONDS: int = 10
    GEMINI_WRITE_TIMEOUT_SECONDS: int = 300
    GEMINI_READ_TIMEOUT_SECONDS: int = 900

    GENERATE_PER_MINUTE_PER_IP: int = 10
    GENERATE_PER_MINUTE_PER_USER: int = 10
    BATCH_CREATE_PER_MINUTE_PER_IP: int = 3
    BATCH_CREATE_PER_MINUTE_PER_USER: int = 3
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    MAX_PROMPT_LEN: int = 800
    MAX_IMAGE_URLS: int = 8
    MAX_UPLOAD_MB: int = 10
    POLL_INTERVAL_SECONDS: int = 30
    TIMEOUT_SECONDS: int = 999
    ARQ_MAX_JOBS: int = 4
    ARQ_MAX_TRIES: int = 4
    ARQ_RETRY_BASE_SECONDS: int = 10
    ARQ_HEALTH_CHECK_INTERVAL_SECONDS: int = 30
    ARQ_HEALTH_CHECK_KEY: str = "nb:ai:worker:health"

    MEDIA_DIR: str = "media"
    AUTO_CREATE_TABLES: bool = False
    STORE_RESULTS: bool = False

    JWT_SECRET: str
    JWT_ALG: str = "HS256"
    AUTH_REQUIRED: bool = True

    BILLING_BASE_URL: str = ""
    BILLING_INTERNAL_TOKEN: str = ""


settings = Settings()
