# app/database.py
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
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
    _ensure_legacy_columns()


def _ensure_legacy_columns() -> None:
    """
    Простейшая «миграция» для старой схемы SQLite:
    - добавляем columns options / correct в questions, если их не было;
    - переносим correct_answer_text -> correct, если новое поле пустое.
    """
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(questions)"))}
        if "options" not in cols:
            conn.execute(text("ALTER TABLE questions ADD COLUMN options TEXT"))
        if "correct" not in cols:
            conn.execute(text("ALTER TABLE questions ADD COLUMN correct VARCHAR"))
        if "correct" in cols and "correct_answer_text" in cols:
            conn.execute(
                text(
                    "UPDATE questions SET correct=correct_answer_text "
                    "WHERE (correct IS NULL OR correct='') AND correct_answer_text IS NOT NULL"
                )
            )


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
