import os

try:
    # python-dotenv у тебя уже есть в requirements.txt, подгружаем .env, если он есть
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # Если dotenv по какой-то причине недоступен — просто игнорируем
    pass


class Settings:
    """
    Простой класс настроек без pydantic.

    Читает значения из переменных окружения / .env (если он есть).
    Делаем все нужные атрибуты, чтобы любой код мог использовать:
      - settings.SECRET_KEY
      - settings.JWT_SECRET / settings.JWT_SECRET_KEY
      - settings.ALGORITHM / settings.JWT_ALGORITHM
      - settings.ACCESS_TOKEN_EXPIRE_MINUTES
    """

    def __init__(self) -> None:
        # Базовый секрет
        secret = os.getenv("SECRET_KEY", "change_me_in_prod")

        # Если заданы альтернативные переменные — используем их
        jwt_secret_env = os.getenv("JWT_SECRET") or os.getenv("JWT_SECRET_KEY")
        if jwt_secret_env:
            secret = jwt_secret_env

        # Унифицируем все варианты имён секрета
        self.SECRET_KEY = secret
        self.JWT_SECRET = secret
        self.JWT_SECRET_KEY = secret

        # Алгоритм
        algo = os.getenv("JWT_ALGORITHM", "HS256")
        self.ALGORITHM = algo
        self.JWT_ALGORITHM = algo

        # Время жизни access‑токена (в минутах)
        # По умолчанию 30 дней (30 * 24 * 60 = 43200)
        self.ACCESS_TOKEN_EXPIRE_MINUTES = int(
            os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "43200")
        )

        self.jwt_secret_key = self.JWT_SECRET_KEY
        self.jwt_algorithm = self.JWT_ALGORITHM
        self.access_token_expire_minutes = self.ACCESS_TOKEN_EXPIRE_MINUTES


# Глобальный объект настроек
settings = Settings()
