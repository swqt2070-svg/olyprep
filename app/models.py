from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


# -------- Пользователь --------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # роли: admin / teacher / student
    role = Column(String, default="student")


# -------- Вопрос --------
class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    answer_type = Column(String, nullable=False)  # "text" | "single"
    options = Column(Text, nullable=True)         # JSON строка для single
    correct = Column(Text, nullable=False)        # текст или индекс


# -------- Тест --------
class Test(Base):
    __tablename__ = "tests"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)

    questions = relationship("TestQuestion", back_populates="test")


# -------- Связь тест — вопрос --------
class TestQuestion(Base):
    __tablename__ = "test_questions"

    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("tests.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    points = Column(Integer, default=1)

    test = relationship("Test", back_populates="questions")
    question = relationship("Question")


# -------- Попытка прохождения --------
class Submission(Base):
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("tests.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    score = Column(Integer, default=0)


# -------- Ответ в попытке --------
class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    given = Column(Text, nullable=False)
    correct = Column(Integer, default=0)
    points = Column(Integer, default=0)
