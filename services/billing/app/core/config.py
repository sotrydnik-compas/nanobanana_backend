import os
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = os.getenv("ENV_FILE")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DEBUG: bool = False
    DATABASE_URL: str
    REDIS_URL: str

    PUBLIC_BASE_URL: str = "http://localhost"

    # JWT
    JWT_SECRET: str
    JWT_ALG: str = "HS256"

    # Внутренний токен для вызовов от ai
    INTERNAL_TOKEN: str

    # (пока заглушки под провайдера)
    PAYMENT_PROVIDER: str = "dummy"
    CURRENCY: str = "RUB"

settings = Settings()
