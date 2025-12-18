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
    - добавляем category_id и таблицу categories для иерархии категорий.
    - добавляем full_name и student_class в users.
    - создаём таблицу registration_codes.
    - добавляем поле active в users.
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
        if "category_id" not in cols:
            conn.execute(text("ALTER TABLE questions ADD COLUMN category_id INTEGER"))

        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "categories" not in tables:
            conn.execute(
                text(
                    """
                    CREATE TABLE categories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name VARCHAR NOT NULL,
                        parent_id INTEGER,
                        UNIQUE(parent_id, name)
                    )
                    """
                )
            )

        # создаём базовые категории из старого строкового поля, если его использовали
        rows = conn.execute(
            text(
                "SELECT DISTINCT TRIM(category) FROM questions "
                "WHERE category IS NOT NULL AND TRIM(category) <> ''"
            )
        ).fetchall()
        for (cat_name,) in rows:
            try:
                conn.execute(
                    text("INSERT OR IGNORE INTO categories(name, parent_id) VALUES (:name, NULL)"),
                    {"name": cat_name},
                )
            except Exception:
                pass

        # заполняем связи вопросов на основе совпадения имени
        conn.execute(
            text(
                """
                UPDATE questions
                SET category_id = (
                    SELECT id FROM categories WHERE categories.name = questions.category LIMIT 1
                )
                WHERE (category_id IS NULL) AND category IS NOT NULL AND TRIM(category) <> ''
                """
            )
        )

        # users: новые поля
        ucols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        if "full_name" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR"))
        if "student_class" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN student_class VARCHAR"))
        if "active" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN active BOOLEAN DEFAULT 1"))

        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "registration_codes" not in tables:
            conn.execute(
                text(
                    """
                    CREATE TABLE registration_codes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code VARCHAR NOT NULL UNIQUE,
                        role VARCHAR NOT NULL,
                        max_uses INTEGER NOT NULL DEFAULT 1,
                        used INTEGER NOT NULL DEFAULT 0,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
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
