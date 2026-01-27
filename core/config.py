import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


load_dotenv()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS")
    API_KEY: str = os.getenv("API_KEY", "")
    API_KEY_HEADER: str = "X-API-Key"

    NANOBANANA_API_KEY: str = os.getenv("NANOBANANA_API_KEY")
    NANOBANANA_BASE_URL: str = "https://api.nanobananaapi.ai/api/v1/nanobanana"
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL")

    DB_URL: str = "sqlite:///./app.db"

    GENERATE_PER_MINUTE_PER_IP: int = 10
    MAX_PROMPT_LEN: int = 800
    MAX_IMAGE_URLS: int = 8
    MAX_UPLOAD_MB: int = 10
    POLL_INTERVAL_SECONDS: int = 30

settings = Settings()
