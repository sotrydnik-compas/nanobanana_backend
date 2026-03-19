import base64

import httpx

from app.core.config import settings
from app.core.logger import logger

SUPPORTED_RESOLUTIONS = ("1K", "2K", "4K")
SUPPORTED_ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")


class GeminiError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class GeminiClient:
    def __init__(self):
        self.base_url = settings.GEMINI_BASE_URL.rstrip("/")
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL
        self.proxy_url = settings.GEMINI_PROXY_URL
        self.connect_timeout = settings.GEMINI_CONNECT_TIMEOUT_SECONDS
        self.write_timeout = settings.GEMINI_WRITE_TIMEOUT_SECONDS
        self.read_timeout = settings.GEMINI_READ_TIMEOUT_SECONDS

    @property
    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.connect_timeout,
        )

    async def generate_image(
        self,
        *,
        prompt: str,
        images: list[dict[str, str]],
        resolution: str,
        aspect_ratio: str,
        google_search: bool,
    ) -> dict:
        if not self.api_key:
            raise GeminiError("GEMINI_API_KEY is not configured")

        parts: list[dict] = [{"text": prompt}]
        for image in images:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image["mime_type"],
                        "data": base64.b64encode(image["data"]).decode("ascii"),
                    }
                }
            )

        payload: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {
                    "aspectRatio": aspect_ratio,
                    "imageSize": resolution,
                },
            },
        }

        if google_search:
            payload["tools"] = [{"google_search": {}}]

        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        logger.info(
            f"[gemini-client] POST {url} model={self.model} proxy={'set' if self.proxy_url else 'unset'} "
            f"images={len(images)} resolution={resolution} aspect={aspect_ratio} google_search={google_search}"
        )

        async with httpx.AsyncClient(
            timeout=self.timeout,
            proxy=self.proxy_url,
        ) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.ReadTimeout as e:
                raise GeminiError(
                    f"Gemini read timeout after waiting {self.read_timeout}s for generation result",
                    retryable=False,
                ) from e
            except httpx.WriteTimeout as e:
                raise GeminiError(
                    f"Gemini write timeout after {self.write_timeout}s while uploading request",
                    retryable=False,
                ) from e
            except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError, httpx.PoolTimeout) as e:
                raise GeminiError(f"Gemini connection error: {e}", retryable=True) from e
            except httpx.HTTPError as e:
                raise GeminiError(f"Gemini transport error: {e}", retryable=False) from e

        logger.info(
            f"[gemini-client] response status={response.status_code} bytes={len(response.content)} "
            f"content_type={response.headers.get('Content-Type')}"
        )

        if response.status_code >= 400:
            raise GeminiError(
                f"Gemini error {response.status_code}: {response.text}",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )

        data = response.json()
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        for part in parts:
            inline_data = part.get("inlineData") or part.get("inline_data")
            if inline_data and inline_data.get("data"):
                return {
                    "image_bytes": base64.b64decode(inline_data["data"]),
                    "mime_type": inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png",
                    "raw_response": data,
                }

        raise GeminiError("Gemini response does not contain an image")
