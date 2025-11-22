from pydantic import BaseModel
from typing import List, Optional


# ---- Вопросы ----

class QuestionCreate(BaseModel):
    text: str
    answer_type: str  # "text" | "single"
    options: Optional[List[str]] = None
    correct: str


class QuestionOut(BaseModel):
    id: int
    text: str
    answer_type: str
    options: Optional[List[str]]

    class Config:
        from_attributes = True


# ---- Тесты ----

class TestCreate(BaseModel):
    title: str


class TestOut(BaseModel):
    id: int
    title: str

    class Config:
        from_attributes = True


class AddQuestionToTest(BaseModel):
    question_id: int
    points: int = 1


# ---- Прохождение ----

class StartSubmission(BaseModel):
    test_id: int


class AnswerIn(BaseModel):
    question_id: int
    given: str
