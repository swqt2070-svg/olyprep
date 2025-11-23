# app/quiz_models.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Берём engine из твоего database.py
from app.database import engine


class QuizBase(DeclarativeBase):
    """Отдельная декларативная база только для модулей тестов."""
    pass


class QuizTest(QuizBase):
    """
    Новый тест, который собирается из задач библиотеки.
    """
    __tablename__ = "quiz_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int] = mapped_column(Integer)  # id учителя
    show_correct_answers: Mapped[bool] = mapped_column(Boolean, default=True)
    # если False — показываем только балл и, при желании, статус по задачам
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    questions: Mapped[List["QuizTestQuestion"]] = relationship(
        back_populates="test",
        cascade="all, delete-orphan",
        order_by="QuizTestQuestion.order_index",
    )


class QuizTestQuestion(QuizBase):
    """
    Связка тест ↔ задача библиотеки.
    """
    __tablename__ = "quiz_test_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_tests.id", ondelete="CASCADE")
    )
    question_id: Mapped[int] = mapped_column(Integer)  # id из таблицы questions
    points: Mapped[int] = mapped_column(Integer, default=1)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    test: Mapped["QuizTest"] = relationship(back_populates="questions")


class QuizSubmission(QuizBase):
    """
    Попытка прохождения теста учеником.
    """
    __tablename__ = "quiz_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_tests.id", ondelete="CASCADE")
    )
    student_id: Mapped[int] = mapped_column(Integer)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    total_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    test: Mapped["QuizTest"] = relationship()
    answers: Mapped[List["QuizSubmissionAnswer"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
    )


class QuizSubmissionAnswer(QuizBase):
    """
    Ответ ученика на конкретную задачу в рамках попытки.
    """
    __tablename__ = "quiz_submission_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_submissions.id", ondelete="CASCADE")
    )
    question_id: Mapped[int] = mapped_column(Integer)

    # answer_type дублируем для удобства (text/single/multiple/…)
    answer_type: Mapped[str] = mapped_column(String(50))

    # Для текстовых — текст
    answer_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Для single/multiple — строка вида "12" или "12,15"
    selected_option_ids: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    submission: Mapped["QuizSubmission"] = relationship(
        back_populates="answers"
    )


# Создаём новые таблицы при импорте модуля
QuizBase.metadata.create_all(bind=engine)
