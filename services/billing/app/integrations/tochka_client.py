import httpx

from app.core.config import settings
from app.core.logger import logger


class TochkaError(Exception):
    pass


class TochkaClient:
    def __init__(self, base_url: str, api_prefix: str, bearer_token: str):
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix.rstrip("/")  # "/uapi" или "/sandbox/v2"
        self.bearer_token = bearer_token

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.bearer_token}",
        }

    async def create_payment_operation(self, data: dict) -> dict:
        if not settings.PAYMENT_ENABLED:
            logger.warning("PAYMENT_ENABLED=False, пропускаем create_payment_operation")
            raise TochkaError("Payments are disabled")

        url = f"{self.base_url}{self.api_prefix}/acquiring/v1.0/payments_with_receipt"

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, json={"Data": data}, headers=self._headers())
        if r.status_code >= 400:
            error_msg = f"create_payment failed {r.status_code}: {r.text}"
            logger.error(error_msg)
            raise TochkaError(error_msg)

        return r.json()

    async def get_payment_operation(self, operation_id: str) -> dict:
        if not settings.PAYMENT_ENABLED:
            logger.warning("PAYMENT_ENABLED=False, пропускаем get_payment_operation")
            raise TochkaError("Payments are disabled")

        url = f"{self.base_url}{self.api_prefix}/acquiring/v1.0/payments/{operation_id}"

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=self._headers())
        if r.status_code >= 400:
            error_msg = f"get_payment failed {r.status_code}: {r.text}"
            logger.error(error_msg)
            raise TochkaError(error_msg)

        return r.json()
