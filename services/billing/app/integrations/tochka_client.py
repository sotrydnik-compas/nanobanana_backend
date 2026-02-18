import httpx


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
        url = f"{self.base_url}{self.api_prefix}/acquiring/v1.0/payments"
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, json={"Data": data}, headers=self._headers())
        if r.status_code >= 400:
            raise TochkaError(f"create_payment failed {r.status_code}: {r.text}")
        return r.json()

    async def get_payment_operation(self, operation_id: str) -> dict:
        url = f"{self.base_url}{self.api_prefix}/acquiring/v1.0/payments/{operation_id}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=self._headers())
        if r.status_code >= 400:
            raise TochkaError(f"get_payment failed {r.status_code}: {r.text}")
        return r.json()
