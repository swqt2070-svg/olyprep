# app/security.py
import datetime as dt
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, status
from passlib.hash import bcrypt

from app.config import settings


# ------------------------
# Хэширование паролей
# ------------------------
def _truncate_password(password: str) -> str:
    """
    Passlib (bcrypt) не принимает пароль > 72 байт.
    Самый простой способ — обрезать строку до 72 символов.
    """
    if password is None:
        return ""
    if len(password) > 72:
        return password[:72]
    return password


def hash_password(password: str) -> str:
    """
    Вернёт хэш пароля для хранения в БД.
    """
    pwd = _truncate_password(password)
    return bcrypt.hash(pwd)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Проверка пароля при логине.
    """
    pwd = _truncate_password(password)
    try:
        return bcrypt.verify(pwd, password_hash)
    except ValueError:
        # Если хэш битый / несовместим — просто не пускаем
        return False


# ------------------------
# JWT токены
# ------------------------
def create_token(
    data: Dict[str, Any],
    expires_delta: Optional[dt.timedelta] = None,
) -> str:
    """
    Создание access-токена (JWT).
    В payload ОБЯЗАТЕЛЬНО передаём хотя бы id пользователя, email и role.
    """
    to_encode = data.copy()

    if expires_delta is None:
        expires_delta = dt.timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    expire = dt.datetime.utcnow() + expires_delta
    to_encode["exp"] = expire

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return encoded_jwt


def verify_token(token: str) -> Dict[str, Any]:
    """
    Декодирование токена. Возвращает payload, если всё ок.
    Бросает HTTPException(401), если токен просрочен или битый.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Токен истёк. Войдите заново.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен.",
        )
