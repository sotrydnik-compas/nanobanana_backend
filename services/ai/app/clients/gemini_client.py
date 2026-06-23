import base64
import json

import httpx

from app.core.config import settings
from app.core.logger import logger

SUPPORTED_RESOLUTIONS = ("1K", "2K", "4K")
SUPPORTED_ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")


class GeminiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        user_message: str | None = None,
        reason: str = "unknown",
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.user_message = user_message
        self.reason = reason
        self.status_code = status_code


class GeminiClient:
    def __init__(self):
        self.base_url = settings.GEMINI_BASE_URL.rstrip("/")
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL
        self.fallback_model = settings.GEMINI_FALLBACK_MODEL
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

    @staticmethod
    def _extract_user_message(parts: list[dict]) -> str | None:
        texts: list[str] = []
        for part in parts:
            text = str(part.get("text") or "").strip()
            if text:
                texts.append(text)

        if not texts:
            return None

        return "\n".join(texts)

    @staticmethod
    def _format_user_error(message: str, detail: str | None = None) -> str:
        suffix = f" {detail.strip()}" if detail and detail.strip() else ""
        return f"Ошибка генерации изображения: {message}{suffix}"

    @staticmethod
    def _normalize_known_provider_message(detail_text: str | None) -> str | None:
        normalized = (detail_text or "").strip().lower()
        if not normalized:
            return None

        if "deadline expired before operation could complete." in normalized:
            return "истекло время ожидания."

        if "this model is currently experiencing high demand." in normalized:
            return "сервис перегружен. Попробуйте позже."

        if "internal error encountered." in normalized:
            return "внутренняя ошибка сервиса. Попробуйте позже."

        return None

    def _build_http_error(self, response: httpx.Response) -> GeminiError:
        raw_text = response.text
        retryable = response.status_code == 429 or response.status_code >= 500
        detail_text: str | None = None

        try:
            payload = response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            error = payload.get("error") or {}
            detail_text = str(error.get("message") or "").strip() or None

        normalized_known_message = self._normalize_known_provider_message(detail_text)
        if normalized_known_message:
            user_message = self._format_user_error(normalized_known_message)
            return GeminiError(
                f"Gemini error {response.status_code}: {raw_text}",
                retryable=retryable,
                user_message=user_message,
                reason="http_error",
                status_code=response.status_code,
            )

        if response.status_code == 429:
            user_message = self._format_user_error("превышен лимит. Попробуйте позже.")
        elif response.status_code >= 500:
            user_message = self._format_user_error("сервис временно недоступен. Попробуйте позже.")
        elif response.status_code in (401, 403):
            user_message = self._format_user_error("ошибка авторизации.")
        else:
            user_message = self._format_user_error("запрос отклонен.")

        return GeminiError(
            f"Gemini error {response.status_code}: {raw_text}",
            retryable=retryable,
            user_message=user_message,
            reason="http_error",
            status_code=response.status_code,
        )

    async def generate_image(
        self,
        *,
        prompt: str,
        images: list[dict[str, str]],
        resolution: str,
        aspect_ratio: str,
        google_search: bool,
        model: str | None = None,
    ) -> dict:
        if not self.api_key:
            raise GeminiError(
                "GEMINI_API_KEY is not configured",
                user_message=self._format_user_error("сервис не настроен."),
                reason="config_error",
            )

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

        selected_model = (model or self.model).strip() if (model or self.model) else self.model
        url = f"{self.base_url}/models/{selected_model}:generateContent"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        logger.info(
            f"[gemini-client] POST {url} model={selected_model} proxy={'set' if self.proxy_url else 'unset'} "
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
                    user_message=self._format_user_error("истекло время ожидания."),
                    reason="read_timeout",
                ) from e
            except httpx.WriteTimeout as e:
                raise GeminiError(
                    f"Gemini write timeout after {self.write_timeout}s while uploading request",
                    retryable=False,
                    user_message=self._format_user_error("не удалось отправить запрос."),
                    reason="write_timeout",
                ) from e
            except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError, httpx.PoolTimeout) as e:
                raise GeminiError(
                    f"Gemini connection error: {e}",
                    retryable=True,
                    user_message=self._format_user_error("не удалось подключиться к сервису."),
                    reason="connection_error",
                ) from e
            except httpx.HTTPError as e:
                raise GeminiError(
                    f"Gemini transport error: {e}",
                    retryable=False,
                    user_message=self._format_user_error("ошибка соединения с сервисом."),
                    reason="transport_error",
                ) from e

        logger.info(
            f"[gemini-client] response status={response.status_code} bytes={len(response.content)} "
            f"content_type={response.headers.get('Content-Type')}"
        )

        if response.status_code >= 400:
            raise self._build_http_error(response)

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

        candidate = (data.get("candidates") or [{}])[0]
        user_message = self._extract_user_message(parts)
        logger.error(
            "[gemini-client] response without image "
            f"model={selected_model} "
            f"finish_reason={candidate.get('finishReason')} "
            f"prompt_feedback={json.dumps(data.get('promptFeedback'), ensure_ascii=False)[:1000]} "
            f"text_parts={json.dumps([part.get('text') for part in parts if part.get('text')], ensure_ascii=False)[:1000]} "
            f"response_snippet={json.dumps(data, ensure_ascii=False)[:2000]}"
        )
        if user_message:
            normalized_message = self._format_user_error("модель вернула текст вместо изображения.")
        else:
            normalized_message = self._format_user_error("модель не вернула изображение.")
        raise GeminiError(
            "Gemini response does not contain an image",
            user_message=normalized_message,
            reason="no_image_response",
        )
