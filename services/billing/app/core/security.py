from jose import jwt
from jose.exceptions import JWTError

from app.core.config import settings


def decode_access_token(token: str) -> dict:
    """
    Валидируем подпись и exp.
    Ожидаем claims: sub, jti, typ=access, role, email, exp
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
        return payload
    except JWTError as e:
        raise ValueError(str(e))
