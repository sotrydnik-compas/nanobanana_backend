import httpx

from app.core.config import settings
from app.core.logger import logger


async def _send_unisender(to_email: str, subject: str, html_content: str) -> None:
    url = f"{settings.UNISENDER_BASE_URL.rstrip('/')}/email/send.json"
    headers = {
        "Accept": "application/json",
        "X-API-KEY": settings.UNISENDER_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "message": {
            "recipients": [{"email": to_email}],
            "body": {"html": html_content, "plaintext": ""},
            "subject": subject,
            "from_email": settings.MAIL_FROM_EMAIL,
            "from_name": settings.MAIL_FROM_NAME,
            # "reply_to": settings.MAIL_FROM_EMAIL,
            "track_links": 0,
            "track_read": 0,
        }
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        # Unisender может вернуть status/error в json при 200 — логируем на всякий случай
        if isinstance(data, dict) and data.get("error"):
            logger.error(f"[mail] Unisender error: {data}")
        else:
            logger.info(f"[mail] sent to {to_email} subject={subject}")


async def send_code_email(to_email: str, subject: str, code: str) -> None:
    html = f"<p>Ваш код: <b>{code}</b></p>"
    if not settings.MAIL_ENABLED:
        logger.info(f"[mail] disabled -> {to_email} subject={subject} code={code}")
        return

    if not settings.UNISENDER_API_KEY or not settings.MAIL_FROM_EMAIL:
        logger.error("[mail] missing UNISENDER_API_KEY or MAIL_FROM_EMAIL")
        return

    try:
        await _send_unisender(to_email, subject, html)
    except Exception as e:
        logger.exception(f"[mail] send failed: {e}")
