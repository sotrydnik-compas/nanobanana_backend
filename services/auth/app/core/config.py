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

    JWT_SECRET: str
    JWT_ALG: str = "HS256"
    JWT_ACCESS_TTL_MIN: int = 15
    JWT_REFRESH_TTL_DAYS: int = 30

    EMAIL_VERIFY_TTL_HOURS: int = 24
    PASSWORD_RESET_TTL_MIN: int = 30

    MAIL_ENABLED: bool = False
    UNISENDER_BASE_URL: str = "https://go1.unisender.ru/ru/transactional/api/v1"
    UNISENDER_API_KEY: str = ""
    MAIL_FROM_EMAIL: str = ""
    MAIL_FROM_NAME: str = "NanoBanana"

    CODE_TTL_SECONDS: int = 900  # 15 минут
    CODE_RESEND_COOLDOWN_SECONDS: int = 60
    CODE_MAX_ATTEMPTS: int = 10

settings = Settings()
