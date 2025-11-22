from passlib.hash import pbkdf2_sha256
import jwt
import datetime

SECRET_KEY = "CHANGE_ME_LATER"  # потом вынесем в .env
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pbkdf2_sha256.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pbkdf2_sha256.verify(password, password_hash)


def create_token(data: dict, expires_minutes: int = 1440) -> str:
    payload = data.copy()
    payload["exp"] = datetime.datetime.utcnow() + datetime.timedelta(minutes=expires_minutes)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
