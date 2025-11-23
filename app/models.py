from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="student")  # 'admin' / 'teacher' / 'student'
    created_at = Column(DateTime, default=datetime.utcnow)

    submissions = relationship(
        "Submission",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    # 'text' – написать текст; 'single' – выбрать один вариант
    answer_type = Column(String, nullable=False)
    # JSON‑строка с вариантами для single‑choice
    options = Column(Text, nullable=True)
    # правильный ответ: либо текст (для text), либо индекс варианта (строкой) для single
    correct = Column(Text, nullable=True)
    # путь к картинке (если есть)
    image_path = Column(String, nullable=True)

    # метаданные библиотеки
    category = Column(String, nullable=True)   # "Дерево", "Металл", ...
    grade = Column(Integer, nullable=True)     # 7, 8, 9, 10, 11 ...
    year = Column(String, nullable=True)       # "1819", "2324" и т.п.
    stage = Column(String, nullable=True)      # "муницип", "регион", "закл" ...

    created_at = Column(DateTime, default=datetime.utcnow)

    # связи
    test_links = relationship(
        "TestQuestion",
        back_populates="question",
        cascade="all, delete-orphan",
    )
    answers = relationship(
        "Answer",
        back_populates="question",
        cascade="all, delete-orphan",
    )


class Test(Base):
    __tablename__ = "tests"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)

    questions = relationship(
        "TestQuestion",
        back_populates="test",
        cascade="all, delete-orphan",
    )
    submissions = relationship(
        "Submission",
        back_populates="test",
        cascade="all, delete-orphan",
    )


class TestQuestion(Base):
    __tablename__ = "test_questions"

    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("tests.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    points = Column(Integer, default=1, nullable=False)

    test = relationship("Test", back_populates="questions")
    question = relationship("Question", back_populates="test_links")


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("tests.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    score = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    test = relationship("Test", back_populates="submissions")
    user = relationship("User", back_populates="submissions")
    answers = relationship(
        "Answer",
        back_populates="submission",
        cascade="all, delete-orphan",
    )


class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)

    given = Column(Text, nullable=True)
    correct = Column(Boolean, default=False, nullable=False)
    points = Column(Integer, default=0, nullable=False)

    submission = relationship("Submission", back_populates="answers")
    question = relationship("Question", back_populates="answers")
