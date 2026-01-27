from starlette.middleware.base import BaseHTTPMiddleware

class AllowIframeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Разрешить встраивание в iframe
        response.headers["X-Frame-Options"] = "ALLOWALL"
        # Если где-то включён CSP — добавь frame-ancestors
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        return response
