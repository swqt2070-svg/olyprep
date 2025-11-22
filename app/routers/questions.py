from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json

from app.deps import get_db
from app.models import Question
from app.schemas import QuestionCreate, QuestionOut

router = APIRouter(prefix="/questions", tags=["questions"])


@router.post("", response_model=QuestionOut)
def create_question(payload: QuestionCreate, db: Session = Depends(get_db)):
    if payload.answer_type not in ("text", "single"):
        raise HTTPException(400, "answer_type must be 'text' or 'single'")

    q = Question(
        text=payload.text,
        answer_type=payload.answer_type,
        options=json.dumps(payload.options) if payload.options else None,
        correct=payload.correct
    )

    db.add(q)
    db.commit()
    db.refresh(q)

    return QuestionOut(
        id=q.id,
        text=q.text,
        answer_type=q.answer_type,
        options=json.loads(q.options) if q.options else None
    )


@router.get("", response_model=list[QuestionOut])
def list_questions(db: Session = Depends(get_db)):
    rows = db.query(Question).all()
    return [
        QuestionOut(
            id=q.id,
            text=q.text,
            answer_type=q.answer_type,
            options=json.loads(q.options) if q.options else None
        )
        for q in rows
    ]


@router.get("/{qid}", response_model=QuestionOut)
def get_question(qid: int, db: Session = Depends(get_db)):
    q = db.get(Question, qid)
    if not q:
        raise HTTPException(404, "question not found")
    return QuestionOut(
        id=q.id,
        text=q.text,
        answer_type=q.answer_type,
        options=json.loads(q.options) if q.options else None
    )
