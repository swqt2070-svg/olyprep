# app/config.py
from pydantic import BaseSettings


class Settings(BaseSettings):
    # Секретный ключ для JWT. Для прода ПЕРЕПИШИ на рандомную длинную строку
    SECRET_KEY: str = "CHANGE_ME_SUPER_SECRET_KEY"
    # Алгоритм шифрования JWT
    ALGORITHM: str = "HS256"
    # Время жизни access-токена (в минутах)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 дней

    class Config:
        # Можно переопределять значения через .env
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
