import datetime
import jwt
from passlib.hash import pbkdf2_sha256

# СЕКРЕТ ДЛЯ JWT — поменяй на свой
SECRET_KEY = "CHANGE_ME_TO_RANDOM_SECRET"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 часа


def hash_password(password: str) -> str:
    """
    Хеш пароля для хранения в БД.
    pbkdf2_sha256 — чистый Python, без ограничений, работает стабильно на любой платформе.
    """
    return pbkdf2_sha256.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Проверка пароля при логине.
    """
    return pbkdf2_sha256.verify(password, password_hash)


def create_token(payload: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    """
    Создаёт JWT токен с полями из payload + exp.
    В payload мы обычно кладём id пользователя и его роль:
      {"id": user.id, "role": user.role}
    """
    data = payload.copy()
    now = datetime.datetime.utcnow()
    expire = now + datetime.timedelta(minutes=expires_minutes)
    data.update({"iat": now, "exp": expire})
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Декодирует JWT и возвращает payload.
    Если токен протух или подпись невалидна — бросит исключение jwt.InvalidTokenError.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
