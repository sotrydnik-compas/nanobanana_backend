from starlette.middleware.base import BaseHTTPMiddleware

class AllowIframeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        response.headers.pop("X-Frame-Options", None)
        response.headers["Content-Security-Policy"] = "frame-ancestors *"

        return response
