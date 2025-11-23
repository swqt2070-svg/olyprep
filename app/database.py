# app/database.py
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Для SQLite-файла olyprep.db в корне проекта
SQLALCHEMY_DATABASE_URL = "sqlite:///./olyprep.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},  # нужно для SQLite в одном потоке
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    Стандартная fastapi-зависимость для получения сессии БД.
    Её импортируют как:
        from app.database import get_db
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    Создаёт таблицы, если их ещё нет.
    Вызывается из main.py при старте приложения.
    """
    # импортируем модели, чтобы они зарегистрировались в Base.metadata
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """
    Удобный контекстный менеджер, если где‑то понадобится
    вручную открыть/закрыть сессию.
    """
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
