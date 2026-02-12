import requests
from app.core.config import settings

class NanoBananaClient:
    def __init__(self):
        self.base_url = settings.NANOBANANA_BASE_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.NANOBANANA_API_KEY}",
            "Content-Type": "application/json",
        }

    def generate_pro(self, payload: dict) -> dict:
        r = requests.post(f"{self.base_url}/generate-pro", headers=self.headers, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()

    def record_info(self, task_id: str) -> dict:
        r = requests.get(
            f"{self.base_url}/record-info",
            params={"taskId": task_id},
            headers={"Authorization": f"Bearer {settings.NANOBANANA_API_KEY}"},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
