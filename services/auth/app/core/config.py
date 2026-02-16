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

settings = Settings()
