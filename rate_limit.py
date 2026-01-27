import time
from collections import defaultdict, deque
from fastapi import Request, HTTPException
from core.config import settings

_hits: dict[str, deque[float]] = defaultdict(deque)

def limit_generate(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()

    window = 60.0
    limit = settings.GENERATE_PER_MINUTE_PER_IP

    q = _hits[ip]
    while q and now - q[0] > window:
        q.popleft()

    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Too Many Requests")

    q.append(now)
