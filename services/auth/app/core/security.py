import re

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from passlib.hash import pbkdf2_sha256

from app.core.config import settings


def hash_password(password: str) -> str:
    return pbkdf2_sha256.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pbkdf2_sha256.verify(password, password_hash)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, email: str, role: str) -> dict:
    jti = uuid4().hex
    exp = _now() + timedelta(minutes=settings.JWT_ACCESS_TTL_MIN)
    payload = {
        "typ": "access",
        "sub": user_id,
        "email": email,
        "role": role,
        "jti": jti,
        "exp": int(exp.timestamp()),
        "iat": int(_now().timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)
    return {"token": token, "jti": jti, "exp": exp}


def create_refresh_token(user_id: str) -> dict:
    jti = uuid4().hex
    exp = _now() + timedelta(days=settings.JWT_REFRESH_TTL_DAYS)
    payload = {
        "typ": "refresh",
        "sub": user_id,
        "jti": jti,
        "exp": int(exp.timestamp()),
        "iat": int(_now().timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)
    return {"token": token, "jti": jti, "exp": exp}


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])


def safe_decode_token(token: str) -> tuple[dict | None, str | None]:
    try:
        return decode_token(token), None
    except ExpiredSignatureError:
        return None, "expired"
    except JWTError:
        return None, "invalid"


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Валидация пароля:
    - минимум 8 символов
    - минимум одна буква
    - минимум одна заглавная буква
    - только латиница
    """
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters"

    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"

    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"

    if not re.match(r'^[A-Za-z0-9!@#$%^&*()_+\-=\[\]{};:\'",.<>/?\\|`~]+$', password):
        return False, "Password can only contain Latin letters, numbers, and special characters"

    return True, ""
