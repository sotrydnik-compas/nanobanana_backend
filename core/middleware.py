from starlette.middleware.base import BaseHTTPMiddleware

class AllowIframeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]

        response.headers["Content-Security-Policy"] = "frame-ancestors *"

        return response
