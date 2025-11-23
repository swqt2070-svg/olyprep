from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt
from fastapi import HTTPException, status

from app.config import settings

# =========================
# Настройки JWT
# =========================

JWT_SECRET_KEY: str = settings.jwt_secret_key
JWT_ALGORITHM: str = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES: int = settings.access_token_expire_minutes


# =========================
# Хэширование паролей
# =========================

def hash_password(password: str) -> str:
    """
    Хэширует пароль через bcrypt.
    """
    if not isinstance(password, str):
        raise TypeError("password must be a string")

    password_bytes = password.encode("utf-8")
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    # bcrypt возвращает bytes, сохраняем в БД как str
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """
    Проверка пароля против хэша.
    """
    if not password_hash:
        return False

    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except ValueError:
        # Если хэш в БД кривой
        return False


# =========================
# JWT‑токены
# =========================

def create_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Создаёт JWT‑токен.

    Ожидается, что в data есть хотя бы:
      - "id"    — id пользователя (int)
      - "role"  — роль пользователя (str)
      - "email" — почта (str)

    Для совместимости:
    - используем "id" как есть
    - дублируем в стандартное поле "sub"
    """
    to_encode = data.copy()

    user_id = to_encode.get("id")
    if user_id is not None:
        to_encode.setdefault("sub", str(user_id))

    now = datetime.now(timezone.utc)

    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = now + expires_delta
    to_encode.update(
        {
            "iat": now,
            "exp": expire,
        }
    )

    encoded_jwt = jwt.encode(
        to_encode,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    return encoded_jwt


def verify_token(token: str) -> Dict[str, Any]:
    """
    Декодирует и валидирует JWT‑токен.

    Возвращает payload, с которым уже работает deps.get_current_user:
    используются data.get("id"), data.get("role"), data.get("email").
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )

        # Если в токене только sub — продублируем в id
        if payload.get("id") is None and payload.get("sub") is not None:
            try:
                payload["id"] = int(payload["sub"])
            except ValueError:
                pass

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


# =========================
# Совместимость с deps.py
# =========================

def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Обёртка для совместимости со старым кодом.
    deps.py импортирует decode_access_token — здесь просто
    прокидываем в verify_token.
    """
    return verify_token(token)
