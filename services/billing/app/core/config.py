import os
from typing import List
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

    # Данные провайдера
    PAYMENT_PROVIDER: str = "tochka"
    CURRENCY: str = "RUB"

    # TOCHKA API
    TOCHKA_BASE_URL: str = "https://enter.tochka.com"
    # "/sandbox/v2" или "/uapi"
    TOCHKA_API_PREFIX: str = "/sandbox/v2"

    TOCHKA_BEARER_TOKEN: str
    TOCHKA_CUSTOMER_CODE: str
    TOCHKA_MERCHANT_ID: str

    # В env удобно хранить как JSON: ["sbp","card"]
    TOCHKA_PAYMENT_MODES: List[str] = ["sbp", "card"]

    TOCHKA_REDIRECT_URL: str = "https://localhost/widget"
    TOCHKA_FAIL_REDIRECT_URL: str = "https://localhost/widget"

    # Публичный ключ OpenAPI (PEM). В env можно хранить одной строкой с \n
    TOCHKA_WEBHOOK_PUBLIC_KEY: str


settings = Settings()
