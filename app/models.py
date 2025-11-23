from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


# ---------------- ENUM-Ы ----------------


class UserRole(str, Enum):
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"


class QuestionType(str, Enum):
    WRITE_TEXT = "write_text"      # написать текст
    CHOOSE_ONE = "choose_one"      # выбрать один вариант
    CHOOSE_MANY = "choose_many"    # выбрать несколько (на будущее)


# ---------------- МОДЕЛИ ПОЛЬЗОВАТЕЛЕЙ ----------------


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default=UserRole.STUDENT.value)
    created_at = Column(DateTime, default=datetime.utcnow)

    tests_created = relationship(
        "Test",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    attempts = relationship(
        "TestAttempt",
        back_populates="user",
        cascade="all, delete-orphan",
    )


# ---------------- МОДЕЛИ ВОПРОСОВ ----------------


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)

    # текст задачи
    text = Column(Text, nullable=False)

    # тип ответа: см. QuestionType
    answer_type = Column(String, nullable=False, default=QuestionType.WRITE_TEXT.value)

    # правильный ответ для текстовых задач
    correct_answer_text = Column(Text, nullable=True)

    # путь к картинке (если есть)
    image_path = Column(String, nullable=True)

    # библиотека задач
    category = Column(String, index=True, nullable=True)   # "Материаловедение"
    grade = Column(Integer, index=True, nullable=True)     # 7, 8, 9, 10, 11
    year = Column(Integer, index=True, nullable=True)      # 1819 -> 2018/19 и т.д.
    stage = Column(String, index=True, nullable=True)      # "регион", "муниц", "закл" и т.п.

    created_at = Column(DateTime, default=datetime.utcnow)

    # варианты ответа
    answers = relationship(
        "Answer",
        back_populates="question",
        cascade="all, delete-orphan",
    )

    # тесты, в которые входит вопрос
    tests = relationship(
        "Test",
        secondary="test_questions",
        back_populates="questions",
    )


class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
    )

    text = Column(String, nullable=False)
    is_correct = Column(Boolean, default=False)
    position = Column(Integer, default=0)  # порядок внутри вопроса (на будущее)

    question = relationship("Question", back_populates="answers")


# ---------------- МОДЕЛИ ТЕСТОВ ----------------


class Test(Base):
    __tablename__ = "tests"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False)          # название теста
    description = Column(Text, nullable=True)

    max_score = Column(Integer, default=0)         # суммарный балл (опционально)
    show_correct_answers = Column(
        Boolean,
        default=True,
    )  # показывать ли правильные ответы ученику после сдачи

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="tests_created")

    questions = relationship(
        "Question",
        secondary="test_questions",
        back_populates="tests",
    )

    attempts = relationship(
        "TestAttempt",
        back_populates="test",
        cascade="all, delete-orphan",
    )


class TestQuestion(Base):
    """
    Связующая таблица тест ↔ вопрос с фиксированным порядком.
    """

    __tablename__ = "test_questions"

    test_id = Column(
        Integer,
        ForeignKey("tests.id", ondelete="CASCADE"),
        primary_key=True,
    )
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    order = Column(Integer, default=0)

    test = relationship("Test", backref="test_questions")
    question = relationship("Question")


# ---------------- ПОПЫТКИ ПРОХОЖДЕНИЯ ТЕСТОВ ----------------


class TestAttempt(Base):
    """
    Одна попытка прохождения теста конкретным пользователем.
    """

    __tablename__ = "test_attempts"

    id = Column(Integer, primary_key=True, index=True)

    test_id = Column(
        Integer,
        ForeignKey("tests.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    score = Column(Integer, default=0)

    test = relationship("Test", back_populates="attempts")
    user = relationship("User", back_populates="attempts")

    answers = relationship(
        "TestAttemptAnswer",
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class TestAttemptAnswer(Base):
    """
    Ответ пользователя на один конкретный вопрос в рамках попытки.
    """

    __tablename__ = "test_attempt_answers"

    id = Column(Integer, primary_key=True, index=True)

    attempt_id = Column(
        Integer,
        ForeignKey("test_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # выбранный вариант (для задач с выбором)
    answer_id = Column(
        Integer,
        ForeignKey("answers.id", ondelete="SET NULL"),
        nullable=True,
    )

    # текст ответа (для задач "написать текст")
    answer_text = Column(Text, nullable=True)

    attempt = relationship("TestAttempt", back_populates="answers")
    question = relationship("Question")
    answer = relationship("Answer")


# ---------------- ОБРАТНАЯ СОВМЕСТИМОСТЬ ----------------
# Старый код в некоторых роутерах (например, users.py)
# всё ещё импортирует Submission. Делаем алиас,
# чтобы ничего не падало.

Submission = TestAttempt
