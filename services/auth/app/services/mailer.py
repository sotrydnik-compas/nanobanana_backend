from app.core.logger import logger
from app.core.config import settings

def send_verify_email(email: str, token: str) -> None:
    link = f"{settings.PUBLIC_BASE_URL}/api/v1/auth/email/verify"
    logger.info(f"[mail] VERIFY email={email} token={token} POST {link}")

def send_reset_email(email: str, token: str) -> None:
    link = f"{settings.PUBLIC_BASE_URL}/api/v1/auth/password/reset/confirm"
    logger.info(f"[mail] RESET email={email} token={token} POST {link}")

def send_change_email(email: str, token: str) -> None:
    link = f"{settings.PUBLIC_BASE_URL}/api/v1/account/email/confirm"
    logger.info(f"[mail] CHANGE_EMAIL email={email} token={token} POST {link}")
