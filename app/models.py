from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, Mapped

from .database import Base


class UserRole(str):
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    email: Mapped[str] = Column(String, unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = Column(String, nullable=False)
    role: Mapped[str] = Column(
        Enum(UserRole.ADMIN, UserRole.TEACHER, UserRole.STUDENT, name="user_roles"),
        nullable=False,
        default=UserRole.STUDENT,
    )
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    created_tests: Mapped[List["Test"]] = relationship(
        "Test", back_populates="created_by", cascade="all,delete-orphan"
    )
    attempts: Mapped[List["TestAttempt"]] = relationship(
        "TestAttempt", back_populates="student", cascade="all,delete-orphan"
    )


class AnswerType(str):
    SINGLE = "single"   # один правильный вариант
    MULTI = "multi"     # несколько правильных вариантов
    TEXT = "text"       # текстовый ответ


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    text: Mapped[str] = Column(Text, nullable=False)

    # тип ответа
    answer_type: Mapped[str] = Column(
        Enum(AnswerType.SINGLE, AnswerType.MULTI, AnswerType.TEXT, name="answer_types"),
        nullable=False,
        default=AnswerType.SINGLE,
    )

    # для текстовых задач – эталонный ответ (строка)
    correct_answer_text: Mapped[Optional[str]] = Column(Text, nullable=True)

    # путь к картинке вопроса (если есть)
    image_path: Mapped[Optional[str]] = Column(String, nullable=True)

    # метаданные для "Библиотеки задач"
    category: Mapped[Optional[str]] = Column(String, nullable=True, index=True)
    grade: Mapped[Optional[str]] = Column(String, nullable=True, index=True)
    year: Mapped[Optional[str]] = Column(String, nullable=True, index=True)
    stage: Mapped[Optional[str]] = Column(String, nullable=True, index=True)

    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    # варианты ответа (для single/multi)
    options: Mapped[List["AnswerOption"]] = relationship(
        "AnswerOption",
        back_populates="question",
        cascade="all,delete-orphan",
        order_by="AnswerOption.id",
    )

    # связи с тестами
    test_links: Mapped[List["TestQuestion"]] = relationship(
        "TestQuestion",
        back_populates="question",
        cascade="all,delete-orphan",
    )


class AnswerOption(Base):
    __tablename__ = "answer_options"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    question_id: Mapped[int] = Column(
        Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    text: Mapped[str] = Column(Text, nullable=False)
    is_correct: Mapped[bool] = Column(Boolean, default=False, nullable=False)

    # картинка для варианта ответа (опционально)
    image_path: Mapped[Optional[str]] = Column(String, nullable=True)

    question: Mapped[Question] = relationship("Question", back_populates="options")


class Test(Base):
    __tablename__ = "tests"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    title: Mapped[str] = Column(String, nullable=False)
    description: Mapped[Optional[str]] = Column(Text, nullable=True)

    created_by_id: Mapped[int] = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[User]] = relationship("User", back_populates="created_tests")

    # настройки теста
    is_public: Mapped[bool] = Column(Boolean, default=False, nullable=False)
    show_answers_to_student: Mapped[bool] = Column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    # привязанные вопросы
    questions: Mapped[List["TestQuestion"]] = relationship(
        "TestQuestion",
        back_populates="test",
        cascade="all,delete-orphan",
        order_by="TestQuestion.order",
    )

    attempts: Mapped[List["TestAttempt"]] = relationship(
        "TestAttempt",
        back_populates="test",
        cascade="all,delete-orphan",
    )


class TestQuestion(Base):
    __tablename__ = "test_questions"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    test_id: Mapped[int] = Column(
        Integer, ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = Column(
        Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # порядок в тесте
    order: Mapped[int] = Column(Integer, nullable=False, default=0)

    # сколько баллов за этот вопрос
    points: Mapped[int] = Column(Integer, nullable=False, default=1)

    test: Mapped[Test] = relationship("Test", back_populates="questions")
    question: Mapped[Question] = relationship("Question", back_populates="test_links")

    __table_args__ = (
        UniqueConstraint("test_id", "question_id", name="uq_test_question"),
    )


class TestAttempt(Base):
    __tablename__ = "test_attempts"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    test_id: Mapped[int] = Column(
        Integer, ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    started_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)

    score: Mapped[Optional[int]] = Column(Integer, nullable=True)
    max_score: Mapped[Optional[int]] = Column(Integer, nullable=True)

    test: Mapped[Test] = relationship("Test", back_populates="attempts")
    student: Mapped[User] = relationship("User", back_populates="attempts")

    answers: Mapped[List["StudentAnswer"]] = relationship(
        "StudentAnswer",
        back_populates="attempt",
        cascade="all,delete-orphan",
    )


class StudentAnswer(Base):
    __tablename__ = "student_answers"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)

    attempt_id: Mapped[int] = Column(
        Integer, ForeignKey("test_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = Column(
        Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Для одиночного выбора – id варианта
    selected_option_id: Mapped[Optional[int]] = Column(
        Integer,
        ForeignKey("answer_options.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Для множественного выбора – запишем id через запятую, типа "3,5,7"
    selected_option_ids: Mapped[Optional[str]] = Column(String, nullable=True)

    # Для текстового ответа
    answer_text: Mapped[Optional[str]] = Column(Text, nullable=True)

    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    attempt: Mapped[TestAttempt] = relationship("TestAttempt", back_populates="answers")
    question: Mapped[Question] = relationship("Question")
    selected_option: Mapped[Optional[AnswerOption]] = relationship("AnswerOption")

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_attempt_question",
        ),
    )


# старый алиас, если где‑то в коде ещё используют Submission
Submission = TestAttempt
