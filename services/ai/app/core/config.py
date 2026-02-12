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

    GENERATE_PER_MINUTE_PER_IP: int = 10
    MAX_PROMPT_LEN: int = 800
    MAX_IMAGE_URLS: int = 8
    MAX_UPLOAD_MB: int = 10
    POLL_INTERVAL_SECONDS: int = 30

    MEDIA_DIR: str = "media"
    AUTO_CREATE_TABLES: bool = False

settings = Settings()
