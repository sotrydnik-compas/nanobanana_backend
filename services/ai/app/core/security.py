from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from app.core.config import settings

def safe_decode_token(token: str) -> tuple[dict | None, str | None]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
        return payload, None
    except ExpiredSignatureError:
        return None, "expired"
    except JWTError:
        return None, "invalid"
