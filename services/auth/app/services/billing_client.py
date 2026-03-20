import httpx


class BillingError(Exception):
    pass


class BillingClient:
    def __init__(self, base_url: str, internal_token: str):
        self.base_url = base_url.rstrip("/")
        self.internal_token = internal_token

    async def grant_signup_system_plan(self, user_id: str) -> dict:
        if not self.base_url or not self.internal_token:
            raise BillingError("Billing integration is not configured")

        url = f"{self.base_url}/internal/system-plan/grant-signup"
        headers = {"X-Internal-Token": self.internal_token}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, data={"user_id": str(user_id)}, headers=headers)

        if response.status_code >= 400:
            raise BillingError(f"Billing error {response.status_code}: {response.text}")

        return response.json() if response.content else {}
