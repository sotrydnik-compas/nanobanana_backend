import httpx


class BillingError(Exception):
    pass


class BillingNoFunds(BillingError):
    pass


class BillingClient:
    def __init__(self, base_url: str, internal_token: str):
        self.base_url = base_url.rstrip("/")
        self.internal_token = internal_token

    async def _post_form(self, path: str, data: dict) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"X-Internal-Token": self.internal_token}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, data=data, headers=headers)

        if r.status_code == 402:
            raise BillingNoFunds("Not enough requests")
        if r.status_code >= 400:
            raise BillingError(f"Billing error {r.status_code}: {r.text}")

        return r.json() if r.content else {}

    async def reserve(self, user_id: str, request_id: str, cost: int = 1):
        return await self._post_form(
            "/internal/usage/reserve",
            {"user_id": str(user_id), "request_id": request_id, "cost": str(cost)},
        )

    async def cancel(self, user_id: str, request_id: str):
        return await self._post_form(
            "/internal/usage/cancel",
            {"user_id": str(user_id), "request_id": request_id},
        )

    async def confirm(self, user_id: str, request_id: str, task_id: str):
        return await self._post_form(
            "/internal/usage/confirm",
            {"user_id": str(user_id), "request_id": request_id, "task_id": task_id},
        )

    async def fail(self, user_id: str, request_id: str, task_id: str, error: str | None = None):
        data = {"user_id": str(user_id), "request_id": request_id, "task_id": task_id}
        if error:
            data["error"] = error
        return await self._post_form("/internal/usage/fail", data)
