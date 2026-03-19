import argparse
import asyncio
from pathlib import Path

import httpx

from app.clients.gemini_client import (
    GeminiClient,
    GeminiError,
    SUPPORTED_ASPECT_RATIOS,
    SUPPORTED_RESOLUTIONS,
)
from app.core.config import settings


def _mask_secret(value: str, *, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def _default_output_path(mime_type: str) -> Path:
    ext = "png" if mime_type == "image/png" else "jpg"
    return Path("/tmp") / f"gemini_connectivity_check.{ext}"


async def _check_model_endpoint(client: GeminiClient) -> tuple[int, str]:
    url = f"{client.base_url}/models/{client.model}"
    headers = {"x-goog-api-key": client.api_key}
    async with httpx.AsyncClient(timeout=client.timeout, proxy=client.proxy_url) as http_client:
        response = await http_client.get(url, headers=headers)
    snippet = response.text[:300].replace("\n", " ").strip()
    return response.status_code, snippet


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test for Gemini API connectivity using current AI service config and GeminiClient."
    )
    parser.add_argument(
        "--prompt",
        default="Generate a simple test image with a single blue circle on a white background.",
        help="Prompt to send to Gemini image generation.",
    )
    parser.add_argument("--resolution", default="1K", choices=list(SUPPORTED_RESOLUTIONS))
    parser.add_argument(
        "--aspect-ratio",
        default="1:1",
        choices=list(SUPPORTED_ASPECT_RATIOS),
    )
    parser.add_argument("--google-search", action="store_true", help="Enable Gemini google_search tool.")
    parser.add_argument(
        "--skip-generate",
        action="store_true",
        help="Only check model endpoint availability, do not send generation request.",
    )
    parser.add_argument(
        "--save-output",
        action="store_true",
        help="Save generated image to /tmp for visual confirmation.",
    )
    args = parser.parse_args()

    client = GeminiClient()

    print("Gemini connectivity check")
    print(f"  base_url: {client.base_url}")
    print(f"  model: {client.model}")
    print(f"  proxy_url: {client.proxy_url or '<not set>'}")
    print(f"  api_enabled: {settings.GEMINI_API_ENABLED}")
    print(f"  connect_timeout: {client.connect_timeout}")
    print(f"  write_timeout: {client.write_timeout}")
    print(f"  read_timeout: {client.read_timeout}")
    print(f"  api_key: {_mask_secret(client.api_key)}")
    print(f"  config_public_base_url: {settings.PUBLIC_BASE_URL}")
    print()

    if not client.api_key:
        print("ERROR: GEMINI_API_KEY is not configured.")
        return 2

    try:
        status_code, snippet = await _check_model_endpoint(client)
        print(f"Model endpoint check: HTTP {status_code}")
        if snippet:
            print(f"  response snippet: {snippet}")
        if status_code >= 400:
            print("ERROR: model endpoint is not reachable with current proxy/API settings.")
            return 3
    except Exception as e:
        print(f"ERROR: failed to reach model endpoint: {e}")
        return 4

    if args.skip_generate:
        print("Generation check skipped.")
        return 0

    try:
        result = await client.generate_image(
            prompt=args.prompt,
            images=[],
            resolution=args.resolution,
            aspect_ratio=args.aspect_ratio,
            google_search=args.google_search,
        )
    except GeminiError as e:
        print(f"ERROR: generation request failed: {e} (retryable={e.retryable})")
        return 5
    except Exception as e:
        print(f"ERROR: unexpected generation failure: {e}")
        return 6

    mime_type = result["mime_type"]
    image_bytes = result["image_bytes"]
    print("Generation check: OK")
    print(f"  mime_type: {mime_type}")
    print(f"  bytes: {len(image_bytes)}")
    raw_response = result.get("raw_response") or {}
    candidates = raw_response.get("candidates") or []
    print(f"  candidates: {len(candidates)}")

    if args.save_output:
        out_path = _default_output_path(mime_type)
        out_path.write_bytes(image_bytes)
        print(f"  saved_to: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
