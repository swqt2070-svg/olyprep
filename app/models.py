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
from sqlalchemy.orm import relationship, Mapped, synonym

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
        "TestAttempt", back_populates="user", cascade="all,delete-orphan"
    )


class AnswerType(str):
    SINGLE = "single"
    MULTI = "multi"
    TEXT = "text"
    NUMBER = "number"

class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    text: Mapped[str] = Column(Text, nullable=False)

    # ??? ??????
    answer_type: Mapped[str] = Column(
        Enum(AnswerType.SINGLE, AnswerType.MULTI, AnswerType.TEXT, AnswerType.NUMBER, name="answer_types"),
        nullable=False,
        default=AnswerType.SINGLE,
    )

    # ???????? (JSON ??? single/multi) ? ?????? ??????
    options: Mapped[Optional[str]] = Column(Text, nullable=True)
    correct: Mapped[Optional[str]] = Column(String, nullable=True)

    # ???? ? ???????? ??????? (???? ????)
    image_path: Mapped[Optional[str]] = Column(String, nullable=True)

    # ?????????? ??? "?????????? ?????"
    category: Mapped[Optional[str]] = Column(String, nullable=True, index=True)
    grade: Mapped[Optional[str]] = Column(String, nullable=True, index=True)
    year: Mapped[Optional[str]] = Column(String, nullable=True, index=True)
    stage: Mapped[Optional[str]] = Column(String, nullable=True, index=True)

    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    # ???????? ?????? (??? single/multi)
    option_items: Mapped[List["AnswerOption"]] = relationship(
        "AnswerOption",
        back_populates="question",
        cascade="all,delete-orphan",
        order_by="AnswerOption.id",
    )

    # ????? ? ???????
    test_links: Mapped[List["TestQuestion"]] = relationship(
        "TestQuestion",
        back_populates="question",
        cascade="all,delete-orphan",
    )

    @property
    def answers(self) -> List["AnswerOption"]:
        """Alias for option items to satisfy legacy code paths."""
        return self.option_items

class AnswerOption(Base):
    __tablename__ = "answer_options"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    question_id: Mapped[int] = Column(
        Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    text: Mapped[str] = Column(Text, nullable=False)
    is_correct: Mapped[bool] = Column(Boolean, default=False, nullable=False)

    # РєР°СЂС‚РёРЅРєР° РґР»СЏ РІР°СЂРёР°РЅС‚Р° РѕС‚РІРµС‚Р° (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    image_path: Mapped[Optional[str]] = Column(String, nullable=True)

    question: Mapped[Question] = relationship("Question", back_populates="option_items")


class Test(Base):
    __tablename__ = "tests"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    title: Mapped[str] = Column(String, nullable=False)
    description: Mapped[Optional[str]] = Column(Text, nullable=True)

    created_by_id: Mapped[int] = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[User]] = relationship("User", back_populates="created_tests")

    # РЅР°СЃС‚СЂРѕР№РєРё С‚РµСЃС‚Р°
    is_public: Mapped[bool] = Column(Boolean, default=False, nullable=False)
    show_answers_to_student: Mapped[bool] = Column(Boolean, default=True, nullable=False)
    max_attempts: Mapped[Optional[int]] = Column(Integer, nullable=True)

    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    # РїСЂРёРІСЏР·Р°РЅРЅС‹Рµ РІРѕРїСЂРѕСЃС‹
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

    # РїРѕСЂСЏРґРѕРє РІ С‚РµСЃС‚Рµ
    order: Mapped[int] = Column(Integer, nullable=False, default=0)

    # СЃРєРѕР»СЊРєРѕ Р±Р°Р»Р»РѕРІ Р·Р° СЌС‚РѕС‚ РІРѕРїСЂРѕСЃ
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
    user_id: Mapped[int] = Column(
        "student_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = synonym("user_id")

    started_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)

    score: Mapped[Optional[int]] = Column(Integer, nullable=True, default=0)
    max_score: Mapped[Optional[int]] = Column(Integer, nullable=True)

    test: Mapped[Test] = relationship("Test", back_populates="attempts")
    user: Mapped[User] = relationship("User", back_populates="attempts")

    answers: Mapped[List["Answer"]] = relationship(
        "Answer",
        back_populates="submission",
        cascade="all,delete-orphan",
    )


class Answer(Base):
    __tablename__ = "student_answers"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)

    submission_id: Mapped[int] = Column(
        "attempt_id",
        Integer,
        ForeignKey("test_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_id = synonym("submission_id")
    question_id: Mapped[int] = Column(
        Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    selected_answer_id: Mapped[Optional[int]] = Column(
        "selected_option_id",
        Integer,
        ForeignKey("answer_options.id", ondelete="SET NULL"),
        nullable=True,
    )
    answer_text: Mapped[Optional[str]] = Column(Text, nullable=True)
    given = synonym("answer_text")
    value = synonym("answer_text")

    correct: bool = Column(Boolean, default=False, nullable=False)
    points: int = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    submission: Mapped[TestAttempt] = relationship("TestAttempt", back_populates="answers")
    question: Mapped[Question] = relationship("Question")
    selected_option: Mapped[Optional[AnswerOption]] = relationship("AnswerOption")

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_attempt_question",
        ),
    )


Submission = TestAttempt
TestAttemptAnswer = Answer

