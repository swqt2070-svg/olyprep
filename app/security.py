# app/security.py

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from app.config import settings
import jwt
from passlib.hash import bcrypt

# ===== Простые настройки JWT (пока без app.config) =====

# ОБЯЗАТЕЛЬНО ПОТОМ ПОМЕНЯЙ НА НОРМАЛЬНЫЙ СЕКРЕТ и вынеси в .env
JWT_SECRET = "super-secret-key-change-me"
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 дней


# ===== Хеширование пароля =====

def hash_password(password: str) -> str:
    """
    Хеширование пароля через bcrypt.
    bcrypt учитывает только первые 72 байта, поэтому режем строку.
    """
    if not isinstance(password, str):
        password = str(password)

    trimmed = password[:72]
    return bcrypt.hash(trimmed)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Проверка пароля.
    """
    if not password or not password_hash:
        return False

    trimmed = password[:72]
    try:
        return bcrypt.verify(trimmed, password_hash)
    except Exception:
        return False


# ===== JWT‑токены =====

def create_token(user_id: int, role: str) -> str:
    """
    Создаёт access‑token для пользователя.
    В payload кладём id и role, чтобы потом доставать в deps.py.
    """
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "id": user_id,
        "role": role,
        "exp": expire,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # В PyJWT>=2 encode уже возвращает str
    return token


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Декодирует токен. Если ок — возвращает payload (dict),
    если нет — None.
    """
    if not token:
        return None

    # Если токен приходит как "Bearer xxx"
    if token.startswith("Bearer "):
        token = token[len("Bearer "):].strip()

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        # токен просрочен
        return None
    except jwt.InvalidTokenError:
        # любая другая ошибка токена
        return None
